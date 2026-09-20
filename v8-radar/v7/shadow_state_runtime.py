from __future__ import annotations

import json
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import CONFIG
from .data_layer import DATA_LAYER
from .experimental_engine import evaluate_candidate
from .paid_enrichment import enrich_candidate
from .signal_lab import DEFAULT_DB
from .state_machine import get_candidate, transition, upsert_candidate

CN_TZ = ZoneInfo("Asia/Shanghai")
_TRADE_DAY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _hhmm(text: str, fallback: dtime) -> dtime:
    try:
        h, m = [int(x) for x in text.split(":")[:2]]
        return dtime(h, m)
    except Exception:
        return fallback


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            return dict(data) if isinstance(data, Mapping) else {}
        except Exception:
            return {}
    return {}


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:
            return []
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, Mapping)]
    return []


def trade_day_status(now: datetime | None = None) -> dict[str, Any]:
    """Verify the A-share trade day before any realtime Sidecar request.

    Weekends are closed without an API call. Weekday holidays are verified with
    Tushare ``trade_cal`` and cached for 12 hours. If the calendar cannot be
    verified, the Sidecar returns UNKNOWN and skips realtime enrichment rather
    than repeatedly querying live endpoints on a possible holiday.
    """
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    if now.weekday() >= 5:
        return {"status": "CLOSED", "verified": True, "reason": "weekend"}
    day = now.strftime("%Y%m%d")
    cached = _TRADE_DAY_CACHE.get(day)
    if cached and cached[0] > time.monotonic():
        return dict(cached[1])
    try:
        raw = DATA_LAYER.relay_call(
            "trade_cal", cache_key=f"trade_cal:{day}", ttl_seconds=12 * 3600,
            exchange="SSE", start_date=day, end_date=day,
        )
        rows = _records(raw)
        if not rows:
            result = {"status": "UNKNOWN", "verified": False, "reason": "empty_trade_cal"}
            _TRADE_DAY_CACHE[day] = (time.monotonic() + 30 * 60, result)
            return dict(result)
        row = rows[-1]
        value = row.get("is_open")
        is_open = str(value).strip() in {"1", "1.0", "True", "true"} or value is True or value == 1
        result = {"status": "OPEN" if is_open else "CLOSED", "verified": True,
                  "reason": "trade_cal", "cal_date": row.get("cal_date") or day}
        _TRADE_DAY_CACHE[day] = (time.monotonic() + 12 * 3600, result)
        return dict(result)
    except Exception as exc:
        result = {"status": "UNKNOWN", "verified": False, "reason": type(exc).__name__}
        _TRADE_DAY_CACHE[day] = (time.monotonic() + 30 * 60, result)
        return dict(result)


def register_watch(*, trade_date: str, candidate: Mapping[str, Any], sector_item: Mapping[str, Any],
                   market_regime: str, paid: Mapping[str, Any] | None = None,
                   changed_at: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    evaluation = evaluate_candidate(candidate, sector_item, market_regime, paid_factors=paid)
    initial = "CANCELLED" if not evaluation["risk_gate_pass"] else "WATCH"
    reasons = list(evaluation.get("veto_reasons") or []) if initial == "CANCELLED" else []
    row = upsert_candidate(
        trade_date=trade_date, code=str(candidate.get("code") or ""), name=str(candidate.get("name") or ""),
        initial_state=initial, scores=evaluation,
        snapshot={"candidate": dict(candidate), "sector": dict(sector_item), "market_regime": market_regime},
        evidence=evaluation, reasons=reasons, changed_at=changed_at, db_path=db_path,
    )
    # WATCH/CANCELLED registration is pushed only in the V7.1 group and only when enabled.
    if CONFIG.push_enabled:
        try:
            from .wework_shadow_push import send_state_once
            send_state_once(row, initial, reasons=reasons, paid=paid or {}, db_path=db_path)
        except Exception:
            pass
    return row


def _candidate_context(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _json_obj(row.get("snapshot_json"))
    candidate = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), Mapping) else {}
    sector = snapshot.get("sector") if isinstance(snapshot.get("sector"), Mapping) else {}
    return {"candidate": dict(candidate), "sector": dict(sector), "market_regime": snapshot.get("market_regime")}


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _degraded_confirmation_checks(paid: Mapping[str, Any], context: Mapping[str, Any] | None) -> tuple[bool, list[str]]:
    """Confirmation checks used while true realtime auction data is unavailable."""
    reasons: list[str] = []
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    daily = paid.get("realtime_daily") if isinstance(paid.get("realtime_daily"), Mapping) else {}
    ctx = dict(context or {})
    candidate = ctx.get("candidate") if isinstance(ctx.get("candidate"), Mapping) else {}
    sector = ctx.get("sector") if isinstance(ctx.get("sector"), Mapping) else {}

    ann_level = str(ann.get("announcement_risk_level") or "UNKNOWN")
    if ann_level in {"HARD_BLOCK", "HIGH"}:
        reasons.append("公告存在高风险")
    elif ann_level == "UNKNOWN" or str(ann.get("status") or "") in {"DATA_UNAVAILABLE", ""}:
        reasons.append("公告风险状态未知")

    minute_ok = minute.get("freshness_status") == "FRESH" and minute.get("quality") == "OK" and not minute.get("blowoff_reversal")
    if not minute_ok:
        reasons.append("09:35后实时分钟未形成新鲜有效确认")

    daily_status = str(daily.get("status") or "")
    if daily_status not in {"OK", "SUCCESS"}:
        reasons.append("实时日线不可用")

    amount = _num(daily.get("amount"))
    if amount is None:
        amount = _num(candidate.get("amount"))
    if amount is None:
        reasons.append("流动性成交额数据缺失")
    elif amount < CONFIG.min_amount:
        reasons.append("成交额低于降级确认阈值")

    spread = _num(daily.get("bid_ask_spread_pct"))
    if spread is None:
        spread = _num(candidate.get("bid_ask_spread_pct"))
    if spread is not None and spread > CONFIG.max_spread_pct:
        reasons.append("买卖价差过大")

    sector_score = _num(sector.get("score"))
    breadth = _num(sector.get("breadth"))
    if sector_score is None or breadth is None:
        reasons.append("板块确认数据缺失")
    elif sector_score < 60 or breadth < 0.5:
        reasons.append("板块强度或扩散不足")

    return len(reasons) == 0, reasons


