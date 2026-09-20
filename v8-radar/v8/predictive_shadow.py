"""V8 probability, point-in-time and portfolio-risk research layer.

The module is deliberately fail-closed and annotation-only.  It never grants
entry permission.  It turns matured executable outcomes into auditable
probability evidence without pretending that a hand-written score is a win
probability.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


MODEL_VERSION = "V8.4_PREDICTIVE_SHADOW_1"
_PROBABILITY_ROWS_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


def _probability_rows(horizon: str, path: Path | None) -> list[dict[str, Any]]:
    """Reuse matured labels briefly so candidate scans do not hammer SQLite."""
    from .storage import connect, initialize

    resolved = str(initialize(path).resolve())
    cache_key = (resolved, str(horizon))
    cached = _PROBABILITY_ROWS_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] <= 60:
        return cached[1]
    db = connect(Path(resolved))
    rows = [dict(row) for row in db.execute("""SELECT d.opportunity_score,d.market_regime,d.sector_score,
      d.trend_quality_score,d.fund_quality_score,d.signal_persistence_score,d.decision_json,
      o.net_return_pct,o.mfe_pct,o.mae_pct
      FROM v8_signal_decisions d JOIN v8_execution_outcomes o ON o.event_key=d.event_key
      WHERE d.action='SHADOW_ENTRY_CONFIRMED' AND o.track IN ('FIXED','FORMAL') AND o.horizon=?
        AND o.matured=1 AND o.net_return_pct IS NOT NULL
      ORDER BY d.signal_time DESC LIMIT 3000""", (str(horizon),)).fetchall()]
    db.close()
    _PROBABILITY_ROWS_CACHE[cache_key] = (now, rows)
    return rows


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def wilson_interval(wins: int, sample_n: int, *, z: float = 1.95996398454) -> tuple[float | None, float | None]:
    """Return a 95% Wilson interval as percentages."""
    if sample_n <= 0 or wins < 0 or wins > sample_n:
        return None, None
    p = wins / sample_n
    denominator = 1 + z * z / sample_n
    center = (p + z * z / (2 * sample_n)) / denominator
    half = z * math.sqrt(p * (1 - p) / sample_n + z * z / (4 * sample_n * sample_n)) / denominator
    return round(max(0.0, center - half) * 100, 2), round(min(1.0, center + half) * 100, 2)


def _feature(row: Mapping[str, Any], name: str) -> float | None:
    aliases = {
        "opportunity_score": "opportunity_score",
        "sector_score": "sector_score",
        "trend_score": "trend_quality_score",
        "fund_score": "fund_quality_score",
        "persistence_score": "signal_persistence_score",
    }
    value = _num(row.get(aliases.get(name, name)))
    if value is not None:
        return value
    try:
        payload = json.loads(str(row.get("decision_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if name == "entry_quality_score":
        return _num(payload.get(name))
    return value


def _distance(candidate: Mapping[str, Any], sample: Mapping[str, Any]) -> float:
    fields = (
        "opportunity_score", "entry_quality_score", "sector_score",
        "trend_score", "fund_score", "persistence_score",
    )
    differences = []
    for field in fields:
        left = _feature(candidate, field)
        right = _feature(sample, field)
        if left is not None and right is not None:
            differences.append(abs(left - right) / 20.0)
    distance = sum(differences) / len(differences) if differences else 9.0
    left_regime = str(candidate.get("market_regime") or "")
    right_regime = str(sample.get("market_regime") or "")
    if left_regime and right_regime and left_regime != right_regime:
        distance += 1.25
    return distance


def estimate_entry_probability(features: Mapping[str, Any], *, horizon: str = "D3",
                               path: Path | None = None, minimum_samples: int = 30,
                               neighbor_limit: int = 200) -> dict[str, Any]:
    """Estimate net-positive probability from nearest matured executable samples.

    A symmetric beta prior prevents tiny groups from displaying extreme values.
    Fewer than ``minimum_samples`` never receives a numeric probability.
    """
    rows = _probability_rows(horizon, path)
    ranked = sorted(((_distance(features, row), row) for row in rows), key=lambda item: item[0])
    neighbors = [row for _, row in ranked[:max(20, int(neighbor_limit))]]
    sample_n = len(neighbors)
    base = {
        "model_version": MODEL_VERSION,
        "horizon": str(horizon),
        "sample_n": sample_n,
        "minimum_samples": int(minimum_samples),
        "label_definition": "可执行确认后对应周期净收益大于0",
        "action_permission": "ANNOTATION_ONLY",
        "quantity_policy": "不通过减少候选数量制造高胜率",
    }
    if sample_n < minimum_samples:
        return {**base, "status": "INSUFFICIENT_SAMPLE", "probability_pct": None,
                "interval_low_pct": None, "interval_high_pct": None,
                "reason": f"相似成熟样本不足{minimum_samples}笔，不输出伪精确胜率"}
    returns = [float(row["net_return_pct"]) for row in neighbors]
    wins = sum(value > 0 for value in returns)
    # Beta(8, 8) regularisation: useful for ranking but still accompanied by
    # the unregularised Wilson interval and sample count.
    posterior = (wins + 8) / (sample_n + 16)
    low, high = wilson_interval(wins, sample_n)
    winning = [value for value in returns if value > 0]
    losing = [value for value in returns if value <= 0]
    average_win = sum(winning) / len(winning) if winning else 0.0
    average_loss = sum(losing) / len(losing) if losing else 0.0
    expected = posterior * average_win + (1 - posterior) * average_loss
    gross_win = sum(winning)
    gross_loss = abs(sum(losing))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    if sample_n >= 200 and posterior >= .80 and low is not None and low >= 70 and expected > 0:
        tier = "HIGH_CONFIDENCE_RESEARCH"
    elif sample_n >= 60 and posterior >= .65 and expected > 0:
        tier = "POSITIVE_RESEARCH"
    else:
        tier = "UNPROVEN"
    return {
        **base, "status": "AVAILABLE", "wins": wins,
        "raw_win_rate_pct": round(wins / sample_n * 100, 2),
        "probability_pct": round(posterior * 100, 2),
        "interval_low_pct": low, "interval_high_pct": high,
        "average_win_pct": round(average_win, 4),
        "average_loss_pct": round(average_loss, 4),
        "expected_net_return_pct": round(expected, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "confidence_tier": tier,
        "reason": "概率来自相似市场状态与买点特征的成熟可执行样本",
    }


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def audit_point_in_time(evaluation_time: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Detect evidence timestamps later than the decision timestamp."""
    cutoff = _parse_timestamp(evaluation_time)
    if cutoff is None:
        return {"status": "UNKNOWN", "violations": [], "checked_fields": 0,
                "action_permission": "ANNOTATION_ONLY", "reason": "评估时间无法解析"}
    keys = {"source_time", "data_time", "trade_date", "published_at", "fetched_at", "observed_at"}
    violations: list[dict[str, str]] = []
    checked = 0

    def visit(value: Any, prefix: str = "evidence") -> None:
        nonlocal checked
        if isinstance(value, Mapping):
            for key, item in value.items():
                path = f"{prefix}.{key}"
                if str(key) in keys:
                    stamp = _parse_timestamp(item)
                    if stamp is not None:
                        checked += 1
                        # Date-only observations on the evaluation date are legal.
                        if stamp > cutoff and not (stamp.time() == datetime.min.time() and stamp.date() == cutoff.date()):
                            violations.append({"field": path, "value": str(item)})
                visit(item, path)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{prefix}[{index}]")

    visit(evidence)
    return {"status": "INVALID" if violations else "VALID", "violations": violations,
            "checked_fields": checked, "evidence_cutoff": cutoff.isoformat(timespec="seconds"),
            "action_permission": "ANNOTATION_ONLY",
            "reason": "发现未来时点证据" if violations else "未发现未来数据回写"}


