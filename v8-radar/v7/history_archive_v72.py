from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = ROOT / "data_v7" / "minute_archive.sqlite3"


def _connect(path: Path = DEFAULT_ARCHIVE) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("""CREATE TABLE IF NOT EXISTS minute_archive(
      code TEXT NOT NULL, observed_at TEXT NOT NULL, trade_date TEXT NOT NULL,
      open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
      source TEXT, data_time TEXT, archive_time TEXT, raw_json TEXT NOT NULL DEFAULT '{}', archived_at TEXT NOT NULL,
      PRIMARY KEY(code, observed_at))""")
    cols = {r[1] for r in c.execute("pragma table_info(minute_archive)").fetchall()}
    for name in ("data_time", "archive_time"):
        if name not in cols:
            c.execute(f"alter table minute_archive add column {name} TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS idx_minute_archive_lookup ON minute_archive(code,trade_date,observed_at)")
    c.commit()
    return c


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if hasattr(value, "to_dict"):
        try:
            return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:
            return []
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, Mapping)]
    return []


def archive_minutes(code: str, rows: Iterable[Mapping[str, Any]], *, source: str = "pro_bar",
                    db_path: Path = DEFAULT_ARCHIVE) -> int:
    """Idempotently archive minutes. Same code+minute is stored once."""
    archive_time = datetime.now().astimezone().isoformat(timespec="seconds")
    payload = []
    clean = str(code).split(".")[0].zfill(6)
    for r0 in rows:
        r = dict(r0)
        ts = str(r.get("trade_time") or r.get("datetime") or r.get("time") or "")
        if not ts:
            continue
        date = str(r.get("trade_date") or ts[:10]).replace("-", "")[:8]
        def n(k):
            try:
                return float(r.get(k)) if r.get(k) not in (None, "") else None
            except Exception:
                return None
        payload.append((clean, ts, date, n("open"), n("high"), n("low"), n("close"), n("vol") or n("volume"), n("amount"),
                        source, ts, archive_time, json.dumps(r, ensure_ascii=False, default=str), archive_time))
    if not payload:
        return 0
    c = _connect(db_path)
    try:
        before = int(c.execute("select count(*) n from minute_archive where code=?", (clean,)).fetchone()["n"])
        c.executemany("""INSERT OR IGNORE INTO minute_archive
        (code,observed_at,trade_date,open,high,low,close,volume,amount,source,data_time,archive_time,raw_json,archived_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
        c.commit()
        after = int(c.execute("select count(*) n from minute_archive where code=?", (clean,)).fetchone()["n"])
        return max(0, after - before)
    finally:
        c.close()


def query_minutes(code: str, *, start: str | None = None, end: str | None = None,
                  data_cutoff_time: str | None = None, db_path: Path = DEFAULT_ARCHIVE) -> list[dict[str, Any]]:
    """Query archive with a hard T0 cutoff. Post-T0 rows can never leak into realtime features."""
    clean = str(code).split(".")[0].zfill(6)
    clauses = ["code=?"]
    args: list[Any] = [clean]
    if start:
        clauses.append("observed_at>=?")
        args.append(start)
    effective_end = min(x for x in [end, data_cutoff_time] if x is not None) if (end or data_cutoff_time) else None
    if effective_end:
        clauses.append("observed_at<=?")
        args.append(effective_end)
    c = _connect(db_path)
    try:
        return [dict(r) for r in c.execute("SELECT * FROM minute_archive WHERE " + " AND ".join(clauses) + " ORDER BY observed_at", tuple(args)).fetchall()]
    finally:
        c.close()


def _target_codes(day: str, *, signal_db_path=None) -> dict[str, set[str]]:
    """Collect archive targets by source without losing priority information."""
    from .config import V72_CONFIG
    from .portfolio import list_positions
    from .signal_lab import DEFAULT_DB, _LOCK, _connect
    db = signal_db_path or DEFAULT_DB
    buckets = {"actual": set(), "simulated": set(), "forward": set(), "candidate": set()}
    for p in list_positions(db_path=db):
        code = str(p.get("ts_code") or "").split(".")[0].zfill(6)
        if not code.strip("0"):
            continue
        if str(p.get("position_type")) == "ACTUAL" and str(p.get("status")) == "HOLDING":
            buckets["actual"].add(code)
        elif str(p.get("position_type")) == "SIMULATED" and str(p.get("status")) == "SIMULATED":
            buckets["simulated"].add(code)
    with _LOCK:
        c = _connect(db)
        try:
            try:
                rows = c.execute("select distinct code from v72_forward_snapshots where signal_trade_date=?", (day,)).fetchall()
                buckets["forward"].update(str(r["code"]).split(".")[0].zfill(6) for r in rows)
            except sqlite3.OperationalError:
                pass
            rows = c.execute("select code from signal_events where trade_date=? and engine='V7_SHADOW' order by coalesce(score,-999999) desc limit ?",
                             (day, int(V72_CONFIG.archive_candidate_top_n))).fetchall()
            buckets["candidate"].update(str(r["code"]).split(".")[0].zfill(6) for r in rows)
        finally:
            c.close()
    return buckets


def _archive_plan(buckets: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Actual > simulated > Forward > candidate; one API call per stock.

    A stock keeps every source label even when it appears in multiple categories.
    """
    priority_order = ("actual", "simulated", "forward", "candidate")
    priority_rank = {name: i for i, name in enumerate(priority_order)}
    all_codes = set().union(*(buckets.get(k, set()) for k in priority_order))
    plan = []
    for code in all_codes:
        labels = [k for k in priority_order if code in buckets.get(k, set())]
        highest = min(labels, key=lambda k: priority_rank[k])
        plan.append({"code": code, "priority": highest, "source_labels": labels})
    return sorted(plan, key=lambda x: (priority_rank[x["priority"]], x["code"]))


def _archive_ts_code(code: str) -> str:
    clean = str(code).split(".")[0].zfill(6)
    if clean.startswith(("4", "8", "92")):
        return f"{clean}.BJ"
    if clean.startswith("6"):
        return f"{clean}.SH"
    return f"{clean}.SZ"


def archive_after_close_targets(*, now: datetime | None = None, bridge=None, signal_db_path=None,
                                archive_db_path: Path = DEFAULT_ARCHIVE, health_manager=None) -> dict[str, Any]:
    """15:10 minute archive with priority de-dup and unified pro_bar budget protection."""
    from .tushare_bridge import BRIDGE
    from .runtime_log_v72 import log_event
    from .health_v72 import HEALTH_MANAGER
    from .config import CONFIG, V72_CONFIG
    now = now or datetime.now().astimezone()
    day_iso = now.date().isoformat()
    day = now.strftime("%Y%m%d")
    bridge = bridge or BRIDGE
    hm = health_manager or HEALTH_MANAGER
    buckets = _target_codes(day_iso, signal_db_path=signal_db_path)
    plan = _archive_plan(buckets)
    success = empty = failed = rows_received = rows_written = 0
    details = []
    budget_exhausted = False

    for index, target in enumerate(plan):
        code = target["code"]
        priority = target["priority"]
        labels = target["source_labels"]
        allowed, budget_reason = hm.allow(
            "pro_bar",
            per_minute=min(CONFIG.api_limit_per_minute, V72_CONFIG.api_default_per_minute),
            per_day=V72_CONFIG.api_default_per_day,
            priority="holding" if priority == "actual" else "normal",
        )
        if not allowed:
            budget_exhausted = True
            # Safely stop: do not burn further calls once the shared budget refuses service.
            remaining = plan[index:]
            for pending in remaining:
                failed += 1
                details.append({"code": pending["code"], "status": "BUDGET_EXHAUSTED", "source": "pro_bar",
                                "priority": pending["priority"], "source_labels": pending["source_labels"],
                                "rows_received": 0, "rows_written": 0, "error": budget_reason})
            break

        ts_code = _archive_ts_code(code)
        try:
            raw = bridge.pro_bar(ts_code=ts_code, freq="1min", start_date=f"{day} 09:30:00", end_date=f"{day} 15:00:00")
            rows = _records(raw)
            received = len(rows)
            rows_received += received
            if received == 0:
                empty += 1
                hm.success("pro_bar")
                details.append({"code": code, "status": "EMPTY", "source": "pro_bar", "priority": priority,
                                "source_labels": labels, "rows_received": 0, "rows_written": 0})
                continue
            n = archive_minutes(code, rows, source="pro_bar", db_path=archive_db_path)
            success += 1
            rows_written += n
            hm.success("pro_bar")
            details.append({"code": code, "status": "SUCCESS" if n > 0 else "SUCCESS_DEDUPED", "source": "pro_bar",
                            "priority": priority, "source_labels": labels, "rows_received": received, "rows_written": n})
        except Exception as exc:
            failed += 1
            hm.failure("pro_bar")
            details.append({"code": code, "status": "UNAVAILABLE", "source": "pro_bar", "priority": priority,
                            "source_labels": labels, "rows_received": 0, "rows_written": 0, "error": type(exc).__name__})
            log_event("history_archive", "历史分钟自动归档失败", code=code, exc=exc, degraded=True, event="minute_archive")

    return {"trade_date": day_iso, "targets": len(plan), "success_stocks": success, "empty_stocks": empty,
            "failed_stocks": failed, "rows_received": rows_received, "rows_written": rows_written,
            "budget_exhausted": budget_exhausted, "target_sources": {k: len(v) for k, v in buckets.items()}, "details": details}


def purge_old_minutes(*, before_trade_date: str, db_path: Path = DEFAULT_ARCHIVE) -> int:
    c = _connect(db_path)
    try:
        cur = c.execute("delete from minute_archive where trade_date<?", (str(before_trade_date).replace("-", "")[:8],))
        c.commit()
        return int(cur.rowcount or 0)
    finally:
        c.close()
