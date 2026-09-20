# -*- coding: utf-8 -*-
"""
轻量 PIT 特征库：SQLite 默认可用。
未来若安装 Polars/Parquet，可在不改策略接口的情况下替换后端。
"""
from __future__ import annotations
import json, sqlite3, threading
from contextlib import closing
from pathlib import Path
from datetime import datetime
from config import DATA_DIR

DB = DATA_DIR / "feature_store.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_snapshot(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    feature_set TEXT NOT NULL,
    payload TEXT NOT NULL,
    source_map TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feature_code_time ON feature_snapshot(ts_code, decision_time);
"""

class FeatureStore:
    def __init__(self, path=DB):
        self.path=str(path); self.lock=threading.Lock()
        with closing(sqlite3.connect(self.path)) as c,c:
            c.executescript(SCHEMA)

    def save(self, ts_code, decision_time, feature_set, payload, source_map=None, observed_at=None):
        observed_at=observed_at or datetime.now().isoformat(timespec="milliseconds")
        with self.lock, closing(sqlite3.connect(self.path)) as c,c:
            return c.execute(
                "INSERT INTO feature_snapshot(ts_code,observed_at,decision_time,feature_set,payload,source_map) VALUES(?,?,?,?,?,?)",
                (ts_code,observed_at,decision_time,feature_set,
                 json.dumps(payload,ensure_ascii=False),json.dumps(source_map or {},ensure_ascii=False))
            ).lastrowid

    def latest_before(self, ts_code, decision_time, feature_set="core"):
        with closing(sqlite3.connect(self.path)) as c:
            row=c.execute(
                """SELECT payload,source_map,observed_at FROM feature_snapshot
                   WHERE ts_code=? AND feature_set=? AND decision_time<=?
                   ORDER BY decision_time DESC,id DESC LIMIT 1""",
                (ts_code,feature_set,decision_time)).fetchone()
        if not row:return None
        return {"payload":json.loads(row[0]),"source_map":json.loads(row[1]),"observed_at":row[2]}