def _returns_by_date(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: str(row.get("trade_date") or ""))
    result: dict[str, float] = {}
    previous = None
    for row in ordered:
        close = _num(row.get("close"))
        day = str(row.get("trade_date") or "")
        if close is not None and close > 0 and previous is not None and previous > 0 and day:
            result[day] = close / previous - 1
        if close is not None and close > 0:
            previous = close
    return result


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_var = sum((a - mean_left) ** 2 for a in left)
    right_var = sum((b - mean_right) ** 2 for b in right)
    if left_var <= 0 or right_var <= 0:
        return None
    return numerator / math.sqrt(left_var * right_var)


def portfolio_correlation_shadow(code: str, *, path: Path | None = None,
                                 lookback: int = 60, minimum_overlap: int = 40) -> dict[str, Any]:
    """Compare a candidate with current holdings using archived daily returns."""
    from .storage import connect, initialize

    initialize(path)
    db = connect(path)
    positions = db.execute("SELECT code,name FROM v8_positions WHERE status='HOLDING' AND shares>0").fetchall()
    candidate_code = str(code).split(".")[0].zfill(6)

    def load(symbol: str) -> dict[str, float]:
        clean = str(symbol).split(".")[0].zfill(6)
        rows = db.execute("""SELECT trade_date,close FROM v8_daily_bars
          WHERE substr(ts_code,1,6)=? ORDER BY trade_date DESC LIMIT ?""", (clean, int(lookback) + 1)).fetchall()
        return _returns_by_date(reversed([dict(row) for row in rows]))

    candidate = load(candidate_code)
    comparisons = []
    for position in positions:
        holding_code = str(position["code"]).split(".")[0].zfill(6)
        if holding_code == candidate_code:
            continue
        holding = load(holding_code)
        days = sorted(set(candidate).intersection(holding))[-int(lookback):]
        corr = _pearson([candidate[day] for day in days], [holding[day] for day in days])
        if corr is not None:
            comparisons.append({"code": holding_code, "name": position["name"],
                                "correlation": round(corr, 4), "overlap_n": len(days)})
    db.close()
    eligible = [row for row in comparisons if int(row["overlap_n"]) >= minimum_overlap]
    if not eligible:
        return {"status": "INSUFFICIENT_SAMPLE", "risk": "UNKNOWN", "max_correlation": None,
                "comparisons": comparisons, "minimum_overlap": minimum_overlap,
                "action_permission": "ANNOTATION_ONLY", "reason": "候选与持仓的重叠日线不足"}
    strongest = max(eligible, key=lambda row: abs(float(row["correlation"])))
    corr = float(strongest["correlation"])
    risk = "HIGH" if corr >= .85 else ("MEDIUM" if corr >= .70 else "LOW")
    return {"status": "AVAILABLE", "risk": risk, "max_correlation": round(corr, 4),
            "strongest_holding": strongest, "comparisons": eligible,
            "action_permission": "ANNOTATION_ONLY",
            "reason": "用相关性避免表面分散、实际同风险暴露"}
