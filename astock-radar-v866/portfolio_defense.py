# -*- coding: utf-8 -*-
"""Evidence-driven, PIT-safe portfolio defense; never submits orders."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

from config import DATA_DIR
from daily_defense_context import DailyDefenseContext
from portfolio_evidence import PortfolioEvidenceStore
from portfolio_monitor import INTRADAY_DB, PortfolioMonitor
from runtime_freshness import quote_guard, parse_cn, market_session

DB_PATH = DATA_DIR / "portfolio_defense.db"
AUDIT_JSON = DATA_DIR / "PORTFOLIO_DEFENSE_AUDIT.json"
AUDIT_TXT = DATA_DIR / "PORTFOLIO_DEFENSE_AUDIT.txt"
RULE_VERSION = "V866_PIT_V8_V7_DEFENSE_3"
POLICY = {
    "state_confirm_rounds": 2,
    "washout_drawdown_pct": 0.025,
    "main_rise_persistence": 70.0,
    "second_strength_persistence": 60.0,
    "defense_buffer_floor_pct": 0.20,
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_defense_state(
 ts_code TEXT PRIMARY KEY, initial_stop REAL, trailing_stop REAL, dynamic_target REAL,
 highest_price REAL, current_state TEXT NOT NULL, previous_state TEXT, state_reason TEXT,
 last_price REAL, observed_at TEXT, updated_at TEXT NOT NULL, pending_state TEXT,
 pending_count INTEGER NOT NULL DEFAULT 0, state_confidence REAL, action TEXT,
 atr_proxy REAL, atr_source TEXT, break_count INTEGER NOT NULL DEFAULT 0,
 last_break_observed_at TEXT, evidence_json TEXT NOT NULL DEFAULT '{}', rule_version TEXT
);
CREATE TABLE IF NOT EXISTS portfolio_defense_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts_code TEXT NOT NULL, observed_at TEXT NOT NULL,
 from_state TEXT, to_state TEXT, action TEXT, trailing_stop REAL, dynamic_target REAL,
 evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_portfolio_defense_history_code_time ON portfolio_defense_history(ts_code, observed_at);
"""


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _number(value, default=None):
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def _price(value):
    return round(float(value), 3) if value is not None and value > 0 else None


def _board_multiple(code):
    return 2.2 if code.startswith("688") else (2.0 if code.startswith(("300", "301")) else 1.7)


