# -*- coding: utf-8 -*-
"""P0 intraday PIT collection infrastructure.

This module only collects, normalizes and stores observations. It does not
trade, tune strategy thresholds, train models or promote A/A+ signals.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from tushare_client import get_pro

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "p0_intraday_pit.db"
STATUS_PATH = DATA_DIR / "P0_REALTIME_STATUS.json"
AUCTION_STATUS_PATH = DATA_DIR / "P0_AUCTION_BASELINE_STATUS.json"
VWAP_AUDIT_JSON = ROOT / "VWAP_UNIT_AUDIT.json"
VWAP_AUDIT_TXT = ROOT / "VWAP_UNIT_AUDIT.txt"
DISCOVERY_AUDIT_JSON = ROOT / "DISCOVERY_WRITE_AUDIT.json"
DISCOVERY_AUDIT_TXT = ROOT / "DISCOVERY_WRITE_AUDIT.txt"
LOCK_PATH = DATA_DIR / "p0_intraday_collector.lock"
CN_TZ = timezone(timedelta(hours=8))
RT_PATTERN = "3*.SZ,6*.SH,0*.SZ,9*.BJ"
CHECKPOINTS = ("09:25", "09:35", "10:00", "11:00", "13:30", "14:30")
DISCOVERY_TYPES = ("REALTIME_WINNERS", "EARLY_STARTUP", "REVERSAL_REPAIR")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot_manifest(
 snapshot_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, observed_at TEXT NOT NULL,
 effective_at TEXT, source TEXT NOT NULL, source_trade_time TEXT, mode TEXT NOT NULL,
 mandatory_label TEXT, status TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
 field_count INTEGER NOT NULL DEFAULT 0, latency_ms REAL, coverage_ratio REAL,
 data_quality REAL, is_pit_safe INTEGER NOT NULL DEFAULT 0, issues_json TEXT,
 error TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_manifest_date_time ON snapshot_manifest(trade_date, observed_at);
CREATE INDEX IF NOT EXISTS ix_manifest_checkpoint ON snapshot_manifest(trade_date, mandatory_label);
CREATE TABLE IF NOT EXISTS intraday_quote(
 snapshot_id TEXT NOT NULL, trade_date TEXT NOT NULL, observed_at TEXT NOT NULL,
 effective_at TEXT NOT NULL, ts_code TEXT NOT NULL, name TEXT, industry TEXT,
 pre_close REAL, open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
 num REAL, bid_price1 REAL, bid_volume1 REAL, ask_price1 REAL, ask_volume1 REAL,
 pct_chg REAL, vwap REAL, vwap_strength REAL, amount_delta REAL,
 amount_velocity REAL, amount_acceleration REAL, return_5m REAL, return_15m REAL,
 return_30m REAL, return_60m REAL, persistence_score REAL, market_rank INTEGER,
 current_top20 INTEGER, current_top50 INTEGER, sector_up_ratio REAL,
 sector_median_pct REAL, sector_amount_velocity REAL, sector_diffusion_rank REAL,
 sequence_state TEXT, breakout_at TEXT, pullback_at TEXT, source TEXT NOT NULL,
 data_quality REAL NOT NULL, is_pit_safe INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id, ts_code),
 FOREIGN KEY(snapshot_id) REFERENCES snapshot_manifest(snapshot_id)
);
CREATE INDEX IF NOT EXISTS ix_quote_code_time ON intraday_quote(ts_code, observed_at);
CREATE INDEX IF NOT EXISTS ix_quote_date_rank ON intraday_quote(trade_date, market_rank);
CREATE TABLE IF NOT EXISTS sector_realtime_snapshot(
 snapshot_id TEXT NOT NULL, trade_date TEXT NOT NULL, observed_at TEXT NOT NULL,
 sector TEXT NOT NULL, stock_count INTEGER NOT NULL, up_count INTEGER NOT NULL,
 up_ratio REAL, median_pct REAL, amount_velocity REAL, diffusion_rank REAL,
 source TEXT NOT NULL, data_quality REAL NOT NULL, is_pit_safe INTEGER NOT NULL,
 PRIMARY KEY(snapshot_id, sector)
);
CREATE TABLE IF NOT EXISTS discovery_observation(
 snapshot_id TEXT NOT NULL, trade_date TEXT NOT NULL, observed_at TEXT NOT NULL,
 ts_code TEXT NOT NULL, discovery_type TEXT NOT NULL, discovery_score REAL,
 execution_pool TEXT, price REAL, pct_chg REAL, session_high REAL, vwap REAL,
 evidence_json TEXT, source TEXT NOT NULL, data_quality REAL NOT NULL,
 is_pit_safe INTEGER NOT NULL, PRIMARY KEY(snapshot_id, ts_code, discovery_type)
);
CREATE INDEX IF NOT EXISTS ix_discovery_date_type_time
 ON discovery_observation(trade_date, discovery_type, observed_at);
CREATE TABLE IF NOT EXISTS sequence_event(
 event_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, ts_code TEXT NOT NULL,
 event_type TEXT NOT NULL, observed_at TEXT NOT NULL, price REAL,
 reference_price REAL, vwap REAL, snapshot_id TEXT NOT NULL,
 source TEXT NOT NULL, is_pit_safe INTEGER NOT NULL,
 UNIQUE(trade_date, ts_code, event_type)
);
CREATE TABLE IF NOT EXISTS auction_daily_baseline(
 trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, open REAL, high REAL, low REAL,
 close REAL, vol REAL, amount REAL, vwap REAL, source TEXT NOT NULL,
 observed_at TEXT NOT NULL, available_from TEXT NOT NULL, data_quality REAL NOT NULL,
 PRIMARY KEY(trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS ix_auction_code_date ON auction_daily_baseline(ts_code, trade_date);
CREATE TABLE IF NOT EXISTS eod_daily_bar(
 trade_date TEXT NOT NULL, ts_code TEXT NOT NULL, open REAL, high REAL, low REAL,
 close REAL, pre_close REAL, pct_chg REAL, vol REAL, amount REAL, source TEXT NOT NULL,
 observed_at TEXT NOT NULL, is_pit_safe INTEGER NOT NULL,
 PRIMARY KEY(trade_date, ts_code)
);
CREATE TABLE IF NOT EXISTS winner_recall_daily(
 trade_date TEXT PRIMARY KEY, computed_at TEXT NOT NULL, actual_top20 INTEGER,
 actual_top50 INTEGER, recall_0925 REAL, recall_0935 REAL, recall_1000 REAL,
 recall_1100 REAL, recall_1330 REAL, recall_1430 REAL, average_lead_minutes REAL,
 realtime_winner_precision REAL, early_startup_rate REAL,
 reversal_repair_rate REAL, meta_samples_added INTEGER,
 pit_leakage_count INTEGER, status TEXT NOT NULL, details_json TEXT NOT NULL
);
"""

