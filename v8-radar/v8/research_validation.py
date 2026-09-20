"""Deterministic research validation for V8 Shadow strategies.

Nothing in this module can promote a strategy into live scoring.  It produces
review evidence only and fails closed when the sample is too small.
"""
from __future__ import annotations

import math
import random
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .evaluation import bootstrap_mean_ci, summarize


def _finite_values(values: Iterable[Any]) -> list[float]:
    result = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def compounded_return_pct(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    wealth = 1.0
    for value in returns:
        wealth *= 1 + float(value) / 100
    return round((wealth - 1) * 100, 4)


def max_drawdown_pct(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    wealth = peak = 1.0
    worst = 0.0
    for value in returns:
        wealth *= 1 + float(value) / 100
        peak = max(peak, wealth)
        worst = min(worst, (wealth / peak - 1) * 100)
    return round(worst, 4)


def monte_carlo_trade_paths(returns: Sequence[float], *, paths: int = 1000,
                            seed: int = 20260901) -> dict[str, Any]:
    values = _finite_values(returns)
    if len(values) < 20:
        return {"available": False, "reason": "NEED_AT_LEAST_20_MATURE_SAMPLES",
                "sample_n": len(values)}
    paths = max(200, min(int(paths), 10_000))
    rng = random.Random(seed)
    totals: list[float] = []
    drawdowns: list[float] = []
    for _ in range(paths):
        sample = [rng.choice(values) for _ in values]
        totals.append(float(compounded_return_pct(sample) or 0))
        drawdowns.append(float(max_drawdown_pct(sample) or 0))
    totals.sort(); drawdowns.sort()
    low = max(0, int(paths * .05))
    high = min(paths - 1, int(paths * .95))
    return {
        "available": True,
        "sample_n": len(values),
        "paths": paths,
        "total_return_p05": round(totals[low], 4),
        "total_return_median": round(statistics.median(totals), 4),
        "total_return_p95": round(totals[high], 4),
        "max_drawdown_p05": round(drawdowns[low], 4),
        "max_drawdown_median": round(statistics.median(drawdowns), 4),
        "loss_path_probability_pct": round(sum(x <= 0 for x in totals) / paths * 100, 2),
    }


def walk_forward_splits(sample_count: int, *, train_size: int = 252,
                        test_size: int = 63, step: int = 63,
                        purge: int = 10, embargo: int = 5) -> list[dict[str, int]]:
    """Return non-overlapping, leakage-aware integer windows.

    Purge separates train from test; embargo separates one test window from
    the next training end.  The function never fills missing observations.
    """
    for name, value in {"sample_count": sample_count, "train_size": train_size,
                        "test_size": test_size, "step": step,
                        "purge": purge, "embargo": embargo}.items():
        if int(value) < 0 or (name in {"train_size", "test_size", "step"} and int(value) == 0):
            raise ValueError(f"INVALID_{name.upper()}")
    result = []
    train_start = 0
    while True:
        train_end = train_start + train_size
        test_start = train_end + purge
        test_end = test_start + test_size
        if test_end > sample_count:
            break
        result.append({"train_start": train_start, "train_end": train_end,
                       "test_start": test_start, "test_end": test_end,
                       "purge": purge, "embargo": embargo})
        train_start += step + embargo
    return result


def evaluate_research_sample(rows: Iterable[Mapping[str, Any]], *,
                             return_key: str = "net_return_pct",
                             benchmark_key: str = "benchmark_return_pct") -> dict[str, Any]:
    materialized = [dict(row) for row in rows]
    mature = [row for row in materialized if bool(row.get("matured")) and row.get(return_key) is not None]
    returns = _finite_values(row.get(return_key) for row in mature)
    alpha_rows = []
    for row in mature:
        try:
            value = float(row[return_key]) - float(row[benchmark_key])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            alpha_rows.append({"matured": 1, "alpha_pct": value})
    result = summarize(materialized, return_key=return_key)
    result.update({
        "compounded_return_pct": compounded_return_pct(returns),
        "sequence_max_drawdown_pct": max_drawdown_pct(returns),
        "bootstrap_mean_ci_95": bootstrap_mean_ci(materialized, return_key=return_key),
        "monte_carlo": monte_carlo_trade_paths(returns),
        "benchmark_coverage_n": len(alpha_rows),
        "alpha": summarize(alpha_rows, return_key="alpha_pct") if alpha_rows else None,
    })
    return result


def promotion_gate(metrics: Mapping[str, Any], *, min_samples: int = 60,
                   min_profit_factor: float = 1.1,
                   max_drawdown_floor_pct: float = -25.0) -> dict[str, Any]:
    reasons = []
    if int(metrics.get("mature_n") or 0) < min_samples:
        reasons.append(f"成熟样本不足{min_samples}")
    if metrics.get("median") is None or float(metrics["median"]) <= 0:
        reasons.append("中位收益未大于0")
    profit_factor = metrics.get("profit_factor")
    # The legacy summary represents an all-winning mature sample as None
    # because the denominator (gross loss) is zero.  Treat that specific
    # case as an infinite profit factor; all other missing values fail closed.
    no_mature_loss = (int(metrics.get("mature_n") or 0) > 0
                      and metrics.get("worst_trade") is not None
                      and float(metrics["worst_trade"]) >= 0)
    if ((profit_factor is None and not no_mature_loss)
            or (profit_factor is not None and float(profit_factor) < min_profit_factor)):
        reasons.append(f"利润因子低于{min_profit_factor}")
    drawdown = metrics.get("sequence_max_drawdown_pct")
    if drawdown is None or float(drawdown) < max_drawdown_floor_pct:
        reasons.append(f"最大回撤差于{max_drawdown_floor_pct}%")
    interval = metrics.get("bootstrap_mean_ci_95") or {}
    interval_low = interval.get("low")
    if not interval.get("available") or interval_low is None or float(interval_low) <= 0:
        reasons.append("平均收益置信区间下界未大于0")
    monte = metrics.get("monte_carlo") or {}
    loss_probability = monte.get("loss_path_probability_pct")
    if (not monte.get("available") or loss_probability is None
            or float(loss_probability) > 35):
        reasons.append("蒙特卡洛亏损路径概率未达标")
    return {
        "eligible_for_manual_review": not reasons,
        "action_permission": "REVIEW_ONLY" if not reasons else "ANNOTATION_ONLY",
        "reasons": reasons,
        "automatic_promotion": False,
    }
