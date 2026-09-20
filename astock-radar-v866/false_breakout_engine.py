# -*- coding: utf-8 -*-
"""Uncalibrated breakout evidence score, deliberately not a probability."""
from __future__ import annotations


def evaluate(feature):
    f = dict(feature or {})
    quality = 50.0
    quality += (float(f.get("close_strength", 50) or 50) - 50) * 0.35
    quality += max(-15, min(15, float(f.get("main_flow_score", 0) or 0) * 0.25))
    quality += (float(f.get("sector_strength", 50) or 50) - 50) * 0.18
    quality -= float(f.get("distribution_risk", 0) or 0) * 0.25
    quality = max(0.0, min(100.0, quality))
    risk = round(100 - quality, 2)
    return {"status": "EOD_PROXY_UNCALIBRATED", "breakout_quality": round(quality, 2),
            "false_break_risk_score": risk, "false_break_probability": None,
            "reason": "缺少突破后分钟停留、首次回踩和再次上攻标签，风险分不能称为概率"}
