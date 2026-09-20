from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT_DIR / "data_v7" / "signal_lab.sqlite3"
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    engine TEXT NOT NULL,
    strategy_version TEXT,
    signal_time TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    price REAL,
    grade TEXT,
    score REAL,
    source TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_events_trade_date ON signal_events(trade_date);
CREATE INDEX IF NOT EXISTS idx_signal_events_engine_code ON signal_events(engine, code);
CREATE INDEX IF NOT EXISTS idx_signal_events_version ON signal_events(strategy_version);

CREATE TABLE IF NOT EXISTS signal_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    observed_date TEXT NOT NULL,
    price REAL NOT NULL,
    source TEXT,
    UNIQUE(signal_id, observed_at),
    FOREIGN KEY(signal_id) REFERENCES signal_events(id)
);
CREATE INDEX IF NOT EXISTS idx_signal_obs_signal_date ON signal_observations(signal_id, observed_date);

CREATE TABLE IF NOT EXISTS daily_prices (
    code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    source TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_prices_date ON daily_prices(trade_date);


CREATE TABLE IF NOT EXISTS minute_prices (
    code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    source TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(code, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_minute_prices_code_date ON minute_prices(code, trade_date, observed_at);

CREATE TABLE IF NOT EXISTS data_quality_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    source TEXT,
    requested_at TEXT NOT NULL,
    received_at TEXT,
    requested_trade_date TEXT,
    data_trade_date TEXT,
    expected_rows INTEGER,
    row_count INTEGER,
    coverage_ratio REAL,
    is_complete INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_data_quality_date ON data_quality_runs(requested_trade_date,dataset);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    horizon TEXT NOT NULL,
    observed_date TEXT,
    close_return_pct REAL,
    max_return_pct REAL,
    min_return_pct REAL,
    max_drawdown_pct REAL,
    net_return_pct REAL,
    strategy_return_pct REAL,
    exit_reason TEXT,
    exit_date TEXT,
    reward_risk_ratio REAL,
    stop_hit INTEGER,
    target1_hit INTEGER,
    target2_hit INTEGER,
    first_stop_date TEXT,
    first_target1_date TEXT,
    first_target2_date TEXT,
    first_trigger TEXT,
    ambiguous_same_day INTEGER,
    source TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(signal_id, horizon),
    FOREIGN KEY(signal_id) REFERENCES signal_events(id)
);
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_horizon ON signal_outcomes(horizon);

CREATE TABLE IF NOT EXISTS v71_candidate_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT,
    state TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    v66_score REAL,
    v71_risk_score REAL,
    v71_quality_score REAL,
    next_day_potential_score REAL,
    potential_label TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    reason_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(trade_date, code, strategy_version)
);
CREATE INDEX IF NOT EXISTS idx_v71_states_date_state ON v71_candidate_states(trade_date,state);

CREATE TABLE IF NOT EXISTS v71_state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    reason_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(candidate_id) REFERENCES v71_candidate_states(id)
);
CREATE INDEX IF NOT EXISTS idx_v71_history_candidate ON v71_state_history(candidate_id,changed_at);


CREATE TABLE IF NOT EXISTS v72_research_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER, code TEXT NOT NULL, observed_at TEXT NOT NULL,
 v66_score REAL, current_v71_score REAL, research_2000_score REAL, feature_json TEXT NOT NULL DEFAULT '{}',
 research_json TEXT NOT NULL DEFAULT '{}', research_rule_version TEXT, research_feature_version TEXT,
 research_sample_version TEXT, research_config_hash TEXT, UNIQUE(code,observed_at));