def decide_state(*, current_state: str, paid: Mapping[str, Any], now: datetime,
                 context: Mapping[str, Any] | None = None) -> tuple[str | None, list[str]]:
    ann = paid.get("announcement") if isinstance(paid, Mapping) else {}
    auction = paid.get("auction") if isinstance(paid, Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid, Mapping) else {}

    if isinstance(ann, Mapping) and str(ann.get("announcement_risk_level") or "") in {"HARD_BLOCK", "HIGH"}:
        return "CANCELLED", list(ann.get("announcement_risk_reasons") or ["公告高风险"])

    t = now.astimezone(CN_TZ).time().replace(tzinfo=None)
    start = _hhmm(CONFIG.confirm_start_time, dtime(9, 35))
    end = _hhmm(CONFIG.confirm_end_time, dtime(10, 10))

    auction_realtime = bool(isinstance(auction, Mapping) and auction.get("auction_realtime_available") is True)
    auction_tradeable = auction.get("auction_tradeable") if isinstance(auction, Mapping) else None
    degraded = not auction_realtime

    if current_state == "WATCH":
        if t >= dtime(9, 25) and t < start:
            if not degraded:
                if auction_tradeable is False:
                    return "CANCELLED", list(auction.get("auction_risk_reasons") or ["实时竞价不可交易"])
                if auction_tradeable is True:
                    return "PREOPEN_CHECK", []
                return None, []
            if CONFIG.realtime_auction_enabled:
                return None, []
            return "PREOPEN_CHECK", ["实时竞价数据未接入，进入降级确认模式"]
        if t >= start:
            if not degraded:
                if auction_tradeable is False:
                    return "CANCELLED", list(auction.get("auction_risk_reasons") or ["实时竞价不可交易"])
                if auction_tradeable is True:
                    return "PREOPEN_CHECK", []
            elif not CONFIG.realtime_auction_enabled:
                return "PREOPEN_CHECK", ["实时竞价数据未接入，进入降级确认模式"]
            if t > end:
                return "EXPIRED", ["确认窗口结束"]
            return None, []

    if current_state == "PREOPEN_CHECK":
        if t >= end:
            return "EXPIRED", ["确认窗口结束且确认条件未满足"]
        if t < start:
            return None, []
        if not degraded:
            if auction_tradeable is not True:
                if auction_tradeable is False:
                    return "CANCELLED", list(auction.get("auction_risk_reasons") or ["实时竞价不可交易"])
                return None, []
            minute_fresh = isinstance(minute, Mapping) and minute.get("freshness_status") == "FRESH"
            if minute_fresh and minute.get("quality") == "BAD":
                return "CANCELLED", ["开盘分钟结构恶化/冲高回落"]
            ok, reasons = _degraded_confirmation_checks(paid, context)
            return ("BUY_CONFIRMED", []) if ok else (None, reasons)

        # No true realtime auction feed: confirm only with post-open live evidence.
        if CONFIG.realtime_auction_enabled:
            return None, ["严格实时竞价模式已开启，但实时竞价接口尚未接入"]
        if isinstance(minute, Mapping) and minute.get("freshness_status") == "FRESH" and minute.get("quality") == "BAD":
            return "CANCELLED", ["开盘分钟结构恶化/冲高回落"]
        ok, reasons = _degraded_confirmation_checks(paid, context)
        if ok:
            return "BUY_CONFIRMED", ["实时竞价数据未接入，本次为降级确认"]
        return None, reasons

    return None, []


