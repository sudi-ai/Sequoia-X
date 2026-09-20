from __future__ import annotations

import json
import re
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import CONFIG
from .paid_enrichment import enrich_candidate
from .signal_lab import DEFAULT_DB, initialize
from .state_machine import complete_push_attempt, reserve_push_attempt
from .wework_shadow_push import send_message

CN_TZ = ZoneInfo("Asia/Shanghai")
ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT_DIR / ".v71_intraday_fusion.json"


def _hhmm(value: str, fallback: dtime) -> dtime:
    try:
        hour, minute = (int(x) for x in value.split(":", 1))
        return dtime(hour, minute)
    except Exception:
        return fallback


def _confirmation_window(now: datetime) -> bool:
    clock = now.astimezone(CN_TZ).time().replace(tzinfo=None)
    morning = _hhmm(CONFIG.intraday_morning_start, dtime(9, 35)) <= clock <= _hhmm(CONFIG.intraday_morning_end, dtime(11, 20))
    afternoon = _hhmm(CONFIG.intraday_afternoon_start, dtime(13, 5)) <= clock <= _hhmm(CONFIG.intraday_afternoon_end, dtime(14, 20))
    return morning or afternoon


def _load_state() -> dict[str, Any]:
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_state(value: Mapping[str, Any]) -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        pass


def _num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except Exception:
        return None


def _fmt(value: Any, default: str = "-") -> str:
    return default if value in (None, "") else str(value)


def _risk_cn(value: Any) -> str:
    return {"LOW": "低", "MEDIUM": "中", "HIGH": "高", "HARD_BLOCK": "严重风险",
            "UNKNOWN": "待确认", "DATA_UNAVAILABLE": "数据不可用"}.get(
                str(value or "UNKNOWN").upper(), _fmt(value, "待确认"))


def _minute_cn(value: Any) -> str:
    return {"OK": "正常", "BAD": "转弱", "UNKNOWN": "待确认",
            "DATA_UNAVAILABLE": "数据不可用"}.get(str(value or "UNKNOWN").upper(), _fmt(value, "待确认"))


