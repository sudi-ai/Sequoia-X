# -*- coding: utf-8 -*-
"""Read-only adapter from the P0 intraday PIT database to the workbench.

This module never calls a market-data provider and never writes to the PIT
database.  It deliberately keeps market discovery records separate from trade
signals: a discovery candidate is evidence of coverage, not a buy signal.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path


P0_DB = Path(__file__).with_name("data") / "p0_intraday_pit.db"
CHECKPOINTS = ("09:25", "09:35", "10:00", "11:00", "13:30", "14:30")


def _connect():
    if not P0_DB.exists():
        return None
    try:
        return sqlite3.connect(f"file:{P0_DB.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None


def _one(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        return row
    except sqlite3.Error:
        return None


def _all(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_intraday_snapshot(limit=30):
    """Return the newest persisted intraday snapshot and discovery evidence.

    A missing or malformed database is reported as ``ready=False`` rather than
    raising, so the dashboard remains usable with no paid data configured.
    """
    result = {
        "ready": False, "db_path": str(P0_DB), "snapshot": {}, "leaders": [],
        "discoveries": [], "sectors": [], "coverage": {}, "checkpoints": {},
        "table_counts": {}, "error": "尚未找到盘中PIT数据库",
    }
    conn = _connect()
    if conn is None:
        return result
    try:
        tables = ("snapshot_manifest", "intraday_quote", "discovery_observation", "sector_realtime_snapshot", "winner_recall_daily")
        result["table_counts"] = {
            name: int((_one(conn, f"SELECT COUNT(*) FROM {name}") or (0,))[0])
            for name in tables
        }
        row = _one(conn, """
            SELECT snapshot_id, trade_date, observed_at, effective_at, source,
                   source_trade_time, mandatory_label, status, row_count,
                   field_count, latency_ms, coverage_ratio, data_quality,
                   is_pit_safe, issues_json
            FROM snapshot_manifest
            WHERE COALESCE(row_count, 0) > 0
            ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1
        """)
        if not row:
            result["error"] = "盘中PIT数据库尚无有效快照"
            return result
        keys = ("snapshot_id", "trade_date", "observed_at", "effective_at", "source", "source_trade_time", "mandatory_label", "status", "row_count", "field_count", "latency_ms", "coverage_ratio", "data_quality", "is_pit_safe", "issues_json")
        snapshot = dict(zip(keys, row))
        try:
            snapshot["issues"] = json.loads(snapshot.pop("issues_json") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot["issues"] = []
        result["snapshot"] = snapshot
        sid = snapshot["snapshot_id"]

        checkpoint_rows = _all(conn, """
            SELECT mandatory_label, COUNT(DISTINCT trade_date), MAX(observed_at)
            FROM snapshot_manifest WHERE mandatory_label IS NOT NULL
            GROUP BY mandatory_label
        """)
        result["checkpoints"] = {
            str(label): {"days": int(days or 0), "latest_at": latest or "--"}
            for label, days, latest in checkpoint_rows if label
        }
        for label in CHECKPOINTS:
            result["checkpoints"].setdefault(label, {"days": 0, "latest_at": "--"})

        quote_rows = _all(conn, """
            SELECT ts_code, name, industry, close, pct_chg, amount, vwap,
                   vwap_strength, amount_velocity, persistence_score,
                   market_rank, current_top20, current_top50, sector_up_ratio,
                   sector_median_pct, sector_diffusion_rank, sequence_state,
                   data_quality, is_pit_safe
            FROM intraday_quote WHERE snapshot_id = ?
            ORDER BY CASE WHEN market_rank IS NULL THEN 1 ELSE 0 END,
                     market_rank ASC, pct_chg DESC LIMIT ?
        """, (sid, int(limit)))
        qkeys = ("ts_code", "name", "industry", "price", "pct_chg", "amount", "vwap", "vwap_strength", "amount_velocity", "persistence_score", "market_rank", "current_top20", "current_top50", "sector_up_ratio", "sector_median_pct", "sector_diffusion_rank", "sequence_state", "data_quality", "is_pit_safe")
        result["leaders"] = [dict(zip(qkeys, row)) for row in quote_rows]

        breadth = _one(conn, """
            SELECT COUNT(*), SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN current_top20 = 1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN current_top50 = 1 THEN 1 ELSE 0 END)
            FROM intraday_quote WHERE snapshot_id = ?
        """, (sid,)) or (0, 0, 0, 0, 0)
        result["coverage"] = {
            "stocks": int(breadth[0] or 0), "up": int(breadth[1] or 0), "down": int(breadth[2] or 0),
            "top20": int(breadth[3] or 0), "top50": int(breadth[4] or 0),
        }

        sector_rows = _all(conn, """
            SELECT COALESCE(NULLIF(industry, ''), '未分类'), COUNT(*),
                   AVG(COALESCE(pct_chg, 0)), AVG(COALESCE(sector_up_ratio, 0)),
                   AVG(COALESCE(sector_diffusion_rank, 999999))
            FROM intraday_quote WHERE snapshot_id = ?
            GROUP BY COALESCE(NULLIF(industry, ''), '未分类')
            ORDER BY AVG(COALESCE(sector_diffusion_rank, 999999)) ASC,
                     AVG(COALESCE(pct_chg, 0)) DESC LIMIT 12
        """, (sid,))
        result["sectors"] = [
            {"name": name, "stocks": int(stocks or 0), "avg_pct": _as_float(avg_pct),
             "up_ratio": _as_float(up_ratio), "diffusion_rank": _as_float(rank, 999999)}
            for name, stocks, avg_pct, up_ratio, rank in sector_rows
        ]

        discovery_rows = _all(conn, """
            SELECT d.ts_code, d.discovery_type, d.discovery_score, d.execution_pool,
                   d.price, d.pct_chg, d.session_high, d.vwap, d.evidence_json,
                   d.observed_at, d.data_quality, d.is_pit_safe,
                   q.name, q.industry, q.market_rank, q.amount_velocity,
                   q.persistence_score, q.sequence_state
            FROM discovery_observation d
            LEFT JOIN intraday_quote q ON q.snapshot_id = d.snapshot_id AND q.ts_code = d.ts_code
            WHERE d.snapshot_id = ?
            ORDER BY COALESCE(d.discovery_score, 0) DESC LIMIT ?
        """, (sid, int(limit)))
        dkeys = ("ts_code", "discovery_type", "discovery_score", "execution_pool", "price", "pct_chg", "session_high", "vwap", "evidence_json", "observed_at", "data_quality", "is_pit_safe", "name", "industry", "market_rank", "amount_velocity", "persistence_score", "sequence_state")
        discoveries = []
        for data in (dict(zip(dkeys, row)) for row in discovery_rows):
            try:
                data["evidence"] = json.loads(data.pop("evidence_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                data["evidence"] = {}
            discoveries.append(data)
        result["discoveries"] = discoveries
        result["ready"] = bool(result["coverage"]["stocks"])
        result["error"] = "" if result["ready"] else "最新快照没有股票行"
        return result
    except Exception as exc:  # Read failures must never take down the dashboard.
        result["error"] = f"读取盘中PIT失败：{type(exc).__name__}"
        return result
    finally:
        conn.close()
