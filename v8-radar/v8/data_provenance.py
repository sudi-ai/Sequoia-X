"""Market-data provenance and unit-normalisation contracts.

The contract is deliberately independent from any provider implementation.  It
records what a source actually returned without granting that source trading
authority.  Unknown units stay unknown instead of being guessed.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any, Iterable, Mapping


VOLUME_MULTIPLIERS = {
    "SHARES": 1.0,
    "LOTS_100": 100.0,
}
AMOUNT_MULTIPLIERS = {
    "YUAN": 1.0,
    "THOUSAND_YUAN": 1_000.0,
    "TEN_THOUSAND_YUAN": 10_000.0,
}
ADJUSTMENTS = {"NONE", "QFQ", "HFQ", "UNKNOWN"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_volume(value: Any, unit: str) -> float | None:
    number = _finite(value)
    multiplier = VOLUME_MULTIPLIERS.get(str(unit or "").upper())
    return number * multiplier if number is not None and multiplier is not None else None


def normalize_amount(value: Any, unit: str) -> float | None:
    number = _finite(value)
    multiplier = AMOUNT_MULTIPLIERS.get(str(unit or "").upper())
    return number * multiplier if number is not None and multiplier is not None else None


def stable_payload_hash(row: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_provenance(*, source: str, fetched_at: datetime, source_time: Any = None,
                     volume_unit: str = "UNKNOWN", amount_unit: str = "UNKNOWN",
                     adjustment: str = "UNKNOWN", fallback_rank: int = 0,
                     row: Mapping[str, Any] | None = None,
                     warnings: Iterable[str] = ()) -> dict[str, Any]:
    volume = str(volume_unit or "UNKNOWN").upper()
    amount = str(amount_unit or "UNKNOWN").upper()
    adjusted = str(adjustment or "UNKNOWN").upper()
    issues = [str(item) for item in warnings if str(item).strip()]
    if volume not in {*VOLUME_MULTIPLIERS, "UNKNOWN"}:
        issues.append(f"UNSUPPORTED_VOLUME_UNIT:{volume}")
    if amount not in {*AMOUNT_MULTIPLIERS, "UNKNOWN"}:
        issues.append(f"UNSUPPORTED_AMOUNT_UNIT:{amount}")
    if adjusted not in ADJUSTMENTS:
        issues.append(f"UNSUPPORTED_ADJUSTMENT:{adjusted}")
    if not str(source or "").strip():
        issues.append("SOURCE_MISSING")
    status = "VALID" if not issues else "DEGRADED"
    return {
        "source": str(source or "UNKNOWN"),
        "source_time": str(source_time) if source_time not in (None, "") else None,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "volume_unit": volume,
        "amount_unit": amount,
        "adjustment": adjusted,
        "fallback_rank": max(0, int(fallback_rank)),
        "quality_status": status,
        "warnings": issues,
        "payload_hash": stable_payload_hash(row or {}),
    }


def compare_prices(primary_price: Any, secondary_price: Any,
                   warning_pct: float = 0.3, conflict_pct: float = 1.0) -> dict[str, Any]:
    primary = _finite(primary_price)
    secondary = _finite(secondary_price)
    if primary is None or secondary is None or primary <= 0 or secondary <= 0:
        return {"status": "INSUFFICIENT", "deviation_pct": None}
    deviation = abs(primary / secondary - 1) * 100
    status = "VALID" if deviation <= warning_pct else (
        "WARNING" if deviation <= conflict_pct else "CONFLICT"
    )
    return {"status": status, "deviation_pct": round(deviation, 4)}
