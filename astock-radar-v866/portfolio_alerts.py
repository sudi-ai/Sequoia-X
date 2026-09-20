# -*- coding: utf-8 -*-
"""PIT-backed manual-portfolio alerts. Never trades or changes a position."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, time as clock_time, timezone

from config import DATA_DIR
from portfolio_monitor import PortfolioMonitor
from portfolio_defense import PortfolioDefenseEngine
from portfolio_evidence import PortfolioEvidenceStore
from wecom_push import send_text_result, webhook_status
from runtime_freshness import quote_guard, parse_cn, market_session


STATE_DB = DATA_DIR / "portfolio_alerts.db"
AUDIT_JSON = DATA_DIR / "PORTFOLIO_ALERT_AUDIT.json"
AUDIT_TXT = DATA_DIR / "PORTFOLIO_ALERT_AUDIT.txt"
SCHEMA = """
CREATE TABLE IF NOT EXISTS sent_portfolio_alert(
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY(trade_date, ts_code, alert_type)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _trade_date(value: str) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else datetime.now().astimezone().date().isoformat()


def _in_market_session() -> bool:
    return market_session()


def _alert_for(item: dict) -> tuple[str, str] | None:
    price = item.get("current_price")
    if price is None:
        return None
    stop, target = item.get("stop_price"), item.get("target_price")
    auto_stop, auto_target = item.get("auto_stop"), item.get("auto_target")
    pnl_pct = item.get("pnl_pct")
    if stop and price <= stop:
        return "MANUAL_STOP", "触及手工止损线"
    if target and price >= target:
        return "MANUAL_TARGET", "进入手工目标区"
    if item.get("defense_action") == "DYNAMIC_DEFENSE":
        return "DYNAMIC_DEFENSE", "连续有效跌破自动移动防守线"
    evidence = item.get('defense_evidence') or {}
    if evidence.get('target_crossed') or (auto_target and price >= auto_target):
        return "DYNAMIC_TARGET", "进入下一R阶段目标复核区"
    if item.get("defense_action") == "DEFENSE_WARNING":
        return "DEFENSE_WARNING", "首次有效跌破动态防守，等待连续分钟确认"
    if item.get("defense_action") == "EVENT_RISK_REVIEW":
        return "EVENT_RISK_REVIEW", "官方事件风险与盘中价格走弱共振，需优先人工复核"
    if item.get("defense_state") == "TRUE_WEAKNESS":
        return "TRUE_WEAKNESS", "真实转弱：防守、VWAP、持续性或短周期动量证据已确认"
    if evidence.get('state_changed') and evidence.get('state_confirmed'):
        states = {'MAIN_RISE': '趋势延续条件满足，检查移动防守',
                  'WASHOUT': '回撤承接条件满足，不代表已证实主力洗盘',
                  'SECOND_STRENGTH': '二次转强条件满足，复核持续性'}
        state = item.get('defense_state')
        if state in states:
            return state, states[state]
    return None


def _message(alerts: list[tuple[dict, str]]) -> str:
    newest = max((str(item.get("price_observed_at") or "") for item, _ in alerts), default="")
    lines = [
        "🔔 A股机会雷达 V8.6.6｜持仓监控提醒",
        f"🕘 服务器报价接收时间：{newest or '暂无带时间戳报价'}（行情源时间未核验）",
        f"📌 本次需人工复核：{len(alerts)} 只",
    ]
    for item, reason in alerts[:6]:
        pnl_pct = item.get("pnl_pct")
        pnl_text = "--" if pnl_pct is None else f"{pnl_pct * 100:+.2f}%"
        lines.append(
            f"⚠ {item.get('name')} {item.get('ts_code')}｜{reason}\n"
            f"   现价 {float(item.get('current_price') or 0):.2f}｜成本 {float(item.get('cost_price') or 0):.3f}｜浮盈亏 {pnl_text}\n"
            f"   防守 {float(item.get('auto_stop') or item.get('stop_price') or 0):.3f}｜目标 {float(item.get('auto_target') or item.get('target_price') or 0):.3f}"
            f"\n   规则依据：{item.get('defense_reason') or '用户设定价格线'}"
        )
    if len(alerts) > 6:
        lines.append(f"另有 {len(alerts) - 6} 只同类提醒，请到工作台的“我的持仓”查看。")
    lines.extend([
        "👉 操作：核对持仓计划、行情与风险承受能力；系统不自动买卖。",
        "⚠ 依据为PIT结构与确认状态；系统不自动买卖，不构成交易建议。",
    ])
    return "\n".join(lines)


class PortfolioAlertMonitor:
    def __init__(self):
        with closing(sqlite3.connect(STATE_DB)) as conn, conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def _already_sent(trade_date: str, code: str, alert_type: str) -> bool:
        with closing(sqlite3.connect(STATE_DB)) as conn:
            row = conn.execute(
                "SELECT 1 FROM sent_portfolio_alert WHERE trade_date=? AND ts_code=? AND alert_type=?",
                (trade_date, code, alert_type),
            ).fetchone()
        return row is not None

    @staticmethod
    def _mark_sent(alerts: list[tuple[dict, str, str]]) -> None:
        sent_at = now_iso()
        with closing(sqlite3.connect(STATE_DB)) as conn, conn:
            for item, alert_type, _ in alerts:
                conn.execute(
                    "INSERT OR IGNORE INTO sent_portfolio_alert VALUES(?,?,?,?,?)",
                    (_trade_date(item.get("price_observed_at", "")), item["ts_code"], alert_type,
                     str(item.get("price_observed_at") or ""), sent_at),
                )

    def run_once(self, dry_run: bool = False) -> dict:
        try:
            evidence_refresh = PortfolioEvidenceStore().refresh_all()
        except Exception as exc:
            evidence_refresh = {"status": f"ERROR:{type(exc).__name__}"}
        defense_audit = PortfolioDefenseEngine().run_once()
        snapshot = PortfolioMonitor().snapshot()
        candidates: list[tuple[dict, str, str]] = []
        blocked = []
        for item in snapshot["positions"]:
            fresh, reason = quote_guard(item)
            quote_time, defense_time = parse_cn(item.get('price_observed_at')), parse_cn(item.get('defense_observed_at'))
            aligned = bool(quote_time and defense_time and 0 <= (quote_time-defense_time).total_seconds() <= 180)
            if not fresh or not aligned:
                blocked.append({'ts_code': item['ts_code'], 'reason': reason if not fresh else 'STALE_DEFENSE'})
                continue
            found = _alert_for(item)
            if not found:
                continue
            alert_type, reason = found
            if not self._already_sent(_trade_date(item.get("price_observed_at", "")), item["ts_code"], alert_type):
                candidates.append((item, alert_type, reason))
        audit = {
            "checked_at": now_iso(), "status": "READY", "webhook": webhook_status(),
            "positions": len(snapshot["positions"]), "new_alerts": len(candidates), "sent": 0,
            "dry_run": bool(dry_run), "last_error": "", "defense_updated": defense_audit.get("updated", 0),
            "evidence_refresh": evidence_refresh,
            "blocked": blocked,
        }
        if candidates and not dry_run and _in_market_session():
            result = send_text_result(_message([(item, reason) for item, _, reason in candidates]))
            if result.get("ok"):
                self._mark_sent(candidates)
                audit["sent"] = len(candidates)
            else:
                audit["status"] = result.get("status", "ERROR")
                audit["last_error"] = result.get("error", "发送失败")
        elif candidates:
            audit["status"] = "DRY_RUN_ALERTS" if dry_run else "OUTSIDE_MARKET_SESSION"
        elif blocked:
            audit["status"] = "DATA_BLOCKED"
        AUDIT_JSON.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        AUDIT_TXT.write_text(
            "V8.6.6 持仓监控审计\n"
            f"检查时间: {audit['checked_at']}\n持仓数: {audit['positions']}\n"
            f"新提醒: {audit['new_alerts']}\n已发送: {audit['sent']}\n状态: {audit['status']}\n"
            "规则: 手工止损/目标优先；自动防守仅在连续证据确认、目标复核或高R利润保护时提醒；非交易时段不推送；不自动交易。\n",
            encoding="utf-8",
        )
        return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    monitor = PortfolioAlertMonitor()
    if args.once or args.dry_run:
        print(json.dumps(monitor.run_once(dry_run=args.dry_run), ensure_ascii=False))
        return 0
    while True:
        monitor.run_once()
        time.sleep(max(30, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
