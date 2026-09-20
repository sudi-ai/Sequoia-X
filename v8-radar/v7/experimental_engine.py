from __future__ import annotations

from typing import Any, Mapping

ENGINE_VERSION = "V7.1-shadow-enhanced"
MIN_TURNOVER_AMOUNT_YUAN = 50_000_000.0
MAX_BID_ASK_SPREAD_PCT = 1.0
MAX_LIQUIDITY_RISK_SCORE = 90.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "是", "有"}


def _risk_gate(candidate: Mapping[str, Any], deep: Mapping[str, Any], paid: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Stage 1: risk veto. Missing paid data is always neutral, never zero/bad."""
    reasons: list[str] = []
    risk = candidate.get("_v66_risk") or candidate.get("risk_context") or candidate.get("regulatory_risk") or {}
    sources = [candidate, deep, risk if isinstance(risk, Mapping) else {}]
    hard_flags = {
        "hard_veto": "原策略硬否决", "delisting_risk": "退市风险", "regulatory_investigation": "监管立案调查",
        "major_illegal": "重大违规", "major_negative_announcement": "重大利空公告", "suspended": "停牌",
        "cannot_buy": "不可买入",
    }
    for key, label in hard_flags.items():
        if any(_truthy(src.get(key)) for src in sources if isinstance(src, Mapping)):
            reasons.append(label)
    for key, label, threshold in (
        ("regulatory_risk_score", "监管风险过高", 85),
        ("announcement_risk_score", "公告风险过高", 85),
        ("liquidity_risk_score", "流动性风险过高", 90),
    ):
        vals = [_num(src.get(key), -1) for src in sources if isinstance(src, Mapping)]
        if vals and max(vals) >= threshold:
            reasons.append(label)
    if any(_truthy(src.get("is_st")) and _truthy(src.get("delisting_risk"))
           for src in sources if isinstance(src, Mapping)):
        reasons.append("ST且存在退市风险")
    ann = paid.get("announcement") if isinstance(paid, Mapping) else {}
    if isinstance(ann, Mapping):
        level = str(ann.get("announcement_risk_level") or "UNKNOWN")
        if ann.get("hard_veto") or level in {"HARD_BLOCK", "HIGH"}:
            reasons.append("付费公告硬风险")
    return (len(reasons) == 0), reasons


def _quality_gate(candidate: Mapping[str, Any], sector_item: Mapping[str, Any], deep: Mapping[str, Any], paid: Mapping[str, Any]) -> tuple[bool, list[str], float]:
    """Stage 2: trade-quality filtering. Conservative and explicit; unavailable data is neutral."""
    reasons=[]
    quality=50.0
    breadth=_num(sector_item.get("breadth"),1.0)
    accel=_num(sector_item.get("accel"),0.0)
    move=_num(candidate.get("move"),0.0)
    pct=_num(candidate.get("pct"),0.0)
    if breadth < 0.35 and pct > 3.0:
        reasons.append("板块扩散不足且个股孤立拉升")
    if pct >= 8.5 and move > 0:
        reasons.append("接近涨停区追高风险")
    if _truthy(candidate.get("one_word_limit_up")):
        reasons.append("一字涨停无法合理成交")
    if _truthy(candidate.get("is_limit_up_locked")):
        reasons.append("涨停封死无法合理成交")
    if candidate.get("amount") not in (None, ""):
        amount=_num(candidate.get("amount"),-1)
        if 0 <= amount < MIN_TURNOVER_AMOUNT_YUAN:
            reasons.append("成交额过低")
    if candidate.get("bid_ask_spread_pct") not in (None, ""):
        spread=_num(candidate.get("bid_ask_spread_pct"),-1)
        if spread > MAX_BID_ASK_SPREAD_PCT:
            reasons.append("买卖价差过大")
    if candidate.get("liquidity_risk_score") not in (None, ""):
        if _num(candidate.get("liquidity_risk_score"),-1) >= MAX_LIQUIDITY_RISK_SCORE:
            reasons.append("流动性风险过高")
    minute = paid.get("realtime_minute") if isinstance(paid, Mapping) else {}
    if (isinstance(minute, Mapping) and minute.get("freshness_status","FRESH")=="FRESH"
            and minute.get("blowoff_reversal")):
        reasons.append("分钟线放量冲高回落")
    auction = paid.get("auction") if isinstance(paid, Mapping) else {}
    if isinstance(auction, Mapping):
        # Realtime auction is not available yet. Missing auction is neutral for ranking;
        # the state machine uses a documented degraded confirmation path instead.
        if auction.get("auction_tradeable") is False:
            reasons.extend(str(x) for x in (auction.get("auction_risk_reasons") or ["竞价不可交易"]))
    if breadth >= 1.0: quality += 12
    if accel >= 0.3: quality += 8
    if isinstance(minute, Mapping) and minute.get("quality") == "OK": quality += 6
    # Too far from planned buy zone is a quality issue, not a hard risk veto.
    trade=deep.get("trade") or {}
    zone=str(trade.get("zone") or "")
    try:
        if "-" in zone:
            hi=float(zone.split("-")[-1]); price=float(candidate.get("price") or 0)
            if price>0 and hi>0 and price/hi-1>0.025:
                reasons.append("价格明显高于计划买入区")
    except Exception:
        pass
    return (len(reasons)==0), reasons, max(0.0,min(100.0,quality))


def evaluate_candidate(candidate: Mapping[str, Any], sector_item: Mapping[str, Any], market_regime: str,
                       paid_factors: Mapping[str, Any] | None = None) -> dict[str, Any]:
    deep = candidate.get("_v64") or {}
    force = deep.get("force") or {}
    trade = deep.get("trade") or {}
    paid = dict(paid_factors or candidate.get("_v7_paid") or {})
    buy_score = _num(deep.get("buy_score"), _num(candidate.get("score"), 50))
    individual = _num(deep.get("individual_score"), 50)
    trend = _num(deep.get("trend_score"), 50)
    force_score = _num(force.get("score"), 50)
    sector_score = _num(sector_item.get("score"), 50)
    breadth = _num(sector_item.get("breadth"), 1.0)
    accel = _num(sector_item.get("accel"), 0.0)

    market_score = {"偏强": 75.0, "中性": 60.0, "偏弱": 40.0}.get(str(market_regime), 55.0)
    breadth_score = max(0.0, min(100.0, 45.0 + breadth * 15.0 + accel * 12.0))
    price_action_score = max(0.0, min(100.0, 0.40 * buy_score + 0.25 * individual + 0.20 * trend + 0.15 * force_score))
    # Stage 3 ranking: only stable core factors contribute in V7.0 baseline.
    score = round(0.20 * market_score + 0.30 * sector_score + 0.20 * breadth_score + 0.30 * price_action_score, 2)

    risk_pass, veto_reasons = _risk_gate(candidate, deep, paid)
    quality_pass, quality_reasons, quality_score = _quality_gate(candidate, sector_item, deep, paid)
    pass_all = risk_pass and quality_pass
    if not risk_pass:
        grade = "否决"
    elif not quality_pass:
        grade = "过滤"
    elif score >= 82:
        grade = "A"
    elif score >= 72:
        grade = "B"
    else:
        grade = "观察"

    # Keep V6.6 score immutable and save V7.1 dimensions separately. Missing paid data lowers evidence completeness, not the score itself.
    v66_score = buy_score
    v71_risk_score = 100.0 if risk_pass else 0.0
    v71_quality_score = round(quality_score, 2)
    next_day_potential_score = round(max(0.0, min(100.0, 0.55 * score + 0.45 * v71_quality_score)), 2) if pass_all else 0.0
    potential_label = "高" if next_day_potential_score >= 82 else ("中" if next_day_potential_score >= 68 else "低")
    evidence_keys=("announcement","auction","realtime_minute","news","chip_cost")
    known=0
    for key in evidence_keys:
        val=paid.get(key) if isinstance(paid, Mapping) else None
        if isinstance(val, Mapping) and str(val.get("status") or "") not in {"", "DATA_UNAVAILABLE", "DISABLED"} and str(val.get("freshness_status") or "") not in {"DATA_UNAVAILABLE", "UNKNOWN"}:
            known += 1
    data_confidence = round(known / len(evidence_keys), 2)

    paid_status={}
    for k in ("auction","realtime_minute","announcement","news","chip_cost"):
        v=paid.get(k)
        if isinstance(v, Mapping): paid_status[k]=v.get("status","OK")
        elif v is None: paid_status[k]="DATA_UNAVAILABLE"
        else: paid_status[k]=v

    return {
        "engine_version": ENGINE_VERSION,
        "score": score,
        "grade": grade,
        "risk_gate_pass": risk_pass,
        "quality_gate_pass": quality_pass,
        "pass_all_gates": pass_all,
        "veto_reasons": veto_reasons,
        "quality_filter_reasons": quality_reasons,
        "quality_score": round(quality_score,2),
        "v66_score": round(v66_score, 2),
        "v71_risk_score": v71_risk_score,
        "v71_quality_score": v71_quality_score,
        "next_day_potential_score": next_day_potential_score,
        "next_day_potential_label": potential_label,
        "data_confidence": data_confidence,
        "market_score": round(market_score, 2),
        "sector_score": round(sector_score, 2),
        "breadth_score": round(breadth_score, 2),
        "price_action_score": round(price_action_score, 2),
        "factor_contributions": {
            "market": round(0.20 * market_score, 2),
            "sector": round(0.30 * sector_score, 2),
            "breadth": round(0.20 * breadth_score, 2),
            "price_action": round(0.30 * price_action_score, 2),
        },
        "paid_factors": paid_status,
        "paid_evidence": paid,
        "trade_plan": trade,
    }
