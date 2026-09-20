# -*- coding: utf-8 -*-
"""Evidence balance; never infers unobservable market-player intent."""
from __future__ import annotations


def evaluate(feature):
    f = dict(feature or {})
    bullish, bearish = [], []
    if float(f.get("sector_strength", 50) or 50) >= 62: bullish.append("板块增强")
    if f.get("main_flow_amount") is not None and float(f.get("main_flow_score", 0) or 0) >= 20: bullish.append("主动资金横截面靠前")
    if float(f.get("close_strength", 50) or 50) >= 72: bullish.append("收盘/当前价承接较强")
    if float(f.get("relative_strength", 0) or 0) >= 2: bullish.append("相对板块强")
    if bool(f.get("auction_available")) and float(f.get("auction_quality", 50) or 50) >= 65: bullish.append("竞价相对强")
    if float(f.get("daily_pct", 0) or 0) >= 5 and float(f.get("close_strength", 50) or 50) < 52: bearish.append("冲高回落")
    if f.get("main_flow_amount") is not None and float(f.get("main_flow_score", 0) or 0) <= -20: bearish.append("资金价格分歧")
    if float(f.get("sector_strength", 50) or 50) < 40: bearish.append("板块掉队")
    if float(f.get("distribution_risk", 0) or 0) >= 55: bearish.append("高位派发证据")
    if float(f.get("close_strength", 50) or 50) < 30: bearish.append("承接偏弱")
    bull_score, bear_score = min(100, len(bullish) * 22), min(100, len(bearish) * 25)
    conflict = min(100, min(bull_score, bear_score) * 1.4)
    state = "NO_TRADE_CONFLICT" if conflict >= 50 else ("BULLISH_EVIDENCE" if bull_score > bear_score else ("BEARISH_EVIDENCE" if bear_score > bull_score else "UNKNOWN"))
    return {"bullish_evidence": bullish, "bearish_evidence": bearish, "bullish_score": bull_score,
            "bearish_score": bear_score, "conflict_score": round(conflict, 2), "state": state}
