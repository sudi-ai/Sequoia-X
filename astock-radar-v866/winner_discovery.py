# -*- coding: utf-8 -*-
"""Wide cross-sectional discovery, isolated from strict V8 execution gates."""
from __future__ import annotations

import json
import math
from pathlib import Path

from anti_deception_engine import evaluate as anti_deception
from event_shock_engine import evaluate as event_shock
from false_breakout_engine import evaluate as false_breakout
from first_pullback_engine import evaluate as first_pullback
from persistence_engine import evaluate as persistence


FACTOR_WEIGHTS = {
    "price_strength_rank": 1.25, "volume_acceleration_rank": 1.00, "amount_acceleration_rank": 1.00,
    "active_buy_rank": 1.15, "relative_strength_rank": 1.10, "sector_strength_rank": 0.90,
    "sector_diffusion_rank": 0.75, "auction_surprise_rank": 0.90, "vwap_strength_rank": 1.00,
    "breakout_quality_rank": 0.90, "event_surprise_rank": 0.80, "liquidity_rank": 0.45,
    "persistence_rank": 1.10,
}


def _num(value, default=0.0):
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except (TypeError, ValueError):
        return float(default)


def _rank(items, values):
    valid = [(index, float(value)) for index, value in enumerate(values) if value is not None and math.isfinite(float(value))]
    ordered = sorted(value for _, value in valid)
    denominator = max(1, len(ordered) - 1)
    output = [None] * len(items)
    for index, value in valid:
        below = sum(1 for other in ordered if other < value)
        equal = sum(1 for other in ordered if other == value)
        output[index] = round(100 * (below + max(0, equal - 1) / 2) / denominator, 3)
    return output


def _winner_type(item):
    if item.get("at_limit_up"):
        return "MODEL_D_EMOTION_CORE"
    if _num(item.get("sector_strength"), 50) >= 62 and _num(item.get("trend_score"), 50) >= 65:
        return "MODEL_B_MAINLINE_TREND"
    if 1 <= _num(item.get("daily_pct")) <= 6 and _num(item.get("breakout_quality"), 50) >= 62:
        return "MODEL_C_LOW_BREAKOUT"
    if _num(item.get("relative_strength")) >= 3:
        return "MODEL_E_INDEPENDENT_MOVE"
    return "UNCLASSIFIED_OBSERVATION"


