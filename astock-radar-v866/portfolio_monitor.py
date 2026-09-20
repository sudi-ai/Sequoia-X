# -*- coding: utf-8 -*-
"""Manual portfolio ledger for the V8.6.6 workbench.

This module stores only user-entered holdings. It does not place orders,
retrieve a broker account, change strategy parameters, or emit notifications.
"""
from __future__ import annotations

import sqlite3
import json
from contextlib import closing
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path

from config import DATA_DIR
from portfolio_risk import DEFAULTS


DB_PATH = DATA_DIR / "portfolio_monitor.db"
INTRADAY_DB = DATA_DIR / "p0_intraday_pit.db"
STOCK_BASIC_CACHE = DATA_DIR / "p0_stock_basic_cache.json"
DEFENSE_DB = DATA_DIR / "portfolio_defense.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_position(
    ts_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT,
    entry_date TEXT,
    cost_price REAL NOT NULL,
    quantity REAL NOT NULL,
    current_price REAL NOT NULL,
    stop_price REAL,
    target_price REAL,
    note TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _float(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _price(value, label, required=True):
    text = str(value or "").replace(",", "").strip()
    if not text:
        if required:
            raise ValueError(f"请填写{label}")
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}格式不正确")
    if amount <= 0:
        raise ValueError(f"{label}必须大于 0")
    if amount.as_tuple().exponent < -3:
        raise ValueError(f"{label}最多保留三位小数，例如 12.345")
    return float(amount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def normalize_code(value: str) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    if len(text) == 6 and text.isdigit():
        suffix = "SH" if text.startswith(("6", "68")) else "BJ" if text.startswith(("4", "8")) else "SZ"
        return f"{text}.{suffix}"
    if len(text) == 9 and text[6] == "." and text[:6].isdigit() and text[7:] in {"SH", "SZ", "BJ"}:
        return text
    raise ValueError("股票代码请填写 6 位代码或标准代码，例如 600519.SH")


class PortfolioMonitor:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(SCHEMA)

    def _settings(self) -> dict:
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute("SELECT key,value FROM portfolio_settings").fetchall()
        return {str(key): str(value) for key, value in rows}

    def save_settings(self, nav, daily_pnl_pct=0.0) -> None:
        nav_value = _float(nav)
        if nav_value < 0:
            raise ValueError("账户总资产不能为负数")
        timestamp = _now()
        values = {"nav": f"{nav_value:.2f}", "daily_pnl_pct": f"{_float(daily_pnl_pct):.6f}"}
        with closing(sqlite3.connect(self.path)) as conn, conn:
            for key, value in values.items():
                conn.execute(
                    "INSERT INTO portfolio_settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (key, value, timestamp),
                )

    @staticmethod
    def _latest_local_quote(code: str) -> dict:
        """Read the newest persisted PIT quote; never call a provider here."""
        try:
            if not INTRADAY_DB.exists():
                return {}
            with closing(sqlite3.connect(INTRADAY_DB)) as conn:
                row = conn.execute(
                    "SELECT close, observed_at, name, industry, data_quality, is_pit_safe "
                    "FROM intraday_quote WHERE ts_code=? AND close > 0 "
                    "ORDER BY observed_at DESC LIMIT 1", (code,)
                ).fetchone()
            if row:
                return {"price": float(row[0]), "observed_at": str(row[1] or ""),
                        "name": str(row[2] or "").strip(), "sector": str(row[3] or "").strip(),
                        "data_quality": _float(row[4]), "is_pit_safe": bool(row[5]),
                        "source": "本地盘中PIT快照"}
        except (sqlite3.Error, TypeError, ValueError):
            pass
        return {}

    def lookup_security(self, value: str) -> dict:
        """Resolve a code to a listed-stock name through the guarded data hub.

        The lookup is informational only. It never submits an order and returns an
        empty name rather than inventing one when the paid source is unavailable.
        """
        code = normalize_code(value)
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute(
                "SELECT name,sector FROM portfolio_position WHERE ts_code=?", (code,)
            ).fetchone()
        saved_name, saved_sector = (str(row[0]).strip(), str(row[1] or "").strip()) if row else ("", "")
        # The intraday collector already stores the full-market security master
        # alongside each PIT quote.  Reuse it before asking a paid endpoint so
        # a temporary API failure never prevents manual position entry.
        quote = self._latest_local_quote(code)
        if quote:
            return {"ts_code": code, "name": quote.get("name") or saved_name,
                    "sector": quote.get("sector") or saved_sector, "source": quote["source"],
                    "last_price": quote["price"], "price_observed_at": quote["observed_at"],
                    "data_quality": quote["data_quality"], "is_pit_safe": quote["is_pit_safe"]}
        if saved_name:
            return {"ts_code": code, "name": saved_name, "sector": saved_sector, "source": "持仓记录"}
        # The initial stock-basic cache is also local real data.  Its schema
        # changed across collector versions, so accept both a row list and a
        # code-keyed mapping without making assumptions about optional fields.
        try:
            cached = json.loads(STOCK_BASIC_CACHE.read_text(encoding="utf-8")) if STOCK_BASIC_CACHE.exists() else {}
            records = cached.values() if isinstance(cached, dict) else cached
            for record in records:
                if not isinstance(record, dict):
                    continue
                record_code = str(record.get("ts_code") or record.get("code") or "").upper().strip()
                if record_code == code:
                    name = str(record.get("name") or "").strip()
                    if name:
                        return {"ts_code": code, "name": name, "sector": str(record.get("industry") or record.get("sector") or "").strip(), "source": "本地股票基础缓存"}
        except (OSError, ValueError, TypeError):
            pass
        try:
            from paid_data_hub import DataHub

            frame = DataHub().fetch_interface(
                "stock_basic",
                {
                    "ts_code": code,
                    "exchange": "",
                    "list_status": "L",
                    "fields": "ts_code,symbol,name,industry,market,list_date",
                },
            )
            if not frame.empty:
                record = frame.iloc[0]
                return {
                    "ts_code": str(record.get("ts_code") or code),
                    "name": str(record.get("name") or "").strip(),
                    "sector": str(record.get("industry") or "").strip(),
                    "source": str(record.get("source") or "付费数据"),
                }
        except Exception:
            pass
        return {"ts_code": code, "name": "", "sector": "", "source": "数据源未返回"}

    def save_position(self, values: dict) -> str:
        code = normalize_code(values.get("ts_code", ""))
        lookup = self.lookup_security(code)
        name = str(values.get("name", "")).strip() or lookup.get("name", "")
        quantity = _float(values.get("quantity"))
        cost_price = _price(values.get("cost_price"), "买入价")
        current_price = _price(values.get("current_price"), "当前价", required=False) or cost_price
        if not name:
            raise ValueError("未能识别股票名称，请检查代码或确认数据源已连接")
        if quantity <= 0 or cost_price <= 0 or current_price <= 0:
            raise ValueError("买入价、数量和当前价必须大于 0")
        stop_price = _price(values.get("stop_price"), "止损价", required=False)
        target_price = _price(values.get("target_price"), "目标价", required=False)
        row = (
            code, name, str(values.get("sector", "")).strip() or lookup.get("sector", ""), str(values.get("entry_date", "")).strip(),
            cost_price, quantity, current_price, stop_price, target_price,
            str(values.get("note", "")).strip(), _now(),
        )
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO portfolio_position(ts_code,name,sector,entry_date,cost_price,quantity,current_price,stop_price,target_price,note,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code) DO UPDATE SET "
                "name=excluded.name,sector=excluded.sector,entry_date=excluded.entry_date,cost_price=excluded.cost_price,"
                "quantity=excluded.quantity,current_price=excluded.current_price,stop_price=excluded.stop_price,"
                "target_price=excluded.target_price,note=excluded.note,updated_at=excluded.updated_at",
                row,
            )
        return code

    def delete_position(self, value: str) -> None:
        code = normalize_code(value)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("DELETE FROM portfolio_position WHERE ts_code=?", (code,))

    def position(self, value: str) -> dict | None:
        try:
            code = normalize_code(value)
        except ValueError:
            return None
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM portfolio_position WHERE ts_code=?", (code,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _defense(code: str) -> dict:
        try:
            if not DEFENSE_DB.exists():
                return {}
            with closing(sqlite3.connect(DEFENSE_DB)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM portfolio_defense_state WHERE ts_code=?", (code,)).fetchone()
            return dict(row) if row else {}
        except sqlite3.Error:
            return {}

    def snapshot(self) -> dict:
        settings = self._settings()
        nav = _float(settings.get("nav"))
        daily_pnl_pct = _float(settings.get("daily_pnl_pct"))
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            records = [dict(row) for row in conn.execute("SELECT * FROM portfolio_position ORDER BY updated_at DESC, ts_code")]
        sector_values: dict[str, float] = {}
        positions = []
        for row in records:
            quote = self._latest_local_quote(row["ts_code"])
            defense = self._defense(row["ts_code"])
            # Never present the saved cost price as a live market quote.  If no
            # persisted quote exists, leave valuation unknown rather than faking
            # zero P&L.
            current_price = quote.get("price") if quote else None
            market_value = current_price * row["quantity"] if current_price is not None else None
            cost_value = row["cost_price"] * row["quantity"]
            pnl = market_value - cost_value if market_value is not None else None
            pnl_pct = pnl / cost_value if pnl is not None and cost_value else None
            allocation = market_value / nav if market_value is not None and nav else None
            sector = row.get("sector") or "未分类"
            if market_value is not None:
                sector_values[sector] = sector_values.get(sector, 0.0) + market_value
            stop = row.get("stop_price")
            target = row.get("target_price")
            auto_stop = _float(defense.get("trailing_stop")) or None
            auto_target = _float(defense.get("dynamic_target")) or None
            defense_state = str(defense.get("current_state") or "")
            if current_price is None:
                status = "等待实时价格"
            elif stop and current_price <= stop:
                status = "已触及止损线"
            elif target and current_price >= target:
                status = "已进入目标区"
            elif auto_stop and current_price <= auto_stop:
                status = "动态防守触及"
            elif defense_state == "MAIN_RISE":
                status = "主升延续"
            elif defense_state == "WASHOUT":
                status = "健康洗盘"
            elif defense_state == "SECOND_STRENGTH":
                status = "二次转强"
            elif defense_state == "TRUE_WEAKNESS":
                status = "真实转弱"
            elif pnl_pct is not None and pnl_pct <= -0.08:
                status = "亏损风险预警"
            elif pnl_pct is not None and pnl_pct <= -0.05:
                status = "亏损关注"
            elif pnl_pct is not None and pnl_pct >= 0.10:
                status = "盈利复核"
            elif not stop or not target:
                status = "持有中｜计划待补充"
            else:
                status = "持有中"
            active_stop = stop or auto_stop
            stop_risk = max(current_price - (active_stop or current_price), 0.0) * row["quantity"] if current_price is not None else 0.0
            try:
                evidence = json.loads(str(defense.get("evidence_json") or "{}"))
            except (TypeError, ValueError):
                evidence = {}
            positions.append({
                **row, "current_price": current_price, "price_source": quote.get("source") if quote else "暂无真实报价",
                "price_observed_at": quote.get("observed_at") if quote else "", "market_value": market_value,
                "data_quality": quote.get("data_quality") if quote else None,
                "is_pit_safe": quote.get("is_pit_safe") if quote else False,
                "cost_value": cost_value, "pnl": pnl, "pnl_pct": pnl_pct, "allocation": allocation,
                "status": status, "stop_risk": stop_risk, "auto_stop": auto_stop,
                "auto_target": auto_target, "defense_state": defense_state,
                "defense_reason": str(defense.get("state_reason") or ""),
                "defense_observed_at": str(defense.get("observed_at") or ""),
                "defense_action": str(defense.get("action") or "HOLD"),
                "defense_confidence": _float(defense.get("state_confidence")),
                "defense_pending_state": str(defense.get("pending_state") or ""),
                "defense_pending_count": int(_float(defense.get("pending_count"))),
                "defense_evidence": evidence,
            })
        total_value = sum(item["market_value"] or 0.0 for item in positions)
        exposure = total_value / nav if nav else None
        sector_exposure = {key: value / nav for key, value in sector_values.items()} if nav else {}
        risks = []
        if nav <= 0:
            risks.append("请先填写账户总资产，才能计算仓位占比和集中度")
        else:
            if exposure and exposure > DEFAULTS["max_total_exposure"]:
                risks.append("总仓位超过默认 80% 上限")
            if daily_pnl_pct <= -DEFAULTS["max_daily_loss_pct"]:
                risks.append("达到默认当日亏损保护线")
            for item in positions:
                if item["allocation"] and item["allocation"] > DEFAULTS["max_position_pct"]:
                    risks.append(f"{item['name']} 单票仓位超过默认 15% 上限")
            for sector, ratio in sector_exposure.items():
                if ratio > DEFAULTS["max_sector_pct"]:
                    risks.append(f"{sector} 板块集中度超过默认 35% 上限")
        return {
            "settings": {"nav": nav, "daily_pnl_pct": daily_pnl_pct}, "positions": positions,
            "total_value": total_value, "exposure": exposure, "sector_exposure": sector_exposure,
            "estimated_stop_risk": sum(item["stop_risk"] for item in positions), "risks": risks,
            "limits": dict(DEFAULTS),
        }