def process_candidate(*, trade_date: str, code: str, now: datetime | None = None,
                      paid_override: Mapping[str, Any] | None = None,
                      trade_day_override: str | None = None,
                      db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    row = get_candidate(trade_date=trade_date, code=code, db_path=db_path)
    if not row:
        return {"changed": False, "reason": "candidate_not_found"}
    current = str(row["state"])
    if current in {"BUY_CONFIRMED", "CANCELLED", "EXPIRED", "CLOSED"}:
        return {"changed": False, "state": current, "reason": "terminal_or_confirmed"}

    calendar = {"status": trade_day_override, "verified": True, "reason": "override"} if trade_day_override else trade_day_status(now)
    if calendar.get("status") != "OPEN":
        return {"changed": False, "state": current, "reason": "market_closed" if calendar.get("status") == "CLOSED" else "trade_calendar_unknown",
                "trade_calendar": calendar}

    paid = dict(paid_override or enrich_candidate(code, now=now, market_is_open=True))
    context = _candidate_context(row)
    next_state, reasons = decide_state(current_state=current, paid=paid, now=now, context=context)
    if not next_state:
        return {"changed": False, "state": current, "paid": paid, "reasons": reasons}
    if next_state == current:
        return {"changed": False, "state": current, "paid": paid, "reasons": reasons}
    updated = transition(trade_date=trade_date, code=code, to_state=next_state, reasons=reasons,
                         evidence={"paid": paid, "state_check_time": now.isoformat(timespec="seconds"),
                                   "trade_calendar": calendar},
                         changed_at=now.isoformat(timespec="seconds"), db_path=db_path)
    return {"changed": True, "state": updated["state"], "reasons": reasons, "paid": paid}


def run_pending_cycle(*, now: datetime | None = None, db_path: Path = DEFAULT_DB, push: bool | None = None,
                      trade_day_override: str | None = None) -> dict[str, Any]:
    """Process persisted WATCH/PREOPEN candidates without touching the V6.6 formal engine."""
    from .state_machine import list_candidates
    from .wework_shadow_push import send_state_once
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    calendar = {"status": trade_day_override, "verified": True, "reason": "override"} if trade_day_override else trade_day_status(now)
    if calendar.get("status") != "OPEN":
        return {"processed": 0, "changed": 0, "pushed": 0, "errors": 0,
                "reason": "market_closed" if calendar.get("status") == "CLOSED" else "trade_calendar_unknown",
                "trade_calendar": calendar, "details": []}
    rows = [r for r in list_candidates(db_path=db_path) if r.get("state") in {"WATCH", "PREOPEN_CHECK"}]
    changed = pushed = errors = 0
    details = []
    do_push = CONFIG.push_enabled if push is None else bool(push)
    for row in rows:
        try:
            result = process_candidate(trade_date=str(row["trade_date"]), code=str(row["code"]), now=now,
                                       trade_day_override="OPEN", db_path=db_path)
            details.append({"code": row["code"], "result": result.get("state") or row["state"],
                            "changed": result.get("changed", False), "reasons": result.get("reasons") or []})
            if result.get("changed"):
                changed += 1
                updated = get_candidate(trade_date=str(row["trade_date"]), code=str(row["code"]), db_path=db_path) or row
                if do_push:
                    ok, _ = send_state_once(updated, str(updated["state"]), reasons=result.get("reasons"),
                                            paid=result.get("paid"), db_path=db_path)
                    pushed += int(bool(ok))
        except Exception as exc:
            errors += 1
            try:
                from .runtime_log_v72 import log_event
                log_event("candidate_cycle", "候选状态处理失败", code=str(row.get("code") or ""), exc=exc, degraded=True, event="candidate_cycle")
            except Exception:
                pass
    return {"processed": len(rows), "changed": changed, "pushed": pushed, "errors": errors,
            "success": errors == 0, "trade_calendar": calendar, "details": details}
