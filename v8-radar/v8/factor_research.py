"""Dependency-free cross-sectional factor research utilities for V8."""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


RESEARCH_VERSION = "V8.4_FACTOR_LAB_1"


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2 + 1
        for offset in range(start, end):
            result[order[offset]] = rank
        start = end
    return result


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_var = sum((a - mean_left) ** 2 for a in left)
    right_var = sum((b - mean_right) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return numerator / math.sqrt(left_var * right_var)


def spearman_ic(rows: Iterable[Mapping[str, Any]], *, factor_key: str = "factor",
                return_key: str = "forward_return") -> float | None:
    pairs = []
    for row in rows:
        factor = _num(row.get(factor_key))
        forward = _num(row.get(return_key))
        if factor is not None and forward is not None:
            pairs.append((factor, forward))
    if len(pairs) < 5:
        return None
    return_value = _pearson(_ranks([pair[0] for pair in pairs]), _ranks([pair[1] for pair in pairs]))
    return round(return_value, 6) if return_value is not None else None


def quantile_returns(rows: Iterable[Mapping[str, Any]], *, factor_key: str = "factor",
                     return_key: str = "forward_return", quantiles: int = 5) -> list[dict[str, Any]]:
    materialized = []
    for row in rows:
        factor = _num(row.get(factor_key))
        forward = _num(row.get(return_key))
        if factor is not None and forward is not None:
            materialized.append((factor, forward))
    materialized.sort(key=lambda pair: pair[0])
    if len(materialized) < quantiles:
        return []
    result = []
    for bucket in range(quantiles):
        start = bucket * len(materialized) // quantiles
        end = (bucket + 1) * len(materialized) // quantiles
        values = [pair[1] for pair in materialized[start:end]]
        result.append({"quantile": bucket + 1, "sample_n": len(values),
                       "mean_return": round(sum(values) / len(values), 6)})
    return result


def evaluate_factor(rows: Iterable[Mapping[str, Any]], *, factor_key: str = "factor",
                    return_key: str = "forward_return", quantiles: int = 5) -> dict[str, Any]:
    materialized = [dict(row) for row in rows]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in materialized:
        groups.setdefault(str(row.get("trade_date") or "ALL"), []).append(row)
    daily_ics = [value for value in (
        spearman_ic(group, factor_key=factor_key, return_key=return_key)
        for group in groups.values()
    ) if value is not None]
    mean_ic = sum(daily_ics) / len(daily_ics) if daily_ics else None
    if len(daily_ics) >= 2:
        variance = sum((value - mean_ic) ** 2 for value in daily_ics) / (len(daily_ics) - 1)
        std = math.sqrt(variance)
        icir = mean_ic / std if std > 0 else None
    else:
        icir = None
    quantile = quantile_returns(materialized, factor_key=factor_key,
                                return_key=return_key, quantiles=quantiles)
    monotonic = bool(quantile and all(
        quantile[index]["mean_return"] <= quantile[index + 1]["mean_return"]
        for index in range(len(quantile) - 1)
    ))
    return {"research_version": RESEARCH_VERSION, "sample_n": len(materialized),
            "day_n": len(groups), "mean_ic": round(mean_ic, 6) if mean_ic is not None else None,
            "icir": round(icir, 6) if icir is not None else None,
            "quantile_returns": quantile, "quantile_monotonic": monotonic,
            "action_permission": "ANNOTATION_ONLY"}