QUOTE_COLUMNS = (
    "snapshot_id", "trade_date", "observed_at", "effective_at", "ts_code", "name",
    "industry", "pre_close", "open", "high", "low", "close", "vol", "amount", "num",
    "bid_price1", "bid_volume1", "ask_price1", "ask_volume1", "pct_chg", "vwap",
    "vwap_strength", "amount_delta", "amount_velocity", "amount_acceleration",
    "return_5m", "return_15m", "return_30m", "return_60m", "persistence_score",
    "market_rank", "current_top20", "current_top50", "sector_up_ratio",
    "sector_median_pct", "sector_amount_velocity", "sector_diffusion_rank",
    "sequence_state", "breakout_at", "pullback_at", "source", "data_quality", "is_pit_safe",
)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def iso(value: datetime | None = None) -> str:
    return (value or now_cn()).isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp, path)


def db_connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    manifest_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(winner_recall_daily)")}
    if "recall_1430" not in manifest_columns:
        conn.execute("ALTER TABLE winner_recall_daily ADD COLUMN recall_1430 REAL")
    return conn


def clean_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return int(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def normalize_code(value: object) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        left, right = text.split(".", 1)
        return f"{left.zfill(6)}.{right}" if left.isdigit() and right in {"SH", "SZ", "BJ"} else ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 6:
        return ""
    suffix = "SH" if digits.startswith("6") else "BJ" if digits.startswith(("4", "8", "9")) else "SZ"
    return f"{digits}.{suffix}"


def in_regular_session(value: datetime) -> bool:
    hhmm = value.strftime("%H:%M:%S")
    return "09:30:00" <= hhmm <= "11:30:30" or "13:00:00" <= hhmm <= "15:00:30"


def in_capture_window(value: datetime) -> bool:
    hhmm = value.strftime("%H:%M:%S")
    return "09:24:30" <= hhmm <= "09:26:30" or in_regular_session(value)


def percentile(series: pd.Series, ascending: bool = True) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, ascending=ascending, method="average").mul(100.0)


