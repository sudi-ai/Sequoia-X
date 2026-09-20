# -*- coding: utf-8 -*-
"""真实历史 PIT 快照库。

PIT 表只追加、不覆盖。缓存表可以更新，但缓存永远不能作为历史决策时刻的
替代品。全市场快照只用于发现层；策略胜率训练必须从真实交易信号
signal_snapshot 关联 signal_outcome 后再开始。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR

DEFAULT_DB = DATA_DIR / "live_pit.db"
SNAPSHOT_TABLES = {
    "market_daily", "stock_daily", "daily_basic", "auction_snapshot", "moneyflow",
    "limitup_pool", "sector_snapshot", "chip_snapshot", "risk_event",
}
ALL_TABLES = SNAPSHOT_TABLES | {"signal_snapshot", "signal_outcome"}
COMMON_COLUMNS = """
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    trade_date TEXT,
    ts_code TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    effective_at TEXT,
    data_quality REAL NOT NULL,
    is_pit_safe INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
"""
SCHEMA = "\n".join([
    *(f"CREATE TABLE IF NOT EXISTS {name} ({COMMON_COLUMNS});" for name in sorted(SNAPSHOT_TABLES)),
    """
    CREATE TABLE IF NOT EXISTS signal_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id TEXT NOT NULL UNIQUE,
        trade_date TEXT NOT NULL,
        decision_time TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        price REAL,
        score REAL,
        pool TEXT,
        market_phase TEXT,
        sector TEXT,
        auction_quality REAL,
        main_flow_score REAL,
        chip_lock_score REAL,
        trend_score REAL,
        buy_timing_score REAL,
        rr REAL,
        event_risk REAL,
        distribution_risk REAL,
        data_quality REAL NOT NULL,
        source TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        effective_at TEXT,
        is_pit_safe INTEGER NOT NULL,
        signal_type TEXT,
        sample_domain TEXT NOT NULL DEFAULT 'TRADE_SIGNAL',
        is_trade_signal INTEGER NOT NULL DEFAULT 1,
        features_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS signal_outcome (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_snapshot_id TEXT NOT NULL,
        outcome_time TEXT NOT NULL,
        t1 REAL,
        t3 REAL,
        t5 REAL,
        max_favorable_excursion REAL,
        max_adverse_excursion REAL,
        triple_barrier_label INTEGER,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(signal_snapshot_id) REFERENCES signal_snapshot(snapshot_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS api_cache (
        cache_key TEXT PRIMARY KEY,
        endpoint TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        stored_at TEXT NOT NULL,
        expires_at REAL NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_signal_date_code ON signal_snapshot(trade_date, ts_code);",
    "CREATE INDEX IF NOT EXISTS ix_outcome_signal ON signal_outcome(signal_snapshot_id);",
])


def _now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PITStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript(SCHEMA)
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(signal_snapshot)")}
            migrations = {
                "signal_type": "TEXT",
                "sample_domain": "TEXT",
                "is_trade_signal": "INTEGER",
            }
            for column, definition in migrations.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE signal_snapshot ADD COLUMN {column} {definition}")

    def append_frame(self, table, frame):
        if table not in SNAPSHOT_TABLES or frame is None or frame.empty:
            return 0
        inserted = 0
        now = _now()
        with self.lock, closing(sqlite3.connect(self.path)) as conn, conn:
            for raw in frame.to_dict("records"):
                payload = {key: value for key, value in raw.items() if key not in {"source", "observed_at", "effective_at", "data_quality", "is_pit_safe"}}
                conn.execute(
                    f"INSERT INTO {table}(snapshot_id,trade_date,ts_code,source,observed_at,effective_at,data_quality,is_pit_safe,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, str(raw.get("trade_date", "")), str(raw.get("ts_code", "")), str(raw.get("source", "unknown")), str(raw.get("observed_at", now)), str(raw.get("effective_at", "")), float(raw.get("data_quality", 0.0) or 0.0), int(bool(raw.get("is_pit_safe", False))), json.dumps(payload, ensure_ascii=False, default=str), now),
                )
                inserted += 1
        return inserted

    def append_signal_snapshot(self, signal):
        from sample_domains import SIGNAL_SAMPLE_DOMAIN, is_trade_signal_record

        required = ("trade_date", "decision_time", "ts_code", "data_quality", "source", "observed_at")
        missing = [key for key in required if signal.get(key) in (None, "")]
        if missing:
            raise ValueError("signal_snapshot 缺少字段: " + ",".join(missing))
        if not is_trade_signal_record(signal):
            raise ValueError("signal_snapshot 只接收真实 A/B/A+ 或 Primary Signal；普通全市场观察必须进入发现样本库")
        snapshot_id = str(signal.get("snapshot_id") or uuid.uuid4().hex)
        columns = [
            "snapshot_id", "trade_date", "decision_time", "ts_code", "name", "price", "score", "pool",
            "market_phase", "sector", "auction_quality", "main_flow_score", "chip_lock_score", "trend_score",
            "buy_timing_score", "rr", "event_risk", "distribution_risk", "data_quality", "source",
            "observed_at", "effective_at", "is_pit_safe", "signal_type", "sample_domain", "is_trade_signal",
            "features_json", "created_at",
        ]
        known = set(columns) | {"snapshot_id"}
        values = dict(signal)
        values["snapshot_id"] = snapshot_id
        values["is_pit_safe"] = int(bool(signal.get("is_pit_safe", False)))
        values["signal_type"] = str(signal.get("signal_type") or "PRIMARY_SIGNAL")
        values["sample_domain"] = SIGNAL_SAMPLE_DOMAIN
        values["is_trade_signal"] = 1
        values["features_json"] = json.dumps({k: v for k, v in signal.items() if k not in known}, ensure_ascii=False, default=str)
        values["created_at"] = _now()
        with self.lock, closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO signal_snapshot(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
                [values.get(column) for column in columns],
            )
        return snapshot_id

    def append_signal_outcome(self, outcome):
        required = ("signal_snapshot_id", "outcome_time")
        missing = [key for key in required if outcome.get(key) in (None, "")]
        if missing:
            raise ValueError("signal_outcome 缺少字段: " + ",".join(missing))
        fields = ("t1", "t3", "t5", "max_favorable_excursion", "max_adverse_excursion", "triple_barrier_label")
        payload = {k: v for k, v in outcome.items() if k not in set(fields) | set(required)}
        with self.lock, closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO signal_outcome(signal_snapshot_id,outcome_time,t1,t3,t5,max_favorable_excursion,max_adverse_excursion,triple_barrier_label,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [outcome.get("signal_snapshot_id"), outcome.get("outcome_time"), *(outcome.get(f) for f in fields), json.dumps(payload, ensure_ascii=False, default=str), _now()],
            )

    def cache_get(self, cache_key):
        import time

        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute("SELECT payload_json,expires_at FROM api_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if not row or float(row[1]) < time.time():
            return None
        return json.loads(row[0])

    def cache_set(self, cache_key, endpoint, records, ttl_seconds=300):
        import time

        with self.lock, closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute(
                "INSERT INTO api_cache(cache_key,endpoint,payload_json,stored_at,expires_at) VALUES(?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload_json=excluded.payload_json,stored_at=excluded.stored_at,expires_at=excluded.expires_at",
                (cache_key, endpoint, json.dumps(records, ensure_ascii=False, default=str), _now(), time.time() + float(ttl_seconds)),
            )

    def counts(self):
        with closing(sqlite3.connect(self.path)) as conn:
            return {table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in sorted(ALL_TABLES)}
