# -*- coding: utf-8 -*-
"""Intraday persistence evidence with explicit insufficient-data output."""
from __future__ import annotations


WINDOWS = (5, 15, 30, 60)


def evaluate(samples):
    rows = list(samples or [])
    if len(rows) < 2:
        result = {f"persistence_{window}m": None for window in WINDOWS}
        return {**result, "status": "INSUFFICIENT_INTRADAY_DATA", "available_minutes": 0,
                "reason": "缺少连续全市场分钟快照，不用单点异动推断持续性"}
    output = {}
    for window in WINDOWS:
        subset = rows[-window:]
        valid = [row for row in subset if row.get("above_vwap") is not None]
        output[f"persistence_{window}m"] = None if len(valid) < min(window, 5) else round(
            100 * sum(bool(row.get("above_vwap")) and float(row.get("active_flow", 0) or 0) >= 0 for row in valid) / len(valid), 2
        )
    available = len(rows)
    return {**output, "status": "OK" if available >= 5 else "INSUFFICIENT_INTRADAY_DATA",
            "available_minutes": available, "reason": "仅使用连续可观察价格/VWAP/资金证据"}