CREATE INDEX IF NOT EXISTS idx_v72_research_code_time ON v72_research_snapshots(code,observed_at);
CREATE TABLE IF NOT EXISTS v72_episodes (
 episode_id TEXT PRIMARY KEY, code TEXT NOT NULL, episode_start_date TEXT NOT NULL, dedupe_key TEXT NOT NULL,
 signal_type TEXT NOT NULL, parent_episode_id TEXT, status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v72_trades (
 trade_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, entry_attempt_no INTEGER NOT NULL, signal_type TEXT NOT NULL,
 entry_time TEXT, entry_price REAL, exit_time TEXT, exit_price REAL, status TEXT NOT NULL, realized_return_pct REAL,
 FOREIGN KEY(episode_id) REFERENCES v72_episodes(episode_id));

CREATE TABLE IF NOT EXISTS v71_push_dedupe (
    trade_date TEXT NOT NULL,
    code TEXT NOT NULL,
    state TEXT NOT NULL,
    pushed_at TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    PRIMARY KEY(trade_date, code, state)
);
"""

_MIGRATIONS = {
    "signal_events": {
        "strategy_version": "TEXT",
    },
    "v71_push_dedupe": {
        "success": "INTEGER NOT NULL DEFAULT 0",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "last_attempt_at": "TEXT",
        "last_error": "TEXT",
    },
    "signal_outcomes": {
        "net_return_pct": "REAL",
        "strategy_return_pct": "REAL",
        "exit_reason": "TEXT",
        "exit_date": "TEXT",
        "reward_risk_ratio": "REAL",
        "first_stop_date": "TEXT",
        "first_target1_date": "TEXT",
        "first_target2_date": "TEXT",
        "first_trigger": "TEXT",
        "ambiguous_same_day": "INTEGER",
    },
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _connect(db_path: Path = DEFAULT_DB):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _ensure_columns(conn)
    return conn


def initialize(db_path: Path = DEFAULT_DB) -> str:
    with _LOCK:
        conn = _connect(db_path)
        conn.commit()
        conn.close()
    return str(db_path)


def record_signal(
    *, engine: str, code: str, signal_time: Optional[str] = None, name: str = "", sector: str = "",
    price: Optional[float] = None, grade: str = "", score: Optional[float] = None, source: str = "",
    snapshot: Optional[Mapping[str, Any]] = None, evidence: Optional[Mapping[str, Any]] = None,
    event_key: Optional[str] = None, strategy_version: str = "", db_path: Path = DEFAULT_DB,
) -> dict[str, Any]:
    stamp = signal_time or datetime.now().astimezone().isoformat(timespec="seconds")
    trade_date = stamp[:10]
    clean_code = str(code or "").strip().split(".")[0].zfill(6)
    if not event_key:
        event_key = f"{engine}|{strategy_version}|{trade_date}|{clean_code}|{source}|{grade}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = (
        event_key, engine, strategy_version, stamp, trade_date, clean_code, name, sector,
        float(price) if price not in (None, "") else None,
        grade, float(score) if score not in (None, "") else None, source,
        json.dumps(snapshot or {}, ensure_ascii=False, default=str),
        json.dumps(evidence or {}, ensure_ascii=False, default=str), now,
    )
    with _LOCK:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO signal_events
                (event_key,engine,strategy_version,signal_time,trade_date,code,name,sector,price,grade,score,source,snapshot_json,evidence_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
            inserted = cur.rowcount == 1
            row = conn.execute("SELECT id FROM signal_events WHERE event_key=?", (event_key,)).fetchone()
            conn.commit()
            return {"id": int(row["id"]), "inserted": bool(inserted), "event_key": event_key}
        finally:
            conn.close()


def record_data_quality(*, dataset: str, source: str, requested_at: str, received_at: str | None,
                        requested_trade_date: str | None, data_trade_date: str | None,
                        expected_rows: int | None, row_count: int, coverage_ratio: float | None,
                        is_complete: bool, status: str, details: Mapping[str, Any] | None = None,
                        db_path: Path = DEFAULT_DB) -> int:
    with _LOCK:
        conn = _connect(db_path)
        try:
            cur = conn.execute(
                """INSERT INTO data_quality_runs
                (dataset,source,requested_at,received_at,requested_trade_date,data_trade_date,expected_rows,row_count,coverage_ratio,is_complete,status,details_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dataset, source, requested_at, received_at, requested_trade_date, data_trade_date,
                 expected_rows, row_count, coverage_ratio, int(bool(is_complete)), status,
                 json.dumps(details or {}, ensure_ascii=False, default=str)))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


def get_signal(signal_id: int, db_path: Path = DEFAULT_DB) -> Optional[dict[str, Any]]:
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM signal_events WHERE id=?", (int(signal_id),)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def compare_summary(trade_date: Optional[str] = None, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    day = trade_date or datetime.now().date().isoformat()
    with _LOCK:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                """SELECT engine, strategy_version, COUNT(*) signals, AVG(score) avg_score
                   FROM signal_events WHERE trade_date=? GROUP BY engine,strategy_version ORDER BY engine""",
                (day,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def list_signals(*, trade_date: Optional[str] = None, engine: Optional[str] = None,
                 db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    clauses, args = [], []
    if trade_date:
        clauses.append("trade_date=?"); args.append(trade_date)
    if engine:
        clauses.append("engine=?"); args.append(engine)
    sql = "SELECT * FROM signal_events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY signal_time,id"
    with _LOCK:
        conn = _connect(db_path)
        try:
            return [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
        finally:
            conn.close()
