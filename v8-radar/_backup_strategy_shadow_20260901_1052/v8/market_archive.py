from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .storage import connect, initialize


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def archive_daily(ts_code: str, rows: Iterable[Mapping[str, Any]], fetched_at: datetime,
                  path: Path | None = None) -> int:
    initialize(path)
    values = []
    for row in rows:
        trade_date = str(row.get("trade_date") or "").strip()
        if not trade_date:
            continue
        amount_raw = _number(row.get("amount"))
        values.append((ts_code, trade_date, _number(row.get("open")), _number(row.get("high")),
                       _number(row.get("low")), _number(row.get("close")), _number(row.get("pre_close")),
                       _number(row.get("pct_chg")), _number(row.get("vol")),
                       amount_raw * 1000 if amount_raw is not None else None,
                       fetched_at.isoformat(timespec="seconds"), "TUSHARE_DAILY"))
    if not values:
        return 0
    db = connect(path)
    db.executemany("""INSERT INTO v8_daily_bars
      (ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol_raw,amount_yuan,fetched_at,source)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,pre_close=excluded.pre_close,
      pct_chg=excluded.pct_chg,vol_raw=excluded.vol_raw,amount_yuan=excluded.amount_yuan,
      fetched_at=excluded.fetched_at,source=excluded.source""", values)
    db.commit(); db.close()
    return len(values)


def archive_minute(ts_code: str, rows: Iterable[Mapping[str, Any]], fetched_at: datetime,
                   freq: str = "1MIN", path: Path | None = None) -> int:
    initialize(path)
    values = []
    for row in rows:
        bar_time = str(row.get("time") or row.get("trade_time") or row.get("datetime") or "").strip()
        if not bar_time:
            continue
        values.append((ts_code, bar_time, freq, _number(row.get("open")), _number(row.get("high")),
                       _number(row.get("low")), _number(row.get("close")), _number(row.get("vol")),
                       _number(row.get("amount")), fetched_at.isoformat(timespec="seconds"), "TUSHARE_RT_MIN"))
    if not values:
        return 0
    db = connect(path)
    db.executemany("""INSERT INTO v8_minute_bars
      (ts_code,bar_time,freq,open,high,low,close,vol_raw,amount_yuan,fetched_at,source)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,bar_time,freq) DO UPDATE SET
      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,vol_raw=excluded.vol_raw,
      amount_yuan=excluded.amount_yuan,fetched_at=excluded.fetched_at,source=excluded.source""", values)
    db.commit(); db.close()
    return len(values)
