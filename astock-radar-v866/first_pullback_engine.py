# -*- coding: utf-8 -*-
"""First-valid-pullback gate; abstains without an intraday path."""
from __future__ import annotations


def evaluate(intraday_samples=None):
    rows = list(intraday_samples or [])
    if len(rows) < 5:
        return {"status": "INSUFFICIENT_INTRADAY_DATA", "first_pullback_quality": None,
                "decision": "WAIT", "reason": "未观察到完整突破、回踩、缩量和重新上攻序列"}
    return {"status": "OBSERVATION_REQUIRED", "first_pullback_quality": None,
            "decision": "WAIT", "reason": "模块已接线，需经真实PIT标签校准后才允许确认"}
