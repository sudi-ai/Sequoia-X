from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

RESEARCH_RULE_VERSION = "V7.2-research-iqr-1"
RESEARCH_FEATURE_VERSION = "V7.2-features-2"
RESEARCH_SAMPLE_VERSION = "RESEARCH_2000-stage2-effective1995-dedup2312"
FORBIDDEN_PREFIXES = ("future_", "outcome_")
STATS_PATH = Path(__file__).with_name("research_2000_frozen_stats.json")

_FEATURE_ALIASES = {
    "ma20_gap": ("ma20_gap", "ma20_gap_pct"),
    "ma60_gap": ("ma60_gap", "ma60_gap_pct"),
    "ma20_slope10": ("ma20_slope10", "ma20_slope"),
    "ret5": ("ret5",),
    "ret20": ("ret20",),
    "ret60": ("ret60",),
    "drawdown20": ("drawdown20", "drawdown20_pct"),
    "vol_ratio": ("vol_ratio", "volume_ratio"),
    "amount_ratio": ("amount_ratio",),
    "atr14_pct": ("atr14_pct",),
    "close_pos": ("close_pos",),
    "lower_wick": ("lower_wick",),
    "upper_wick": ("upper_wick",),
}


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def assert_no_future_fields(features: Mapping[str, Any]) -> None:
    bad = [str(k) for k in features if str(k).startswith(FORBIDDEN_PREFIXES)]
    if bad:
        raise ValueError("future/outcome fields forbidden in realtime research: " + ",".join(bad))


def config_hash(config: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(config), sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _load_stats(path: Path = STATS_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "states" not in data:
        raise ValueError("invalid frozen research statistics")
    return data


def _board_name(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        "MAIN": "主板", "主板": "主板",
        "CHINEXT": "创业板", "GEM": "创业板", "创业板": "创业板",
        "STAR": "科创板", "STAR50": "科创板", "科创板": "科创板",
        "ST": "主板",  # ST is retained as a risk flag separately; stage2 statistics are exchange-board based.
    }
    return aliases.get(text.upper(), aliases.get(text, text or "ALL"))


def _feature_value(features: Mapping[str, Any], canonical: str) -> float | None:
    for key in _FEATURE_ALIASES.get(canonical, (canonical,)):
        if key in features:
            val = _num(features.get(key))
            if val is not None:
                return val
    return None


def _iqr_similarity(value: float, stat: Mapping[str, Any]) -> float:
    """Robust 0-1 closeness to a frozen historical distribution.

    No learned weights are used. Distance is measured from the historical median
    in IQR units. Inside half an IQR receives high similarity; similarity decays
    smoothly and reaches 0 at roughly 3 IQRs. This is descriptive evidence only.
    """
    med = _num(stat.get("median")); q1 = _num(stat.get("q1")); q3 = _num(stat.get("q3"))
    if med is None or q1 is None or q3 is None:
        return 0.5
    scale = max(abs(q3 - q1), max(abs(med) * 0.05, 1e-6))
    d = abs(value - med) / scale
    return max(0.0, min(1.0, 1.0 - d / 3.0))


def _state_similarity(features: Mapping[str, Any], state_stats: Mapping[str, Any]) -> tuple[float, list[str], list[str], list[str]]:
    stats = state_stats.get("stats") if isinstance(state_stats.get("stats"), Mapping) else {}
    sims: list[tuple[str, float]] = []
    missing: list[str] = []
    for canonical in _FEATURE_ALIASES:
        if canonical not in stats:
            continue
        val = _feature_value(features, canonical)
        if val is None:
            missing.append(canonical)
            continue
        sims.append((canonical, _iqr_similarity(val, stats[canonical])))
    if not sims:
        return 0.5, [], [], missing
    score = sum(v for _, v in sims) / len(sims)
    support = [k for k, v in sims if v >= 0.75]
    contradict = [k for k, v in sims if v <= 0.25]
    return round(max(0.0, min(1.0, score)), 4), support, contradict, missing


def evaluate_research(features: Mapping[str, Any], board_stats: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return frozen, explainable Research similarity without BUY/SELL semantics."""
    assert_no_future_fields(features)
    features = dict(features)
    close = _num(features.get("close")); ma20 = _num(features.get("ma20")); ma60 = _num(features.get("ma60"))
    if "ma20_gap" not in features and "ma20_gap_pct" not in features and close is not None and ma20:
        features["ma20_gap_pct"] = (close / ma20 - 1.0) * 100.0
    if "ma60_gap" not in features and "ma60_gap_pct" not in features and close is not None and ma60:
        features["ma60_gap_pct"] = (close / ma60 - 1.0) * 100.0
    frozen = dict(board_stats) if board_stats else _load_stats()
    board = _board_name(features.get("board_type") or features.get("market"))
    states = frozen.get("states") if isinstance(frozen.get("states"), Mapping) else {}

    def stats_for(state: str) -> Mapping[str, Any]:
        group = states.get(state) if isinstance(states.get(state), Mapping) else {}
        return group.get(board) or group.get("ALL") or {}

    main, main_support, main_contra, m1 = _state_similarity(features, stats_for("main_rise"))
    wash, wash_support, wash_contra, m2 = _state_similarity(features, stats_for("washout"))
    decline, decline_support, decline_contra, m3 = _state_similarity(features, stats_for("true_decline"))
    missing = sorted(set(m1 + m2 + m3))
    feature_count = len(_FEATURE_ALIASES)
    observed = max(0, feature_count - len(missing))
    coverage = observed / feature_count if feature_count else 0.0
    core = {"ma20_gap","ma60_gap","ma20_slope10","ret20","drawdown20","vol_ratio","atr14_pct"}
    core_observed = len([k for k in core if k not in missing])
    core_coverage = core_observed / len(core)
    strength = "HIGH" if core_coverage >= 0.85 else ("MEDIUM" if core_coverage >= 0.55 else "LOW")

    # A research profile score is only the strongest positive-pattern similarity.
    # It is not a probability, win rate, or trade instruction.
    profile_score = round(max(main, wash), 4)
    config = {
        "rule": RESEARCH_RULE_VERSION,
        "feature": RESEARCH_FEATURE_VERSION,
        "sample": RESEARCH_SAMPLE_VERSION,
        "board": board,
        "stats_sha256": config_hash(frozen),
    }
    return {
        "research_rule_version": RESEARCH_RULE_VERSION,
        "research_feature_version": RESEARCH_FEATURE_VERSION,
        "research_sample_version": RESEARCH_SAMPLE_VERSION,
        "research_config_hash": config_hash(config),
        "research_2000_score": profile_score,
        "main_rise_similarity": main,
        "washout_similarity": wash,
        "true_decline_risk_similarity": decline,
        "supporting_evidence": {
            "MAIN_RISE": main_support,
            "WASHOUT": wash_support,
            "TRUE_DECLINE": decline_support,
        },
        "contradicting_evidence": {
            "MAIN_RISE": main_contra,
            "WASHOUT": wash_contra,
            "TRUE_DECLINE": decline_contra,
        },
        "evidence_strength": strength,
        "feature_coverage": round(coverage, 4),
        "missing_fields": missing,
        "board_type": board,
        "market_regime": features.get("market_regime"),
        "interpretation": "Research相似度，仅用于Shadow记录，不代表胜率或买卖指令",
    }