def normalize_volume_and_vwap(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Infer share/lot units by amount-price consistency and quarantine bad VWAP."""
    result = frame.copy()
    raw_volume = pd.to_numeric(result["vol"], errors="coerce")
    amount = pd.to_numeric(result["amount"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    ratio = amount / (raw_volume * close).where((raw_volume > 0) & (close > 0))
    eligible_ratio = ratio.replace([float("inf"), float("-inf")], pd.NA).dropna()
    median_ratio = float(eligible_ratio.median()) if not eligible_ratio.empty else None
    if median_ratio is not None and 50.0 <= median_ratio <= 150.0:
        multiplier, unit = 100.0, "LOT_100_SHARES"
    elif median_ratio is not None and 0.5 <= median_ratio <= 1.5:
        multiplier, unit = 1.0, "SHARE"
    else:
        multiplier, unit = None, "UNKNOWN"
    result["vol_raw"] = raw_volume
    result["volume_unit"] = unit
    result["volume_multiplier"] = multiplier
    normalized_volume = raw_volume * multiplier if multiplier is not None else raw_volume * float("nan")
    calculated_vwap = amount / normalized_volume.where(normalized_volume > 0)
    low = pd.to_numeric(result["low"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    eligible = (amount > 0) & (raw_volume > 0) & (low > 0) & (high > 0)
    valid = eligible & calculated_vwap.between(low * 0.99, high * 1.01, inclusive="both")
    abnormal = eligible & ~valid
    result["vol"] = normalized_volume
    result["vwap_valid"] = valid
    result["vwap"] = calculated_vwap.where(valid)
    eligible_count = int(eligible.sum())
    valid_count = int(valid.sum())
    abnormal_count = int(abnormal.sum())
    audit = {
        "status": "PASS" if eligible_count and valid_count / eligible_count >= 0.95 and abnormal_count / eligible_count <= 0.01 else "FAIL",
        "detected_volume_unit": unit, "volume_multiplier_to_shares": multiplier,
        "median_amount_volume_price_ratio": round(median_ratio, 6) if median_ratio is not None else None,
        "universe_rows": len(result), "eligible_active_rows": eligible_count, "valid_vwap_rows": valid_count,
        "invalid_or_missing_rows": len(result) - valid_count, "abnormal_vwap_rows": abnormal_count,
        "valid_rate_pct": round(valid_count / eligible_count * 100.0, 4) if eligible_count else 0.0,
        "abnormal_rate_pct": round(abnormal_count / eligible_count * 100.0, 4) if eligible_count else 100.0,
        "rule": "Invalid VWAP is NULL and cannot enter Alpha Rank, pullback logic or execution evidence.",
    }
    return result, audit


def write_vwap_audit(audit: dict, meta: dict) -> None:
    payload = dict(audit)
    payload.update(generated_at=iso(), observed_at=meta.get("observed_at"), source=meta.get("source"),
                   is_pit_safe=bool(meta.get("is_pit_safe")), audit_scope="UNIT_AND_RANGE_ONLY")
    atomic_json(VWAP_AUDIT_JSON, payload)
    lines = ["VWAP UNIT AUDIT", "=" * 40, f"Status: {payload['status']}",
             f"Detected volume unit: {payload['detected_volume_unit']}",
             f"Multiplier to shares: {payload['volume_multiplier_to_shares']}",
             f"Eligible active rows: {payload['eligible_active_rows']}",
             f"Valid VWAP rows: {payload['valid_vwap_rows']}",
             f"VWAP valid rate: {payload['valid_rate_pct']}%", f"VWAP abnormal rate: {payload['abnormal_rate_pct']}%",
             f"Observed at: {payload['observed_at']}", f"PIT-safe market sample: {payload['is_pit_safe']}", "", payload["rule"]]
    VWAP_AUDIT_TXT.write_text("\n".join(lines), encoding="utf-8")


class RealtimeProvider:
    """The only raw Tushare access point used by the P0 collector."""

    def __init__(self):
        self.pro = get_pro()

    def is_trade_day(self, value: datetime) -> tuple[bool, str]:
        day = value.strftime("%Y%m%d")
        try:
            frame = self.pro.trade_cal(exchange="", start_date=day, end_date=day)
            if frame is None or frame.empty or "is_open" not in frame.columns:
                return False, "trade_cal returned no identifiable row"
            return bool(int(frame.iloc[0]["is_open"])), "Tushare.trade_cal"
        except Exception as exc:
            return False, f"trade_cal ERROR: {type(exc).__name__}: {str(exc)[:180]}"

    def full_market(self, requested: datetime | None = None) -> tuple[pd.DataFrame, dict]:
        requested_at = requested or now_cn()
        started = time.perf_counter()
        try:
            raw = self.pro.rt_k(ts_code=RT_PATTERN)
        except Exception as exc:
            return pd.DataFrame(), {
                "status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                "requested_at": iso(requested_at), "observed_at": iso(),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "source": "Tushare/dailyfetch.rt_k", "issues": ["NO_FALLBACK"],
                "data_quality": 0.0, "is_pit_safe": False,
            }
        received = now_cn()
        frame = raw.copy() if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
        aliases = {"code": "ts_code", "price": "close", "volume": "vol", "trade_date_time": "trade_time"}
        frame.rename(columns={key: val for key, val in aliases.items() if key in frame.columns}, inplace=True)
        required = ("ts_code", "name", "pre_close", "open", "high", "low", "close", "vol", "amount")
        for column in required:
            if column not in frame.columns:
                frame[column] = None
        frame["ts_code"] = frame["ts_code"].map(normalize_code)
        frame = frame[frame["ts_code"] != ""].drop_duplicates("ts_code", keep="last").copy()
        for column in ("pre_close", "open", "high", "low", "close", "vol", "amount", "num",
                       "bid_price1", "bid_volume1", "ask_price1", "ask_volume1"):
            if column not in frame.columns:
                frame[column] = None
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame, vwap_audit = normalize_volume_and_vwap(frame)
        rows = len(frame)
        coverage = min(1.0, rows / 5000.0) if rows else 0.0
        positive_price = float((frame["close"].fillna(0) > 0).mean()) if rows else 0.0
        nonzero_volume = float((frame["vol"].fillna(0) > 0).mean()) if rows else 0.0
        issues = []
        if rows < 5000:
            issues.append(f"FULL_MARKET_COVERAGE_LOW:{rows}/5000")
        if "trade_time" not in frame.columns:
            issues.append("SOURCE_TRADE_TIME_NOT_RETURNED; using local receipt timestamp")
        if positive_price < 0.98:
            issues.append(f"INVALID_PRICE_RATIO:{1-positive_price:.4f}")
        if requested_at.strftime("%H:%M") == "09:25" and nonzero_volume < 0.30:
            issues.append("AUCTION_0925_RT_K_STALE_OR_UNAVAILABLE")
        status = "OK" if rows >= 5000 and positive_price >= 0.98 else "PARTIAL" if rows else "EMPTY"
        pit_safe = status == "OK" and in_capture_window(received) and not any(item.startswith("AUCTION_0925") for item in issues)
        quality = max(0.0, min(0.95, coverage * positive_price * (0.9 if "trade_time" not in frame.columns else 1.0)))
        source_trade_time = None
        if "trade_time" in frame.columns and frame["trade_time"].notna().any():
            source_trade_time = str(frame["trade_time"].dropna().max())
        meta = {
            "status": status, "error": None, "requested_at": iso(requested_at), "observed_at": iso(received),
            "effective_at": iso(received), "source_trade_time": source_trade_time,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2), "source": "Tushare/dailyfetch.rt_k",
            "row_count": rows, "field_count": len(frame.columns), "coverage_ratio": coverage,
            "positive_price_ratio": positive_price, "nonzero_volume_ratio": nonzero_volume,
            "issues": issues, "data_quality": round(quality, 4), "is_pit_safe": pit_safe,
        }
        meta["vwap_unit_audit"] = vwap_audit
        write_vwap_audit(vwap_audit, meta)
        return frame, meta

    def industry_map(self) -> tuple[dict[str, str], dict]:
        cache_path = DATA_DIR / "p0_stock_basic_cache.json"
        try:
            frame = self.pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry,list_date")
            records = frame.fillna("").to_dict("records") if frame is not None else []
            payload = {"observed_at": iso(), "source": "Tushare.stock_basic", "records": records}
            atomic_json(cache_path, payload)
            return {normalize_code(row.get("ts_code")): str(row.get("industry") or "未分类") for row in records}, {
                "status": "OK", "source": "Tushare.stock_basic", "rows": len(records)}
        except Exception as exc:
            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                records = payload.get("records", [])
                return {normalize_code(row.get("ts_code")): str(row.get("industry") or "未分类") for row in records}, {
                    "status": "CACHE", "source": payload.get("source"), "observed_at": payload.get("observed_at"),
                    "rows": len(records), "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
            return {}, {"status": "ERROR", "rows": 0, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def load_snapshot_before(conn: sqlite3.Connection, trade_date: str, cutoff: datetime) -> tuple[dict, str | None]:
    row = conn.execute(
        "SELECT snapshot_id,observed_at FROM snapshot_manifest WHERE trade_date=? AND observed_at<=? "
        "AND status LIKE 'OK%' AND is_pit_safe=1 ORDER BY observed_at DESC LIMIT 1",
        (trade_date, iso(cutoff)),
    ).fetchone()
    if not row:
        return {}, None
    values = conn.execute(
        "SELECT ts_code,close,high,amount,amount_velocity,pct_chg,vwap FROM intraday_quote WHERE snapshot_id=?",
        (row["snapshot_id"],),
    ).fetchall()
    return {item["ts_code"]: dict(item) for item in values}, row["observed_at"]


def load_sequence_state(conn: sqlite3.Connection, trade_date: str) -> dict[str, dict[str, sqlite3.Row]]:
    state: dict[str, dict[str, sqlite3.Row]] = {}
    for row in conn.execute("SELECT * FROM sequence_event WHERE trade_date=?", (trade_date,)):
        state.setdefault(row["ts_code"], {})[row["event_type"]] = row
    return state


def build_intraday_features(conn: sqlite3.Connection, frame: pd.DataFrame, meta: dict) -> tuple[list[dict], list[dict], list[dict]]:
    observed = datetime.fromisoformat(meta["observed_at"])
    trade_date = observed.strftime("%Y%m%d")
    current = frame.copy()
    if "industry" not in current.columns:
        current["industry"] = "未分类"
    current["industry"] = current["industry"].fillna("未分类")
    current["pct_chg"] = (current["close"] / current["pre_close"] - 1.0) * 100.0
    calculated_vwap = current["amount"] / current["vol"].where(current["vol"] > 0)
    current["vwap"] = calculated_vwap.where(current["vwap_valid"].fillna(False)) if "vwap_valid" in current.columns else calculated_vwap
    current["vwap_strength"] = (current["close"] / current["vwap"] - 1.0) * 100.0
    previous, previous_at = load_snapshot_before(conn, trade_date, observed - timedelta(seconds=1))
    previous_seconds = max(1.0, (observed - datetime.fromisoformat(previous_at)).total_seconds()) if previous_at else 60.0
    previous_amount = current["ts_code"].map({code: row.get("amount") for code, row in previous.items()})
    previous_velocity = current["ts_code"].map({code: row.get("amount_velocity") for code, row in previous.items()})
    current["amount_delta"] = (current["amount"] - previous_amount).clip(lower=0)
    current["amount_velocity"] = current["amount_delta"] / previous_seconds
    current["amount_acceleration"] = (current["amount_velocity"] / previous_velocity.where(previous_velocity > 0) - 1.0).clip(lower=-10, upper=20)
    available_horizons = 0
    for minutes in (5, 15, 30, 60):
        anchor, _ = load_snapshot_before(conn, trade_date, observed - timedelta(minutes=minutes))
        old_close = current["ts_code"].map({code: row.get("close") for code, row in anchor.items()})
        current[f"return_{minutes}m"] = (current["close"] / old_close - 1.0) * 100.0
        if old_close.notna().any():
            available_horizons += 1
    parts = [(current[f"return_{minutes}m"] > 0).astype(float).where(current[f"return_{minutes}m"].notna()) for minutes in (5, 15, 30, 60)]
    current["persistence_score"] = pd.concat(parts, axis=1).mean(axis=1, skipna=True) * 100.0
    current["market_rank"] = current["pct_chg"].rank(ascending=False, method="min").fillna(len(current) + 1).astype(int)
    current["current_top20"] = current["market_rank"] <= 20
    current["current_top50"] = current["market_rank"] <= 50
    current["price_strength_rank"] = percentile(current["pct_chg"])
    current["volume_acceleration_rank"] = percentile(current["amount_velocity"])
    current["amount_acceleration_rank"] = percentile(current["amount_acceleration"])
    current["vwap_strength_rank"] = percentile(current["vwap_strength"])
    current["persistence_rank"] = percentile(current["persistence_score"])
    current["liquidity_rank"] = percentile(current["amount"])
    current["relative_strength_rank"] = current["price_strength_rank"]
    grouped = current.groupby("industry", dropna=False)
    sector = grouped.agg(stock_count=("ts_code", "count"), up_count=("pct_chg", lambda x: int((x > 0).sum())),
                         median_pct=("pct_chg", "median"), amount_velocity=("amount_velocity", "sum")).reset_index()
    sector["up_ratio"] = sector["up_count"] / sector["stock_count"].where(sector["stock_count"] > 0)
    sector["diffusion_rank"] = percentile(sector["up_ratio"] * 0.7 + percentile(sector["median_pct"]) / 100.0 * 0.3)
    sector_map = sector.set_index("industry").to_dict("index")
    current["sector_up_ratio"] = current["industry"].map({key: val["up_ratio"] for key, val in sector_map.items()})
    current["sector_median_pct"] = current["industry"].map({key: val["median_pct"] for key, val in sector_map.items()})
    current["sector_amount_velocity"] = current["industry"].map({key: val["amount_velocity"] for key, val in sector_map.items()})
    current["sector_diffusion_rank"] = current["industry"].map({key: val["diffusion_rank"] for key, val in sector_map.items()})
    current["sector_strength_rank"] = current["sector_diffusion_rank"]
    event_state = load_sequence_state(conn, trade_date)
    previous_high = {code: row.get("high") for code, row in previous.items()}
    events: list[dict] = []
    sequence_states, breakout_times, pullback_times = [], [], []
    for record in current.to_dict("records"):
        code = record["ts_code"]
        state = event_state.get(code, {})
        close, vwap, prior_high = float(record.get("close") or 0), float(record.get("vwap") or 0), float(previous_high.get(code) or 0)
        breakout, pullback, repair = state.get("FIRST_BREAKOUT"), state.get("FIRST_VALID_PULLBACK"), state.get("REPAIR_CONFIRMED")
        if not breakout and prior_high > 0 and close >= prior_high * 1.001 and float(record.get("pct_chg") or 0) > 0:
            breakout = {"event_type": "FIRST_BREAKOUT", "ts_code": code, "price": close, "reference_price": prior_high, "vwap": vwap}
            events.append(breakout)
        breakout_price = float((breakout["price"] if isinstance(breakout, sqlite3.Row) else breakout.get("price")) or 0) if breakout else 0
        if breakout and not pullback and breakout_price > 0 and close <= breakout_price * 0.995 and close >= vwap > 0:
            pullback = {"event_type": "FIRST_VALID_PULLBACK", "ts_code": code, "price": close, "reference_price": breakout_price, "vwap": vwap}
            events.append(pullback)
        if pullback and not repair and breakout_price > 0 and close >= breakout_price:
            repair = {"event_type": "REPAIR_CONFIRMED", "ts_code": code, "price": close, "reference_price": breakout_price, "vwap": vwap}
            events.append(repair)
        sequence_states.append("REPAIR_CONFIRMED" if repair else "PULLBACK_WAIT_REPAIR" if pullback else "BREAKOUT_WAIT_PULLBACK" if breakout else "WAIT_FIRST_BREAKOUT")
        breakout_times.append((breakout["observed_at"] if isinstance(breakout, sqlite3.Row) else meta["observed_at"]) if breakout else None)
        pullback_times.append((pullback["observed_at"] if isinstance(pullback, sqlite3.Row) else meta["observed_at"]) if pullback else None)
    current["sequence_state"], current["breakout_at"], current["pullback_at"] = sequence_states, breakout_times, pullback_times
    current["price"], current["daily_pct"], current["sector"] = current["close"], current["pct_chg"], current["industry"]
    current["sector_strength"], current["relative_strength"] = current["sector_diffusion_rank"], current["price_strength_rank"]
    current["breakout_quality_rank"] = percentile(current["close"] / current["high"].where(current["high"] > 0))
    current["active_buy_rank"], current["auction_surprise_rank"], current["event_surprise_rank"] = None, None, None
    current["intraday_ready"] = bool(meta.get("is_pit_safe")) and available_horizons >= 1
    current["discovery_mode"] = "INTRADAY_PIT" if bool(current["intraday_ready"].any()) else "INTRADAY_WARMUP"
    current["source"], current["data_quality"], current["is_pit_safe"] = meta["source"], meta["data_quality"], bool(meta["is_pit_safe"])
    current["observed_at"], current["effective_at"], current["trade_date"] = meta["observed_at"], meta["effective_at"], trade_date
    records = [{key: clean_value(value) for key, value in row.items()} for row in current.to_dict("records")]
    sectors = [{key: clean_value(value) for key, value in row.items()} for row in sector.to_dict("records")]
    return records, sectors, events


def apply_existing_discovery(records: list[dict], meta: dict) -> tuple[list[dict], dict, str | None]:
    try:
        from winner_discovery import discover
        payload = discover(records, decision_time=meta["observed_at"], trade_date=meta["observed_at"][:10].replace("-", ""), market_data_date=meta["observed_at"][:10].replace("-", ""))
        if isinstance(payload, dict) and isinstance(payload.get("candidates"), list):
            records = payload["candidates"]
            summary = payload.get("summary") or {}
        else:
            summary = payload if isinstance(payload, dict) else {}
        for row in records:
            row["discovery_mode"] = "INTRADAY_PIT" if row.get("intraday_ready") else "INTRADAY_WARMUP"
        return records, summary, None
    except Exception as exc:
        return records, {}, f"{type(exc).__name__}: {str(exc)[:400]}"


def discovery_labels(record: dict) -> list[str]:
    found: set[str] = set()
    values = [record.get("winner_type"), record.get("discovery_pools")]
    for value in values:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            text = str(item or "").upper()
            for label in DISCOVERY_TYPES:
                if label in text:
                    found.add(label)
    for label in DISCOVERY_TYPES:
        if record.get(label) or record.get(label.lower()):
            found.add(label)
    return sorted(found)


def write_discovery_audit(conn: sqlite3.Connection, snapshot_id: str, records: list[dict], meta: dict) -> dict:
    expected = {label: sum(label in discovery_labels(row) for row in records) for label in DISCOVERY_TYPES}
    stored = {label: 0 for label in DISCOVERY_TYPES}
    for row in conn.execute("SELECT discovery_type,COUNT(*) n FROM discovery_observation WHERE snapshot_id=? GROUP BY discovery_type", (snapshot_id,)):
        stored[row["discovery_type"]] = int(row["n"])
    mismatch = {label: {"expected": expected[label], "stored": stored[label]} for label in DISCOVERY_TYPES if expected[label] != stored[label]}
    realtime_rule = expected["REALTIME_WINNERS"] == 0 or stored["REALTIME_WINNERS"] > 0
    payload = {"status": "PASS" if not mismatch and realtime_rule else "FAIL", "generated_at": iso(),
               "snapshot_id": snapshot_id, "observed_at": meta.get("observed_at"),
               "mode": "INTRADAY_PIT" if meta.get("is_pit_safe") else "OFF_HOURS_TEST",
               "expected": expected, "stored": stored, "mismatch": mismatch,
               "rule_realtime_nonzero_written_nonzero": realtime_rule,
               "source": "winner_discovery returned candidates -> discovery_observation",
               "no_demo_substitution": True}
    atomic_json(DISCOVERY_AUDIT_JSON, payload)
    lines = ["DISCOVERY WRITE AUDIT", "=" * 44, f"Status: {payload['status']}",
             f"Snapshot: {snapshot_id}", f"Mode: {payload['mode']}"]
    lines.extend(f"{label}: expected={expected[label]}, stored={stored[label]}" for label in DISCOVERY_TYPES)
    lines += [f"REALTIME_WINNERS nonzero write rule: {realtime_rule}", "No DEMO substitution: True"]
    DISCOVERY_AUDIT_TXT.write_text("\n".join(lines), encoding="utf-8")
    return payload


def choose_checkpoint(conn: sqlite3.Connection, observed: datetime, interval: int) -> str | None:
    trade_date = observed.strftime("%Y%m%d")
    for label in CHECKPOINTS:
        target = datetime.combine(observed.date(), datetime.strptime(label, "%H:%M").time(), tzinfo=CN_TZ)
        if 0 <= (observed - target).total_seconds() <= max(90, interval + 20):
            exists = conn.execute("SELECT 1 FROM snapshot_manifest WHERE trade_date=? AND mandatory_label=? AND status LIKE 'OK%' LIMIT 1", (trade_date, label)).fetchone()
            if not exists:
                return label
    return None


def insert_snapshot(conn: sqlite3.Connection, records: list[dict], sectors: list[dict], events: list[dict], meta: dict,
                    checkpoint: str | None, discovery_error: str | None) -> str:
    snapshot_id = f"rt-{meta['observed_at'].replace(':','').replace('-','')}-{uuid.uuid4().hex[:8]}"
    observed_at, trade_date = meta["observed_at"], meta["observed_at"][:10].replace("-", "")
    mode = "INTRADAY_PIT" if meta.get("is_pit_safe") else "OFF_HOURS_TEST"
    status = "OK" if meta.get("status") == "OK" else str(meta.get("status") or "ERROR")
    if discovery_error and status == "OK":
        status = "OK_DISCOVERY_ERROR"
    issues = list(meta.get("issues") or [])
    if discovery_error:
        issues.append("DISCOVERY_ERROR:" + discovery_error)
    quote_sql = "INSERT INTO intraday_quote(" + ",".join(QUOTE_COLUMNS) + ") VALUES(" + ",".join("?" for _ in QUOTE_COLUMNS) + ")"
    values = []
    for row in records:
        payload = dict(row)
        payload.update(snapshot_id=snapshot_id, trade_date=trade_date, observed_at=observed_at, effective_at=meta["effective_at"], source=meta["source"], data_quality=meta["data_quality"], is_pit_safe=int(bool(meta.get("is_pit_safe"))))
        values.append(tuple(clean_value(payload.get(column)) for column in QUOTE_COLUMNS))
    with conn:
        conn.execute("INSERT INTO snapshot_manifest(snapshot_id,trade_date,observed_at,effective_at,source,source_trade_time,mode,mandatory_label,status,row_count,field_count,latency_ms,coverage_ratio,data_quality,is_pit_safe,issues_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (snapshot_id, trade_date, observed_at, meta.get("effective_at"), meta["source"], meta.get("source_trade_time"), mode, checkpoint, status, len(records), meta.get("field_count", 0), meta.get("latency_ms"), meta.get("coverage_ratio"), meta.get("data_quality"), int(bool(meta.get("is_pit_safe"))), json.dumps(issues, ensure_ascii=False), meta.get("error"), iso()))
        conn.executemany(quote_sql, values)
        for row in sectors:
            conn.execute("INSERT INTO sector_realtime_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (snapshot_id, trade_date, observed_at, row.get("industry") or "未分类", row.get("stock_count") or 0, row.get("up_count") or 0, row.get("up_ratio"), row.get("median_pct"), row.get("amount_velocity"), row.get("diffusion_rank"), meta["source"] + "+stock_basic.industry", meta["data_quality"], int(bool(meta.get("is_pit_safe")))))
        for event in events:
            conn.execute("INSERT OR IGNORE INTO sequence_event VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, trade_date, event["ts_code"], event["event_type"], observed_at, event.get("price"), event.get("reference_price"), event.get("vwap"), snapshot_id, meta["source"], int(bool(meta.get("is_pit_safe")))))
        for row in records:
            for label in discovery_labels(row):
                evidence = {key: row.get(key) for key in ("amount_acceleration_rank", "vwap_strength_rank", "persistence_rank", "sector_diffusion_rank", "sequence_state")}
                conn.execute("INSERT OR IGNORE INTO discovery_observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (snapshot_id, trade_date, observed_at, row["ts_code"], label, row.get("market_alpha_score"), row.get("pool"), row.get("close"), row.get("pct_chg"), row.get("high"), row.get("vwap"), json.dumps(evidence, ensure_ascii=False), meta["source"], meta["data_quality"], int(bool(meta.get("is_pit_safe")))))
    return snapshot_id


def insert_failure(conn: sqlite3.Connection, meta: dict, checkpoint: str | None = None) -> str:
    snapshot_id = "failed-" + uuid.uuid4().hex
    observed_at = meta.get("observed_at") or iso()
    with conn:
        conn.execute("INSERT INTO snapshot_manifest(snapshot_id,trade_date,observed_at,effective_at,source,mode,mandatory_label,status,row_count,field_count,latency_ms,coverage_ratio,data_quality,is_pit_safe,issues_json,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (snapshot_id, observed_at[:10].replace("-", ""), observed_at, meta.get("effective_at"), meta.get("source", "Tushare/dailyfetch.rt_k"), "INTRADAY_PIT", checkpoint, meta.get("status", "ERROR"), 0, 0, meta.get("latency_ms"), 0.0, 0.0, 0, json.dumps(meta.get("issues") or ["NO_FALLBACK"], ensure_ascii=False), meta.get("error"), iso()))
    return snapshot_id


def collect_once(interval: int = 60, allow_offhours: bool = False, provider: RealtimeProvider | None = None, db_path: Path = DB_PATH) -> dict:
    interval = max(30, min(60, int(interval)))
    observed, provider = now_cn(), provider or RealtimeProvider()
    conn = db_connect(db_path)
    checkpoint = choose_checkpoint(conn, observed, interval)
    if not allow_offhours and not in_capture_window(observed):
        result = {"status": "WAITING_MARKET_SESSION", "observed_at": iso(observed), "next_action": "collector remains active", "mode": "INTRADAY_PIT", "interval_seconds": interval}
        atomic_json(STATUS_PATH, result); conn.close(); return result
    trade_day, calendar_source = provider.is_trade_day(observed)
    if not trade_day and not allow_offhours:
        result = {"status": "MARKET_CLOSED_OR_CALENDAR_ERROR", "observed_at": iso(observed), "calendar": calendar_source, "mode": "INTRADAY_PIT", "interval_seconds": interval}
        atomic_json(STATUS_PATH, result); conn.close(); return result
    frame, meta = provider.full_market(observed)
    meta["is_pit_safe"] = bool(meta.get("is_pit_safe")) and not allow_offhours
    if frame.empty or meta.get("status") not in {"OK", "PARTIAL"}:
        snapshot_id = insert_failure(conn, meta, checkpoint)
        result = {**meta, "snapshot_id": snapshot_id, "mode": "INTRADAY_PIT", "checkpoint": checkpoint, "calendar": calendar_source, "no_silent_fallback": True}
        atomic_json(STATUS_PATH, result); conn.close(); return result
    industries, industry_status = provider.industry_map()
    frame["industry"] = frame["ts_code"].map(industries).fillna("未分类")
    records, sectors, events = build_intraday_features(conn, frame, meta)
    records, discovery_summary, discovery_error = apply_existing_discovery(records, meta)
    snapshot_id = insert_snapshot(conn, records, sectors, events, meta, checkpoint, discovery_error)
    discovery_audit = write_discovery_audit(conn, snapshot_id, records, meta)
    label_counts = {label: sum(label in discovery_labels(row) for row in records) for label in DISCOVERY_TYPES}
    result = {"status": "OK" if meta.get("status") == "OK" else meta.get("status"), "snapshot_id": snapshot_id,
              "mode": "INTRADAY_PIT" if meta.get("is_pit_safe") else "OFF_HOURS_TEST", "checkpoint": checkpoint,
              "observed_at": meta["observed_at"], "source": meta["source"], "rows": len(records), "sectors": len(sectors),
              "events_added": len(events), "discovery_counts": label_counts, "discovery_error": discovery_error,
              "discovery_summary": discovery_summary, "discovery_write_audit": discovery_audit,
              "vwap_unit_audit": meta.get("vwap_unit_audit"), "industry_map": industry_status, "data_quality": meta["data_quality"],
              "is_pit_safe": meta["is_pit_safe"], "issues": meta.get("issues", []), "calendar": calendar_source,
              "interval_seconds": interval, "no_silent_fallback": True,
              "safety": ["NO_TRADING", "NO_PARAMETER_CHANGE", "NO_MODEL_TRAINING", "NO_A_PLUS_AUTO_ENABLE"]}
    atomic_json(STATUS_PATH, result); conn.close(); return result


def bootstrap_auction_baseline(days: int = 20, provider: RealtimeProvider | None = None, db_path: Path = DB_PATH) -> dict:
    provider, conn, current = provider or RealtimeProvider(), db_connect(db_path), now_cn()
    start, end = (current - timedelta(days=max(45, days * 3))).strftime("%Y%m%d"), (current - timedelta(days=1)).strftime("%Y%m%d")
    errors, inserted = [], 0
    try:
        calendar = provider.pro.trade_cal(exchange="", start_date=start, end_date=end, is_open="1")
        open_dates = sorted(str(value) for value in calendar["cal_date"].tolist())[-days:]
    except Exception as exc:
        open_dates = []; errors.append(f"trade_cal: {type(exc).__name__}: {str(exc)[:240]}")
    completed_dates = []
    for trade_date in open_dates:
        existing = conn.execute("SELECT COUNT(*) FROM auction_daily_baseline WHERE trade_date=?", (trade_date,)).fetchone()[0]
        if existing >= 4500:
            completed_dates.append(trade_date); continue
        try:
            frame = provider.pro.stk_auction_o(trade_date=trade_date)
            if frame is None or frame.empty:
                errors.append(f"{trade_date}: EMPTY"); continue
            observed_at, values = iso(), []
            for row in frame.to_dict("records"):
                code = normalize_code(row.get("ts_code"))
                if code:
                    values.append((trade_date, code, row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("vol"), row.get("amount"), row.get("vwap"), "Tushare.stk_auction_o", observed_at, observed_at, 1.0))
            with conn:
                before = conn.total_changes
                conn.executemany("INSERT OR IGNORE INTO auction_daily_baseline VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
                inserted += conn.total_changes - before
            if len(values) >= 4500:
                completed_dates.append(trade_date)
            else:
                errors.append(f"{trade_date}: COVERAGE_LOW {len(values)}")
        except Exception as exc:
            errors.append(f"{trade_date}: {type(exc).__name__}: {str(exc)[:240]}")
    result = {"status": "READY" if len(completed_dates) >= days else "INCOMPLETE", "requested_days": days,
              "completed_dates": len(completed_dates), "dates": completed_dates, "inserted_rows": inserted,
              "database_dates": conn.execute("SELECT COUNT(DISTINCT trade_date) FROM auction_daily_baseline").fetchone()[0],
              "database_rows": conn.execute("SELECT COUNT(*) FROM auction_daily_baseline").fetchone()[0], "errors": errors,
              "observed_at": iso(), "source": "Tushare.stk_auction_o (historical, available only after close)",
              "pit_rule": "May be used only for decisions after available_from; never backfilled into old decisions."}
    atomic_json(AUCTION_STATUS_PATH, result); conn.close(); return result


def acquire_lock() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii")); return fd
    except FileExistsError as exc:
        raise RuntimeError(f"collector already active or stale lock exists: {LOCK_PATH}") from exc


def release_lock(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def daemon(interval: int) -> None:
    interval = max(30, min(60, int(interval)))
    fd, last_capture, baseline_day, eod_day, provider = acquire_lock(), 0.0, None, None, RealtimeProvider()
    try:
        while True:
            current, day, hhmm = now_cn(), now_cn().strftime("%Y%m%d"), now_cn().strftime("%H:%M")
            if baseline_day != day and (hhmm < "09:10" or hhmm > "15:10"):
                bootstrap_auction_baseline(20, provider); baseline_day = day
            if in_capture_window(current) and time.monotonic() - last_capture >= interval:
                collect_once(interval, False, provider); last_capture = time.monotonic()
            if eod_day != day and "15:08" <= hhmm <= "15:30":
                try:
                    from p0_outcomes import update_signal_outcomes
                    from p0_acceptance import generate_acceptance
                    update_signal_outcomes(max_fetch_dates=10); generate_acceptance(day)
                except Exception as exc:
                    atomic_json(STATUS_PATH, {"status": "EOD_ERROR", "error": f"{type(exc).__name__}: {str(exc)[:400]}", "observed_at": iso()})
                eod_day = day
            time.sleep(5)
    finally:
        release_lock(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 full-market intraday PIT collector")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-offhours", action="store_true", help="probe only; stored as non-PIT test")
    parser.add_argument("--bootstrap-auction", action="store_true")
    parser.add_argument("--interval", type=int, default=int(os.getenv("P0_SNAPSHOT_INTERVAL_SECONDS", "60")))
    args = parser.parse_args()
    if args.bootstrap_auction:
        print(json.dumps(bootstrap_auction_baseline(20), ensure_ascii=False, indent=2)); return 0
    if args.once:
        print(json.dumps(collect_once(args.interval, args.allow_offhours), ensure_ascii=False, indent=2, default=str)); return 0
    daemon(args.interval); return 0


if __name__ == "__main__":
    raise SystemExit(main())
