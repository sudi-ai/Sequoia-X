"""Compatibility accessors backed only by V8's independent context cache."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CN = ZoneInfo("Asia/Shanghai")
CONTEXT = ROOT / "data_v8" / "independent_discovery_state.json"


def _load() -> dict[str, Any]:
    try:
        value = json.loads(CONTEXT.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _stamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value))
        return result.replace(tzinfo=CN) if result.tzinfo is None else result.astimezone(CN)
    except Exception:
        return None


def market_context(now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(CN)).astimezone(CN)
    raw = _load(); observed = _stamp(raw.get("observed_at")); market = raw.get("market") or {}
    if not observed or observed.date() != now.date():
        return {"source_status": "UNAVAILABLE", "missing": ["v8_independent_market_snapshot"]}
    return {"source_status": "FRESH" if now-observed <= timedelta(minutes=6) else "STALE",
            "data_time": raw.get("source_time") or observed.isoformat(),
            "advance_ratio": market.get("advance_ratio"), "limit_up_count": market.get("limit_up"),
            "limit_down_count": market.get("limit_down"), "market_score": market.get("score"),
            "market_regime": market.get("regime"), "source": "V8独立全市场快照", "missing": []}


def sector_context(sector_name: str, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(CN)).astimezone(CN)
    raw = _load(); observed = _stamp(raw.get("observed_at")); sectors = raw.get("sectors") or {}
    row = sectors.get(str(sector_name)) if isinstance(sectors, dict) else None
    if not observed or observed.date() != now.date() or not isinstance(row, dict):
        return {"source_status": "UNAVAILABLE", "missing": ["v8_independent_sector_snapshot"]}
    return {"source_status": "FRESH" if now-observed <= timedelta(minutes=6) else "STALE",
            "data_time": raw.get("source_time") or observed.isoformat(), "sector": sector_name,
            "up_ratio": row.get("up_ratio"), "median_return": (row.get("median_pct") or 0)/100,
            "leader_strength": row.get("top3_pct"), "constituent_count": row.get("count"),
            "sector_score": row.get("score"), "source": "V8独立行业扩散快照",
            "missing": ["逐笔资金持续度"]}
