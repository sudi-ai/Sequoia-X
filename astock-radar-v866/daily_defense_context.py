# -*- coding: utf-8 -*-
"""Cached completed-day context for portfolio defense.

Intraday decisions never use the unfinished current daily bar.  This module
reads only completed daily bars and records the source and fallback state.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from config import DATA_DIR


DB_PATH = DATA_DIR / "daily_defense_context.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_defense_context(
 ts_code TEXT PRIMARY KEY, asof_trade_date TEXT, atr14 REAL, support20 REAL,
 ma20 REAL, ma60 REAL, rows_used INTEGER NOT NULL, source TEXT, status TEXT NOT NULL,
 checked_at TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}'
);
"""


def _number(value):
    try:
        value = float(value)
        return value if value == value else None
    except (TypeError, ValueError):
        return None


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atr14(rows):
    ordered = sorted(rows, key=lambda row: str(row.get("trade_date") or ""))
    if len(ordered) < 15:
        return None
    values = []
    for index, row in enumerate(ordered[-15:]):
        high, low = _number(row.get("high")), _number(row.get("low"))
        previous = _number(row.get("pre_close")) or (_number(ordered[-16 + index].get("close")) if index else None)
        if high is None or low is None:
            continue
        values.append(max(high - low, abs(high - previous) if previous is not None else 0.0, abs(low - previous) if previous is not None else 0.0))
    return round(sum(values[-14:]) / 14, 6) if len(values) >= 14 else None


class DailyDefenseContext:
    def __init__(self):
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def _cached(code):
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM daily_defense_context WHERE ts_code=?", (code,)).fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _save(code, context):
        with closing(sqlite3.connect(DB_PATH)) as conn, conn:
            conn.execute(
                "INSERT INTO daily_defense_context(ts_code,asof_trade_date,atr14,support20,ma20,ma60,rows_used,source,status,checked_at,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ts_code) DO UPDATE SET asof_trade_date=excluded.asof_trade_date,atr14=excluded.atr14,support20=excluded.support20,ma20=excluded.ma20,ma60=excluded.ma60,rows_used=excluded.rows_used,source=excluded.source,status=excluded.status,checked_at=excluded.checked_at,details_json=excluded.details_json",
                (code, context.get("asof_trade_date", ""), context.get("atr14"), context.get("support20"), context.get("ma20"), context.get("ma60"), int(context.get("rows_used") or 0), context.get("source", ""), context.get("status", "UNAVAILABLE"), context.get("checked_at", _now()), json.dumps(context.get("details") or {}, ensure_ascii=False)),
            )

    def get(self, code):
        cached = self._cached(code)
        today = datetime.now().astimezone().date().isoformat()
        if cached and cached.get("status") == "OK" and str(cached.get("checked_at") or "")[:10] == today:
            return {**cached, "details": json.loads(cached.get("details_json") or "{}"), "from_cache": True}
        try:
            from paid_data_hub import DataHub
            # Yesterday is the latest day certainly known before today's close.
            end_date = (datetime.now().astimezone().date() - timedelta(days=1)).strftime("%Y%m%d")
            frame = DataHub().daily_bars(code, end_date=end_date, limit=90, use_cache=True, pit_safe=False)
            rows = frame.to_dict("records") if frame is not None and not frame.empty else []
            closes = [_number(row.get("close")) for row in rows]
            closes = [value for value in closes if value is not None]
            lows = [_number(row.get("low")) for row in rows[-20:]]
            lows = [value for value in lows if value is not None]
            atr14 = _atr14(rows)
            context = {
                "asof_trade_date": str(rows[-1].get("trade_date") or "") if rows else "",
                "atr14": atr14,
                "support20": round(min(lows), 3) if lows else None,
                "ma20": round(sum(closes[-20:]) / 20, 3) if len(closes) >= 20 else None,
                "ma60": round(sum(closes[-60:]) / 60, 3) if len(closes) >= 60 else None,
                "rows_used": len(rows), "source": str(getattr(frame, "attrs", {}).get("endpoint") or "daily_bars"),
                "status": "OK" if atr14 is not None else "INCOMPLETE", "checked_at": _now(),
                "details": {"provider_status": str(getattr(frame, "attrs", {}).get("status") or ""), "current_bar_excluded": True},
            }
        except Exception as exc:
            context = {"asof_trade_date": "", "atr14": None, "support20": None, "ma20": None, "ma60": None, "rows_used": 0, "source": "daily_bars", "status": "UNAVAILABLE", "checked_at": _now(), "details": {"error": type(exc).__name__}}
        self._save(code, context)
        return {**context, "from_cache": False}
