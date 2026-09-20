from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import json

from .data_provenance import build_provenance, normalize_amount
from .storage import connect, initialize


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def archive_daily(ts_code: str, rows: Iterable[Mapping[str, Any]], fetched_at: datetime,
                  path: Path | None = None, *, source: str = "TUSHARE_DAILY",
                  volume_unit: str = "LOTS_100", amount_unit: str = "THOUSAND_YUAN",
                  adjustment: str = "NONE", fallback_rank: int = 0) -> int:
    initialize(path)
    values = []; provenance = []
    for row in rows:
        trade_date = str(row.get("trade_date") or "").strip()
        if not trade_date:
            continue
        amount_raw = _number(row.get("amount"))
        proof = build_provenance(source=source, fetched_at=fetched_at,
          source_time=row.get("trade_date"), volume_unit=volume_unit, amount_unit=amount_unit,
          adjustment=adjustment, fallback_rank=fallback_rank, row=row)
        values.append((ts_code, trade_date, _number(row.get("open")), _number(row.get("high")),
                       _number(row.get("low")), _number(row.get("close")), _number(row.get("pre_close")),
                       _number(row.get("pct_chg")), _number(row.get("vol")),
                       normalize_amount(amount_raw, amount_unit),
                       fetched_at.isoformat(timespec="seconds"), source))
        provenance.append(("DAILY",ts_code,trade_date,"",source,proof["source_time"],proof["fetched_at"],
          proof["volume_unit"],proof["amount_unit"],proof["adjustment"],proof["fallback_rank"],
          proof["quality_status"],proof["payload_hash"],json.dumps(proof["warnings"],ensure_ascii=False),
          json.dumps({"normalized_amount_yuan":normalize_amount(amount_raw,amount_unit)},ensure_ascii=False)))
    if not values:
        return 0
    db = connect(path)
    db.executemany("""INSERT INTO v8_daily_bars
      (ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol_raw,amount_yuan,fetched_at,source)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,pre_close=excluded.pre_close,
      pct_chg=excluded.pct_chg,vol_raw=excluded.vol_raw,amount_yuan=excluded.amount_yuan,
      fetched_at=excluded.fetched_at,source=excluded.source""", values)
    db.executemany("""INSERT INTO v8_market_data_provenance(dataset,ts_code,observed_key,freq,source,
      source_time,fetched_at,volume_unit,amount_unit,adjustment,fallback_rank,quality_status,payload_hash,
      warnings_json,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dataset,ts_code,observed_key,freq)
      DO UPDATE SET source=excluded.source,source_time=excluded.source_time,fetched_at=excluded.fetched_at,
      volume_unit=excluded.volume_unit,amount_unit=excluded.amount_unit,adjustment=excluded.adjustment,
      fallback_rank=excluded.fallback_rank,quality_status=excluded.quality_status,payload_hash=excluded.payload_hash,
      warnings_json=excluded.warnings_json,details_json=excluded.details_json""",provenance)
    db.commit(); db.close()
    return len(values)