def discover(candidates, decision_time="", trade_date="", market_data_date=""):
    rows = [dict(item) for item in (candidates or [])]
    incoming_intraday = [bool(item.get("intraday_ready")) for item in rows]
    factor_values = {name: [] for name in FACTOR_WEIGHTS}
    for index, item in enumerate(rows):
        item["relative_strength"] = round(_num(item.get("daily_pct")) - _num(item.get("sector_pct")), 3)
        breakout = false_breakout(item); item.update(breakout)
        deception = anti_deception(item); item.update(deception)
        item["event_shock"] = event_shock(None, None)
        item["persistence"] = persistence([])
        item["first_pullback"] = first_pullback([])
        factor_values["price_strength_rank"].append(_num(item.get("daily_pct")))
        factor_values["volume_acceleration_rank"].append(_num(item.get("volume_ratio")) if item.get("volume_ratio") is not None else None)
        factor_values["amount_acceleration_rank"].append(None)
        factor_values["active_buy_rank"].append(_num(item.get("main_flow_score")) if item.get("main_flow_amount") is not None else None)
        factor_values["relative_strength_rank"].append(item["relative_strength"])
        factor_values["sector_strength_rank"].append(_num(item.get("sector_strength")))
        factor_values["sector_diffusion_rank"].append(_num(item.get("sector_resonance")))
        factor_values["auction_surprise_rank"].append(_num(item.get("auction_quality")) if item.get("auction_available") else None)
        factor_values["vwap_strength_rank"].append(None)
        factor_values["breakout_quality_rank"].append(_num(item.get("breakout_quality")))
        factor_values["event_surprise_rank"].append(None)
        factor_values["liquidity_rank"].append(math.log1p(max(0, _num(item.get("amount")))) if item.get("amount") is not None else None)
        factor_values["persistence_rank"].append(None)
    for name, values in factor_values.items():
        ranks = _rank(rows, values)
        for index, value in enumerate(ranks): rows[index][name] = value
    for item in rows:
        available = [(name, item.get(name), weight) for name, weight in FACTOR_WEIGHTS.items() if item.get(name) is not None]
        item["alpha_factor_coverage"] = round(len(available) / len(FACTOR_WEIGHTS), 3)
        item["market_alpha_score"] = round(sum(float(value) * weight for _, value, weight in available) / max(0.0001, sum(weight for _, _, weight in available)), 3) if available else None
        item["intraday_ready"] = incoming_intraday[index]
        item["discovery_mode"] = "INTRADAY_PIT" if item["intraday_ready"] and item.get("is_pit_safe") else "EOD_CROSS_SECTION"
        item["sample_domain"] = "FULL_MARKET_PIT" if item["discovery_mode"] == "INTRADAY_PIT" else "EOD_CROSS_SECTION"
    rows.sort(key=lambda item: -(item.get("market_alpha_score") or -1))
    total = max(1, len(rows)); winner_cut = max(100, min(200, round(total * 0.035)))
    for index, item in enumerate(rows, 1):
        item["market_alpha_rank"] = index
        item["market_alpha_percentile"] = round(100 * (total - index + 1) / total, 3)
        item["market_alpha_top_pct"] = round(100 * index / total, 3)
        pools = []
        if index <= winner_cut: pools.append("REALTIME_WINNERS")
        if index <= max(winner_cut, round(total * 0.08)) and 0.5 <= _num(item.get("daily_pct")) <= 6 and _num(item.get("buy_timing_score")) >= 48 and _num(item.get("false_break_risk_score")) < 58:
            pools.append("EARLY_STARTUP")
        if index <= max(winner_cut, round(total * 0.12)) and _num(item.get("daily_pct")) > 0 and _num(item.get("close_strength")) >= 70 and _num(item.get("false_break_risk_score")) < 62:
            pools.append("REVERSAL_REPAIR")
        item["discovery_pools"] = pools
        item["winner_type"] = _winner_type(item)
        item["discovery_action"] = "重点观察，等待首次有效回踩" if pools else "普通观察"
    missing = [name for name, values in factor_values.items() if not any(value is not None for value in values)]
    intraday_ready = any(item.get("sample_domain") == "FULL_MARKET_PIT" for item in rows)
    summary = {"mode": "INTRADAY_PIT" if intraday_ready else "EOD_CROSS_SECTION", "intraday_ready": intraday_ready, "sample_domain": "FULL_MARKET_PIT" if intraday_ready else "EOD_CROSS_SECTION", "universe": len(rows), "winner_cut": winner_cut,
        "realtime_winners": sum("REALTIME_WINNERS" in item["discovery_pools"] for item in rows),
        "early_startup": sum("EARLY_STARTUP" in item["discovery_pools"] for item in rows),
        "reversal_repair": sum("REVERSAL_REPAIR" in item["discovery_pools"] for item in rows),
        "missing_factors": missing, "recall_status": "等待收盘赢家标签" if intraday_ready else "全市场PIT样本不足",
        "recall_reason": "盘中发现结果已保存，收盘后才计算Top20/Top50召回" if intraday_ready else "尚无09:25/09:35/10:00等全市场PIT分钟快照，不能回算提前发现率",
        "decision_time": decision_time, "trade_date": trade_date, "market_data_date": market_data_date}
    return {"candidates": rows, "summary": summary}


def record_snapshot(data_dir, discovery):
    path = Path(data_dir) / "winner_discovery_history.jsonl"
    summary, rows = discovery.get("summary", {}), discovery.get("candidates", [])
    record = {**summary, "top20": [row.get("ts_code") for row in rows[:20]], "top50": [row.get("ts_code") for row in rows[:50]],
              "top200": [row.get("ts_code") for row in rows[:200]]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