def _money(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "待确认"
    if abs(number) >= 100_000_000:
        return f"{number / 100_000_000:+.2f}亿"
    return f"{number / 10_000:+.0f}万"


def _zone_bounds(value: Any) -> tuple[float | None, float | None]:
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    if len(numbers) < 2:
        return None, None
    low, high = float(numbers[0]), float(numbers[1])
    return (min(low, high), max(low, high))


def _pullback_support_check(event: Mapping[str, Any], paid: Mapping[str, Any], old: Mapping[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    """Confirm a real pullback after a prior rise; a falling price alone can never pass."""
    candidate = event.get("candidate") if isinstance(event.get("candidate"), Mapping) else {}
    deep = event.get("deep") if isinstance(event.get("deep"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    current = _num(candidate.get("price")) or _num(minute.get("last"))
    confirmed_price = _num(old.get("confirmed_price"))
    previous_peak = _num(old.get("peak_price")) or confirmed_price
    peak = max(x for x in (previous_peak, current) if x is not None) if any(x is not None for x in (previous_peak, current)) else None
    low, high = _zone_bounds(trade.get("zone") or old.get("zone"))
    defense = _num(trade.get("defense")) or _num(old.get("defense"))
    reasons: list[str] = []
    prior_rise = bool(confirmed_price and peak and peak >= confirmed_price * 1.008)
    meaningful_pullback = bool(current and peak and current <= peak * 0.994)
    in_zone = bool(current and low and high and low * 0.995 <= current <= high * 1.01)
    structure_safe = bool(current and (defense is None or current > defense))
    minute_ok = minute.get("freshness_status") == "FRESH" and minute.get("quality") == "OK" and not minute.get("blowoff_reversal")
    vwap = _num(minute.get("vwap"))
    vwap_ok = vwap is None or bool(current and current >= vwap * 0.995)
    risk_ok = str(ann.get("announcement_risk_level") or "UNKNOWN") == "LOW"
    if not prior_rise: reasons.append("首次确认后尚未出现有效上冲")
    if not meaningful_pullback: reasons.append("尚未形成可识别回踩")
    if not in_zone: reasons.append("价格未回到观察区附近")
    if not structure_safe: reasons.append("价格已触及或跌破防守位")
    if not minute_ok: reasons.append("分钟承接或数据新鲜度未通过")
    if not vwap_ok: reasons.append("价格尚未重新获得VWAP承接")
    if not risk_ok: reasons.append("公告风险不是明确低风险")
    qualified = not reasons
    count = int(old.get("pullback_count") or 0) + 1 if qualified else 0
    updated = {"peak_price": peak, "pullback_count": count, "zone": trade.get("zone") or old.get("zone"),
               "defense": defense, "pullback_last_price": current}
    return qualified and count >= 2, reasons, updated


def _build_pullback_message(event: Mapping[str, Any], paid: Mapping[str, Any], now: datetime) -> str:
    candidate = event.get("candidate") if isinstance(event.get("candidate"), Mapping) else {}
    deep = event.get("deep") if isinstance(event.get("deep"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    code = str(candidate.get("code") or "").split(".")[0].zfill(6)
    return "\n".join([
        "🟢 A股机会雷达 V7.2｜回踩承接确认（Shadow）", "",
        f"{candidate.get('name','')} {code}｜{event.get('sector') or '-'}",
        f"💰 现价 {_fmt(candidate.get('price'))}｜观察区 {_fmt(trade.get('zone'))}",
        f"📉 回踩状态：首次确认后已有上冲，本次回到观察区并连续两轮企稳",
        f"✅ 分钟 {_minute_cn(minute.get('quality'))}｜VWAP {_fmt(minute.get('vwap'),'数据不足')}｜数据时间 {_fmt(minute.get('data_time'),'待确认')}",
        f"🛡 防守 {_fmt(trade.get('defense'))}｜目标 {_fmt(trade.get('target1'))}", "",
        "👉 操作",
        "回踩承接连续两轮通过，可按原计划评估测试仓；若消息到达时已再次快速拉升，仍不追高。",
        "❌ 失效：跌破防守位、分钟转弱、板块走弱、放量破位或新增重大风险。",
        f"🕘 确认时间：{now.isoformat(timespec='seconds')}",
        "📌 不自动下单，仅用于Shadow验证。",
    ])


def _send_once(*, day: str, code: str, state: str, text: str, db_path: Path = DEFAULT_DB) -> tuple[bool, str]:
    if not CONFIG.push_enabled:
        return False, "push_disabled"
    initialize(db_path)
    reserved = reserve_push_attempt(trade_date=day, code=code, state=state,
                                    max_attempts=CONFIG.push_max_attempts, db_path=db_path)
    if not reserved.get("allowed"):
        return False, str(reserved.get("reason") or "push_not_allowed")
    ok, detail = send_message(text)
    complete_push_attempt(trade_date=day, code=code, state=state, success=ok,
                          error="" if ok else detail, db_path=db_path)
    return ok, detail


def _build_message(event: Mapping[str, Any], paid: Mapping[str, Any], *, confirmed: bool,
                   reasons: list[str], now: datetime) -> str:
    candidate = event.get("candidate") if isinstance(event.get("candidate"), Mapping) else {}
    deep = event.get("deep") if isinstance(event.get("deep"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
    force = deep.get("force") if isinstance(deep.get("force"), Mapping) else {}
    flow = deep.get("flow") if isinstance(deep.get("flow"), Mapping) else {}
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    code = str(candidate.get("code") or "").split(".")[0].zfill(6)
    fast = str(event.get("tier") or "") == "fast"
    if fast:
        title = "🟠 A股机会雷达 V7.1｜盘中突破确认" if confirmed else "🟠 A股机会雷达 V7.1｜盘中快速异动"
        action = ("快速异动连续两轮成立；位置可能偏高，仅结合观察区等待承接，禁止追涨。" if confirmed
                  else "短时量价正在加速，等待下一轮与V7实时数据复核，不直接追涨。")
        conclusion = ("盘中爆发潜力较高，但不等同于低风险买点或次日延续确认。" if confirmed
                      else "发现盘中快速拉升迹象，尚未完成突破确认。")
    else:
        title = "🟢 A股机会雷达 V7.1｜当日盘中确认" if confirmed else "🟡 A股机会雷达 V7.1｜当日盘中观察"
        action = ("当日盘中连续确认已通过；优先等回踩承接或二次转强，禁止追高。" if confirmed
                  else "当前仅观察，等待连续两轮及V7实时数据确认，不追高。")
        conclusion = ("V6.6盘中信号成立，V7实时风险与量价复核通过。" if confirmed
                      else "V6.6发现机会，V7尚未完成当日盘中确认。")
    lines = [
        title, "", f"{candidate.get('name','')} {code}｜{event.get('sector') or '-'}",
        f"💰 现价 {_fmt(candidate.get('price'))}｜今日 {_num(candidate.get('pct')) or 0:+.2f}%｜市场 {event.get('market_regime') or '-'}",
        f"⚡ 3分钟 {_num(candidate.get('move')) or 0:+.2f}%｜新增成交 {_money(candidate.get('amount_delta'))}",
        f"⭐ V6.6 {_fmt(deep.get('buy_score', candidate.get('score')))}｜个股 {_fmt(deep.get('individual_score'))}｜趋势 {_fmt(deep.get('trend_score'))}",
        f"🧠 主力 {_fmt(force.get('score'))}｜资金 {_money(flow.get('net'))}｜公告风险 {_risk_cn(ann.get('announcement_risk_level'))}",
        f"✅ 分钟 {_minute_cn(minute.get('quality'))}｜数据时间 {_fmt(minute.get('data_time'),'待确认')}",
        f"🎯 观察 {_fmt(trade.get('zone'))}｜防守 {_fmt(trade.get('defense'))}｜目标 {_fmt(trade.get('target1'))}", "",
        "👉 操作", action,
    ]
    if reasons:
        lines.append("🛡 待确认/失效：" + "；".join(reasons[:4]))
    lines += ["⚠️ 实时竞价过程未接入，本通道依据盘中实时数据降级确认。", f"📌 结论：{conclusion}"]
    return "\n".join(lines)


def _paid_checks(event: Mapping[str, Any], paid: Mapping[str, Any]) -> tuple[bool, list[str], bool]:
    candidate = event.get("candidate") if isinstance(event.get("candidate"), Mapping) else {}
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    daily = paid.get("realtime_daily") if isinstance(paid.get("realtime_daily"), Mapping) else {}
    reasons: list[str] = []
    level = str(ann.get("announcement_risk_level") or "UNKNOWN")
    hard_risk = level in {"HARD_BLOCK", "HIGH"}
    if hard_risk:
        reasons.append("公告存在高风险")
    elif level == "UNKNOWN" or str(ann.get("status") or "") in {"", "DATA_UNAVAILABLE"}:
        reasons.append("公告风险待确认")
    if minute.get("freshness_status") != "FRESH" or minute.get("quality") != "OK" or minute.get("blowoff_reversal"):
        reasons.append("实时分钟未形成有效确认")
    if str(daily.get("status") or "") not in {"OK", "SUCCESS"}:
        reasons.append("实时日线不可用")
    amount = _num(daily.get("amount")) or _num(candidate.get("amount"))
    if amount is None or amount < CONFIG.min_amount:
        reasons.append("成交额不足或缺失")
    pct = _num(candidate.get("pct"))
    if pct is None or pct > CONFIG.intraday_max_pct:
        reasons.append("涨幅过高或涨幅数据缺失")
    if candidate.get("one_word_limit_up") or candidate.get("is_limit_up_locked"):
        reasons.append("涨停封死，无法合理成交")
    return not reasons, reasons, hard_risk


def handle_v66_intraday_event(*, candidate: Mapping[str, Any], deep: Mapping[str, Any], tier: str,
                              source: str, sector_item: Mapping[str, Any] | None = None,
                              market_regime: str = "正常", now: datetime | None = None,
                              paid_override: Mapping[str, Any] | None = None,
                              db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Fuse a structured V6.6 intraday event into the V7.1 channel.

    The legacy scorer remains unchanged. A same-day confirmation needs at least
    two consecutive V6 confirmations plus fresh V7 paid/live evidence.
    """
    # V8 is a side-effect-isolated research consumer in this copied project.
    # Failure never changes the inherited V6/V7 return path.
    try:
        from v8.legacy_adapter import handle_v8_legacy_event
        handle_v8_legacy_event(candidate=candidate,deep=deep,tier=tier,source=source,
                               sector_item=sector_item or {},market_regime=market_regime,now=now)
    except Exception:
        pass
    if not (CONFIG.unified_mode and CONFIG.import_v66_intraday):
        return {"status": "DISABLED"}
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    code = str(candidate.get("code") or "").split(".")[0].zfill(6)
    if not code.strip("0"):
        return {"status": "INVALID_CODE"}
    day = now.date().isoformat()
    event = {"candidate": dict(candidate), "deep": dict(deep), "tier": tier, "source": source,
             "sector": (sector_item or {}).get("sector") or "", "market_regime": market_regime}
    state = _load_state()
    key = f"{day}|{source}|{code}"
    old = state.get(key) if isinstance(state.get(key), Mapping) else {}
    confirmation_tier = tier in {"confirm", "fast"}
    count = int(old.get("confirm_count") or 0) + 1 if confirmation_tier else 0
    state[key] = {**dict(old), "confirm_count": count, "last_seen": now.isoformat(timespec="seconds"), "tier": tier}
    state = {k: v for k, v in state.items() if k.startswith(day + "|")}
    _save_state(state)

    if not confirmation_tier or count < CONFIG.intraday_confirm_rounds or not _confirmation_window(now):
        paid = dict(paid_override or {})
        text = _build_message(event, paid, confirmed=False,
                              reasons=["等待连续确认"] if tier == "confirm" else [], now=now)
        ok, detail = _send_once(day=day, code=code, state="INTRADAY_WATCH", text=text, db_path=db_path)
        return {"status": "WATCH", "confirm_count": count, "pushed": ok, "detail": detail}

    paid = dict(paid_override or enrich_candidate(code, now=now, market_is_open=True))
    passed, reasons, hard_risk = _paid_checks(event, paid)
    if hard_risk:
        text = _build_message(event, paid, confirmed=False, reasons=reasons, now=now).replace(
            "🟡 A股机会雷达 V7.1｜当日盘中观察", "❌ A股机会雷达 V7.1｜当日候选取消")
        ok, detail = _send_once(day=day, code=code, state="INTRADAY_CANCELLED", text=text, db_path=db_path)
        return {"status": "CANCELLED", "confirm_count": count, "reasons": reasons, "pushed": ok, "detail": detail}
    if not passed:
        return {"status": "WAITING_DATA", "confirm_count": count, "reasons": reasons}
    if old.get("confirmed"):
        pullback_confirmed, pullback_reasons, pullback_update = _pullback_support_check(event, paid, old)
        state[key] = {**state[key], **pullback_update}
        _save_state(state)
        if pullback_confirmed:
            text = _build_pullback_message(event, paid, now)
            ok, detail = _send_once(day=day, code=code, state="PULLBACK_SUPPORT_CONFIRMED", text=text, db_path=db_path)
            return {"status": "PULLBACK_SUPPORT_CONFIRMED", "confirm_count": count,
                    "pullback_count": pullback_update["pullback_count"], "pushed": ok, "detail": detail}
        return {"status": "WAITING_PULLBACK", "confirm_count": count,
                "pullback_count": pullback_update["pullback_count"], "reasons": pullback_reasons}
    text = _build_message(event, paid, confirmed=True, reasons=[], now=now)
    ok, detail = _send_once(day=day, code=code, state="INTRADAY_CONFIRMED", text=text, db_path=db_path)
    state[key] = {**state[key], "confirmed": True, "confirmed_price": _num(candidate.get("price")),
                  "peak_price": _num(candidate.get("price")), "pullback_count": 0,
                  "zone": (deep.get("trade") or {}).get("zone") if isinstance(deep.get("trade"), Mapping) else None,
                  "defense": (deep.get("trade") or {}).get("defense") if isinstance(deep.get("trade"), Mapping) else None}
    _save_state(state)
    return {"status": "INTRADAY_CONFIRMED", "confirm_count": count, "pushed": ok, "detail": detail}


__all__ = ["handle_v66_intraday_event", "_confirmation_window", "_pullback_support_check", "_build_pullback_message"]