def archive_daily_batch(rows: Iterable[Mapping[str, Any]], fetched_at: datetime,
                        path: Path | None = None, *, source: str = "TUSHARE_DAILY_BATCH",
                        volume_unit: str = "LOTS_100", amount_unit: str = "THOUSAND_YUAN",
                        adjustment: str = "NONE", fallback_rank: int = 0) -> int:
    """Mirror a full-market daily response into V8's normalized bar archive."""
    initialize(path);values=[];provenance=[]
    for row in rows:
        ts_code=str(row.get("ts_code") or "").strip()
        trade_date=str(row.get("trade_date") or "").strip()
        if not ts_code or not trade_date:
            continue
        amount_raw=_number(row.get("amount"))
        proof=build_provenance(source=source,fetched_at=fetched_at,source_time=trade_date,
          volume_unit=volume_unit,amount_unit=amount_unit,adjustment=adjustment,
          fallback_rank=fallback_rank,row=row)
        values.append((ts_code,trade_date,_number(row.get("open")),_number(row.get("high")),
          _number(row.get("low")),_number(row.get("close")),_number(row.get("pre_close")),
          _number(row.get("pct_chg")),_number(row.get("vol")),
          normalize_amount(amount_raw,amount_unit),
          fetched_at.isoformat(timespec="seconds"),source))
        provenance.append(("DAILY",ts_code,trade_date,"",source,proof["source_time"],proof["fetched_at"],
          proof["volume_unit"],proof["amount_unit"],proof["adjustment"],proof["fallback_rank"],
          proof["quality_status"],proof["payload_hash"],json.dumps(proof["warnings"],ensure_ascii=False),"{}"))
    if not values:
        return 0
    db=connect(path)
    for offset in range(0,len(values),1000):
        db.executemany("""INSERT INTO v8_daily_bars
          (ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol_raw,amount_yuan,fetched_at,source)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
          open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,pre_close=excluded.pre_close,
          pct_chg=excluded.pct_chg,vol_raw=excluded.vol_raw,amount_yuan=excluded.amount_yuan,
          fetched_at=excluded.fetched_at,source=excluded.source""",values[offset:offset+1000])
    for offset in range(0,len(provenance),1000):
        db.executemany("""INSERT INTO v8_market_data_provenance(dataset,ts_code,observed_key,freq,source,
          source_time,fetched_at,volume_unit,amount_unit,adjustment,fallback_rank,quality_status,payload_hash,
          warnings_json,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dataset,ts_code,observed_key,freq)
          DO UPDATE SET source=excluded.source,source_time=excluded.source_time,fetched_at=excluded.fetched_at,
          volume_unit=excluded.volume_unit,amount_unit=excluded.amount_unit,adjustment=excluded.adjustment,
          fallback_rank=excluded.fallback_rank,quality_status=excluded.quality_status,payload_hash=excluded.payload_hash,
          warnings_json=excluded.warnings_json,details_json=excluded.details_json""",provenance[offset:offset+1000])
    db.commit();db.close();return len(values)


def archive_minute(ts_code: str, rows: Iterable[Mapping[str, Any]], fetched_at: datetime,
                   freq: str = "1MIN", path: Path | None = None, *,
                   source: str = "TUSHARE_RT_MIN", volume_unit: str = "UNKNOWN",
                   amount_unit: str = "YUAN", adjustment: str = "NONE",
                   fallback_rank: int = 0) -> int:
    initialize(path)
    values = []; provenance = []
    for row in rows:
        bar_time = str(row.get("time") or row.get("trade_time") or row.get("datetime") or "").strip()
        if not bar_time:
            continue
        proof=build_provenance(source=source,fetched_at=fetched_at,source_time=bar_time,
          volume_unit=volume_unit,amount_unit=amount_unit,adjustment=adjustment,
          fallback_rank=fallback_rank,row=row)
        values.append((ts_code, bar_time, freq, _number(row.get("open")), _number(row.get("high")),
                       _number(row.get("low")), _number(row.get("close")), _number(row.get("vol")),
                       normalize_amount(row.get("amount"),amount_unit), fetched_at.isoformat(timespec="seconds"), source))
        provenance.append(("MINUTE",ts_code,bar_time,freq,source,proof["source_time"],proof["fetched_at"],
          proof["volume_unit"],proof["amount_unit"],proof["adjustment"],proof["fallback_rank"],
          proof["quality_status"],proof["payload_hash"],json.dumps(proof["warnings"],ensure_ascii=False),"{}"))
    if not values:
        return 0
    db = connect(path)
    db.executemany("""INSERT INTO v8_minute_bars
      (ts_code,bar_time,freq,open,high,low,close,vol_raw,amount_yuan,fetched_at,source)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,bar_time,freq) DO UPDATE SET
      open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,vol_raw=excluded.vol_raw,
      amount_yuan=excluded.amount_yuan,fetched_at=excluded.fetched_at,source=excluded.source""", values)
    db.executemany("""INSERT INTO v8_market_data_provenance(dataset,ts_code,observed_key,freq,source,
      source_time,fetched_at,volume_unit,amount_unit,adjustment,fallback_rank,quality_status,payload_hash,
      warnings_json,details_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dataset,ts_code,observed_key,freq)
      DO UPDATE SET source=excluded.source,source_time=excluded.source_time,fetched_at=excluded.fetched_at,
      volume_unit=excluded.volume_unit,amount_unit=excluded.amount_unit,adjustment=excluded.adjustment,
      fallback_rank=excluded.fallback_rank,quality_status=excluded.quality_status,payload_hash=excluded.payload_hash,
      warnings_json=excluded.warnings_json,details_json=excluded.details_json""",provenance)
    db.commit(); db.close()
    return len(values)
