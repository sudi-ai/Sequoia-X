# -*- coding: utf-8 -*-
"""Deterministic offline checks for P0 append-only/PIT contracts."""
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from p0_realtime import CN_TZ, build_intraday_features, db_connect, discovery_labels, insert_snapshot, normalize_volume_and_vwap

with tempfile.TemporaryDirectory() as folder:
    conn = db_connect(Path(folder) / "p0.db")
    observed = datetime(2026, 9, 2, 9, 35, tzinfo=CN_TZ).isoformat()
    meta = {"observed_at": observed, "effective_at": observed, "source": "TEST", "status": "OK", "field_count": 9, "latency_ms": 1.0, "coverage_ratio": 1.0, "data_quality": 1.0, "is_pit_safe": True, "issues": [], "error": None}
    frame = pd.DataFrame([{"ts_code": "000001.SZ", "name": "TEST", "industry": "BANK", "pre_close": 10.0, "open": 10.1, "high": 10.3, "low": 10.0, "close": 10.2, "vol": 1000, "amount": 10200}])
    records, sectors, events = build_intraday_features(conn, frame, meta)
    snapshot_id = insert_snapshot(conn, records, sectors, events, meta, "09:35", None)
    assert conn.execute("SELECT COUNT(*) FROM intraday_quote WHERE snapshot_id=?", (snapshot_id,)).fetchone()[0] == 1
    manifest = conn.execute("SELECT * FROM snapshot_manifest WHERE snapshot_id=?", (snapshot_id,)).fetchone()
    assert manifest["effective_at"] <= manifest["observed_at"] and manifest["is_pit_safe"] == 1
    assert conn.execute("SELECT COUNT(*) FROM intraday_quote WHERE observed_at>?", (observed,)).fetchone()[0] == 0
    conn.close()
print("P0_SELF_TEST PASS: append-only snapshot, normalized quote, sector snapshot, PIT timestamp contract")
unit_frame = pd.DataFrame([{"vol": 1000, "amount": 1020000, "close": 10.2, "low": 10.0, "high": 10.3}])
unit_frame, unit_audit = normalize_volume_and_vwap(unit_frame)
assert unit_audit["detected_volume_unit"] == "LOT_100_SHARES"
assert unit_audit["status"] == "PASS" and abs(float(unit_frame.iloc[0]["vwap"]) - 10.2) < 1e-9
assert discovery_labels({"discovery_pools": ["REALTIME_WINNERS"]}) == ["REALTIME_WINNERS"]
print("VWAP_UNIT_TEST PASS; DISCOVERY_RETURN_ADAPTER_TEST PASS")