class PortfolioDefenseEngine:
    """V8 R-stage protection + V7 two-round confirmation on current PIT facts."""

    def __init__(self):
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.executescript(SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(portfolio_defense_state)")}
            for name, sql_type in {
                "pending_state": "TEXT", "pending_count": "INTEGER NOT NULL DEFAULT 0",
                "state_confidence": "REAL", "action": "TEXT", "atr_proxy": "REAL",
                "atr_source": "TEXT", "break_count": "INTEGER NOT NULL DEFAULT 0",
                "last_break_observed_at": "TEXT", "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
                "rule_version": "TEXT",
            }.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE portfolio_defense_state ADD COLUMN {name} {sql_type}")

    @staticmethod
    def _quote(code):
        if not INTRADAY_DB.exists():
            return {}
        try:
            with closing(sqlite3.connect(INTRADAY_DB)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT close,open,high,low,pre_close,vwap,pct_chg,return_5m,return_15m,return_30m,"
                    "persistence_score,amount_velocity,sector_up_ratio,sector_diffusion_rank,industry,observed_at,"
                    "data_quality,is_pit_safe FROM intraday_quote WHERE ts_code=? AND close>0 "
                    "ORDER BY observed_at DESC LIMIT 1", (code,)
                ).fetchone()
            return dict(row) if row else {}
        except sqlite3.Error:
            return {}

    @staticmethod
    def _prior(code):
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM portfolio_defense_state WHERE ts_code=?", (code,)).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _atr_proxy(quote):
        """Visible fallback until a 14-day daily bar cache is connected."""
        high, low = _number(quote.get("high")), _number(quote.get("low"))
        close, previous = _number(quote.get("close")), _number(quote.get("pre_close"))
        move15 = abs(_number(quote.get("return_15m"), 0.0) or 0.0) / 100.0
        values = []
        if high and low and high >= low:
            values.append(high - low)
        if close and previous:
            values.append(abs(close - previous))
        if close and move15:
            values.append(close * move15 * 2)
        return _price(max(values)) if values else None

    @staticmethod
    def _support(quote, price):
        values = [_number(quote.get("vwap")), _number(quote.get("low"))]
        usable = [value for value in values if value is not None and 0 < value < price]
        return _price(max(usable)) if usable else None

    @staticmethod
    def _r_multiple(price, cost, initial):
        return (price - cost) / (cost - initial) if initial and cost > initial else None

    @staticmethod
    def _trailing(initial, previous, peak, cost, atr, support, r_value):
        values = [value for value in (initial, previous) if value is not None and value > 0]
        reason = "初始结构防守"
        if r_value is not None and r_value >= 1 and support:
            values.append(support); reason = "R1结构支撑保护"
        if r_value is not None and r_value >= 1.5:
            values.append(cost); reason = "R1.5成本保护"
        if r_value is not None and r_value >= 2 and atr:
            values.append(peak - 1.6 * atr); reason = "R2利润保护"
        if r_value is not None and r_value >= 3 and atr:
            values.append(peak - 1.25 * atr); reason = "R3收紧保护"
        return _price(max(value for value in values if value > 0)), reason

    @staticmethod
    def _target(cost, initial, peak, atr, r_value):
        if not initial or cost <= initial:
            return _price(peak + atr) if atr else None
        risk = cost - initial
        next_r = 1.0 if r_value is None or r_value < 1 else (1.5 if r_value < 1.5 else (2.0 if r_value < 2 else (3.0 if r_value < 3 else 4.0)))
        value = cost + risk * next_r
        if r_value is not None and r_value >= 3 and atr:
            value = max(value, peak + 1.5 * atr)
        return _price(value)

    @staticmethod
    def _proposal(quote, peak, initial, break_confirmed, previous_state, external):
        price, vwap = _number(quote.get("close")), _number(quote.get("vwap"))
        persistence = _number(quote.get("persistence_score"), 0.0) or 0.0
        ret5, ret15 = _number(quote.get("return_5m"), 0.0) or 0.0, _number(quote.get("return_15m"), 0.0) or 0.0
        sector_up, diffusion = _number(quote.get("sector_up_ratio"), 0.0) or 0.0, _number(quote.get("sector_diffusion_rank"), 0.0) or 0.0
        above_vwap = bool(price and vwap and price >= vwap)
        sector_ok = sector_up >= 0.50 or diffusion >= 50
        drawdown = price / peak - 1 if price and peak else 0.0
        event_hard = bool(external.get("event_hard"))
        flow_bearish = str(external.get("flow_bias") or "") == "NEGATIVE"
        if break_confirmed or (initial and price and price < initial):
            return "TRUE_WEAKNESS", .90, "有效跌破动态防守，且VWAP/连续分钟确认已满足"
        if event_hard and not above_vwap and ret5 <= 0:
            return "EVENT_RISK_REVIEW", .85, "官方硬事件与VWAP下方、短周期走弱同时出现，需优先人工复核"
        if drawdown <= -POLICY["washout_drawdown_pct"] and above_vwap and persistence >= 45 and sector_ok:
            return "WASHOUT", .70, "回撤存在，但仍在VWAP上方，持续性和板块扩散未同步失效"
        if previous_state == "WASHOUT" and above_vwap and ret5 > 0 and persistence >= POLICY["second_strength_persistence"] and sector_ok:
            return "SECOND_STRENGTH", .75, "洗盘后重新站回VWAP，短周期动量和持续性恢复"
        if above_vwap and ret15 >= 0 and persistence >= POLICY["main_rise_persistence"] and sector_ok:
            return "MAIN_RISE", .75, "VWAP上方、15分钟动量、持续性和板块扩散共振"
        if flow_bearish and not above_vwap and ret15 < 0:
            return "OBSERVE", .55, "盘后资金偏弱且盘中VWAP/15分钟动量走弱；需等待价格结构确认"
        return "OBSERVE", .45, "证据尚未形成一致方向，保持观察"

    @staticmethod
    def _confirm(prior, proposal, confidence, reason):
        current = str(prior.get("current_state") or "OBSERVE")
        pending = str(prior.get("pending_state") or "")
        count = int(_number(prior.get("pending_count"), 0) or 0)
        if current == "TRUE_WEAKNESS" and proposal == "MAIN_RISE":
            proposal, confidence, reason = "OBSERVE", .45, "真实转弱后必须先完成独立恢复确认，禁止一次反弹跳回主升"
        if proposal == current:
            return current, "", 0, False, True, reason
        count = count + 1 if pending == proposal else 1
        if not (proposal == "TRUE_WEAKNESS" and confidence >= .90) and count < POLICY["state_confirm_rounds"]:
            return current, proposal, count, False, False, f"{reason}｜第 {count}/{POLICY['state_confirm_rounds']} 次确认"
        return proposal, "", 0, current != proposal, True, reason

    def run_once(self):
        positions = PortfolioMonitor().snapshot().get("positions", [])
        transitions, degraded, updated, skipped = [], [], 0, []
        for position in positions:
            code, cost = str(position["ts_code"]), _number(position.get("cost_price"))
            quote = self._quote(code)
            receipt_ok, receipt_reason = quote_guard(quote)
            if not market_session() or not receipt_ok:
                skipped.append({'ts_code': code, 'reason': receipt_reason if not receipt_ok else 'OUTSIDE_SESSION'})
                continue
            if not quote or not quote.get("is_pit_safe") or (_number(quote.get("data_quality"), 0.0) or 0.0) < .75 or not cost:
                degraded.append(code); continue
            price = _number(quote.get("close"))
            if not price:
                degraded.append(code); continue
            prior, daily, external = self._prior(code), DailyDefenseContext().get(code), PortfolioEvidenceStore.latest(code)
            observed, previous_observed = parse_cn(quote.get('observed_at')), parse_cn(prior.get('observed_at'))
            if previous_observed and observed and (observed-previous_observed).total_seconds()<60:
                skipped.append({'ts_code': code, 'reason': 'NO_NEW_INDEPENDENT_MINUTE'})
                continue
            intraday_atr, intraday_support = self._atr_proxy(quote), self._support(quote, price)
            atr = _number(daily.get("atr14")) or intraday_atr
            daily_support = _number(daily.get("support20"))
            supports = [value for value in (intraday_support, daily_support) if value is not None and 0 < value < price]
            support = _price(max(supports)) if supports else None
            atr_source = "已完成日线ATR14" if _number(daily.get("atr14")) else "当日PIT波动代理，日线ATR暂不可用"
            peak = max(value for value in (_number(prior.get("highest_price")), _number(quote.get("high")), price) if value)
            initial = _number(prior.get("initial_stop"))
            if initial is None:
                candidates = [cost - _board_multiple(code) * atr] if atr else [cost * .92]
                if support: candidates.append(support)
                initial = _price(max(value for value in candidates if value > 0))
            r_value = self._r_multiple(price, cost, initial)
            trailing, stop_reason = self._trailing(initial, _number(prior.get("trailing_stop")), peak, cost, atr, support, r_value)
            buffer = max(POLICY["defense_buffer_floor_pct"], 15 * atr / price if atr else 0.0)
            below_vwap = bool(_number(quote.get("vwap")) and price < _number(quote.get("vwap")))
            effective_break = bool(trailing and price < trailing * (1 - buffer / 100) and below_vwap)
            observed_at = str(quote.get("observed_at") or "")
            break_count = int(_number(prior.get("break_count"), 0) or 0)
            if effective_break and observed_at != str(prior.get("last_break_observed_at") or ""):
                break_count += 1
            elif not effective_break:
                break_count = 0
            required = 3 if (_number(quote.get("amount_velocity"), 1.0) or 1.0) < .85 else 2
            proposal, confidence, reason = self._proposal(quote, peak, initial, break_count >= required, str(prior.get("current_state") or "OBSERVE"), external)
            state, pending, pending_count, changed, confirmed, state_reason = self._confirm(prior, proposal, confidence, reason)
            event_price_confirmed = bool(external.get("event_hard")) and (effective_break or (below_vwap and (_number(quote.get("return_15m"), 0.0) or 0.0) < 0))
            action = "DYNAMIC_DEFENSE" if break_count >= required else ("DEFENSE_WARNING" if effective_break else ("EVENT_RISK_REVIEW" if event_price_confirmed else ("RISK_REVIEW" if state == "TRUE_WEAKNESS" else "HOLD")))
            target = self._target(cost, initial, peak, atr, r_value)
            evidence = {"price":price,"cost":cost,"session_high":_number(quote.get("high")),"session_low":_number(quote.get("low")),"vwap":_number(quote.get("vwap")),"support":support,"atr_proxy":atr,"atr_source":atr_source,"daily_context_status":daily.get("status"),"daily_context_date":daily.get("asof_trade_date"),"ma20":_number(daily.get("ma20")),"ma60":_number(daily.get("ma60")),"r_multiple":r_value,"persistence":_number(quote.get("persistence_score")),"return_5m":_number(quote.get("return_5m")),"return_15m":_number(quote.get("return_15m")),"sector_up_ratio":_number(quote.get("sector_up_ratio")),"sector_diffusion_rank":_number(quote.get("sector_diffusion_rank")),"break_buffer_pct":buffer,"break_count":break_count,"break_required":required,"effective_break":effective_break,"stop_reason":stop_reason,"state_reason":state_reason,"external_evidence":{"risk_level":external.get("risk_level"),"flow_bias":external.get("flow_bias"),"event_hard":external.get("event_hard"),"summary":external.get("summary"),"observed_at":external.get("observed_at"),"status":external.get("status")},"event_price_confirmed":event_price_confirmed}
            previous_target = _number(prior.get('dynamic_target'))
            previous_price = _number(prior.get('last_price'))
            evidence.update(previous_stop=_number(prior.get('trailing_stop')), previous_target=previous_target,
                state_changed=bool(changed), state_confirmed=bool(confirmed),
                target_crossed=bool(previous_target and previous_price is not None and previous_price<previous_target<=price),
                receipt_guard=receipt_reason)
            record = (code, initial, trailing, target, peak, state, str(prior.get("current_state") or "OBSERVE"), state_reason, price, observed_at, _now(), pending or None, pending_count, confidence, action, atr, atr_source, break_count, observed_at if effective_break else None, json.dumps(evidence,ensure_ascii=False), RULE_VERSION)
            with closing(sqlite3.connect(DB_PATH)) as conn, conn:
                conn.execute("INSERT INTO portfolio_defense_state(ts_code,initial_stop,trailing_stop,dynamic_target,highest_price,current_state,previous_state,state_reason,last_price,observed_at,updated_at,pending_state,pending_count,state_confidence,action,atr_proxy,atr_source,break_count,last_break_observed_at,evidence_json,rule_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code) DO UPDATE SET initial_stop=excluded.initial_stop,trailing_stop=MAX(portfolio_defense_state.trailing_stop,excluded.trailing_stop),dynamic_target=MAX(portfolio_defense_state.dynamic_target,excluded.dynamic_target),highest_price=MAX(portfolio_defense_state.highest_price,excluded.highest_price),current_state=excluded.current_state,previous_state=excluded.previous_state,state_reason=excluded.state_reason,last_price=excluded.last_price,observed_at=excluded.observed_at,updated_at=excluded.updated_at,pending_state=excluded.pending_state,pending_count=excluded.pending_count,state_confidence=excluded.state_confidence,action=excluded.action,atr_proxy=excluded.atr_proxy,atr_source=excluded.atr_source,break_count=excluded.break_count,last_break_observed_at=excluded.last_break_observed_at,evidence_json=excluded.evidence_json,rule_version=excluded.rule_version", record)
                if changed:
                    conn.execute("INSERT INTO portfolio_defense_history(ts_code,observed_at,from_state,to_state,action,trailing_stop,dynamic_target,evidence_json) VALUES(?,?,?,?,?,?,?,?)", (code, observed_at, str(prior.get("current_state") or "OBSERVE"), state, action, trailing, target, json.dumps(evidence,ensure_ascii=False)))
            updated += 1
            if changed: transitions.append({"ts_code":code,"name":position.get("name"),"from":prior.get("current_state") or "OBSERVE","to":state,"action":action,"reason":state_reason,"observed_at":observed_at})
        audit = {"checked_at":_now(),"rule_version":RULE_VERSION,"policy":POLICY,"positions":len(positions),"updated":updated,"degraded":degraded,"transitions":transitions,"skipped":skipped}
        AUDIT_JSON.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding="utf-8")
        AUDIT_TXT.write_text("V8.6.6 持仓动态防守审计\n"+f"检查时间: {audit['checked_at']}\n持仓: {audit['positions']}｜已更新: {updated}｜数据不足: {len(degraded)}｜状态切换: {len(transitions)}\n"+"规则: PIT价格、VWAP、分钟动量、持续性、板块扩散、结构支撑、R阶段和连续有效跌破确认；官方公告与盘后资金仅在价格确认后提高风险等级，不自动交易。\n",encoding="utf-8")
        return audit


if __name__ == "__main__":
    print(json.dumps(PortfolioDefenseEngine().run_once(), ensure_ascii=False))
