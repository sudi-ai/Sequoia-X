from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
import zlib
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import CONFIG, ROOT
from .runtime_log import log_exception, log_warning


ARCHIVE_DB = CONFIG.paid_archive_db_path
REPORT_DIR = ROOT / "reports_v8"
_INIT_LOCK = threading.RLock()
_INITIALIZED: set[str] = set()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS v8_paid_archive_rows(
 api TEXT NOT NULL,record_key TEXT NOT NULL,identity_text TEXT NOT NULL,ts_code TEXT,
 data_date TEXT,fetched_at TEXT NOT NULL,payload_zlib BLOB NOT NULL,
 PRIMARY KEY(api,record_key));
CREATE INDEX IF NOT EXISTS idx_v8_paid_archive_lookup
 ON v8_paid_archive_rows(api,data_date,ts_code);
CREATE TABLE IF NOT EXISTS v8_paid_archive_queue(
 id INTEGER PRIMARY KEY AUTOINCREMENT,task_key TEXT NOT NULL UNIQUE,api TEXT NOT NULL,scope TEXT NOT NULL,
 priority INTEGER NOT NULL DEFAULT 10,params_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'PENDING',
 attempts INTEGER NOT NULL DEFAULT 0,not_before TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 row_count INTEGER NOT NULL DEFAULT 0,last_error TEXT);
CREATE INDEX IF NOT EXISTS idx_v8_paid_archive_queue_due
 ON v8_paid_archive_queue(status,not_before,priority DESC,id);
CREATE TABLE IF NOT EXISTS v8_paid_archive_state(
 state_key TEXT PRIMARY KEY,state_value TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS v8_paid_archive_runs(
 run_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,finished_at TEXT,task_key TEXT,api TEXT,status TEXT,
 row_count INTEGER NOT NULL DEFAULT 0,latency_ms REAL,last_error TEXT,details_json TEXT NOT NULL DEFAULT '{}');
"""


def _connect(path: Path = ARCHIVE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def initialize(path: Path = ARCHIVE_DB) -> Path:
    key = str(path.resolve())
    with _INIT_LOCK:
        if key in _INITIALIZED:
            return path
        db = _connect(path)
        db.executescript(SCHEMA)
        # A terminated process must not leave jobs permanently stuck in RUNNING.
        db.execute("""UPDATE v8_paid_archive_queue SET status='RETRY',updated_at=CURRENT_TIMESTAMP
          WHERE status='RUNNING' AND julianday(updated_at)<julianday('now','-10 minutes')""")
        db.commit(); db.close(); _INITIALIZED.add(key)
    return path


def _records(value: Any) -> list[dict[str, Any]]:
    if hasattr(value, "to_dict"):
        try:
            return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:
            return []
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, Mapping)]
    return []


def _first(row: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _identity(api: str, row: Mapping[str, Any]) -> tuple[str, str, str]:
    code = _first(row, ("ts_code", "code", "symbol"))
    date = _first(row, ("trade_time", "time", "datetime", "ann_date", "report_date",
                        "trade_date", "cal_date", "end_date", "date", "pub_time"))
    extras: list[str] = []
    keys_by_api = {
        "cyq_chips": ("price",),
        "news": ("title", "content"),
        "major_news": ("title", "content"),
        "anns_d": ("title", "url"),
        "report_rc": ("report_title", "org_name", "author_name"),
        "forecast": ("type", "summary", "change_reason"),
        "index_member_all": ("l1_code", "l2_code", "l3_code", "in_date", "out_date"),
        "fina_mainbz": ("bz_item", "bz_code", "curr_type"),
        "stock_company": ("com_id", "exchange"),
    }
    for key in keys_by_api.get(api, ()):
        value = row.get(key)
        if value not in (None, ""):
            extras.append(str(value).strip()[:240])
    if not code:
        code = _first(row, ("name", "com_name", "exchange", "publisher", "src"))
    identity = "|".join((api, code, date, *extras))
    if identity.rstrip("|") == api:
        identity = api + "|" + json.dumps(dict(row), ensure_ascii=False, sort_keys=True, default=str)[:1000]
    return identity, code, date[:19]


def archive_rows(api: str, value: Any, *, fetched_at: datetime | None = None,
                 path: Path = ARCHIVE_DB) -> int:
    rows = _records(value)
    if not rows:
        return 0
    initialize(path)
    now = (fetched_at or datetime.now().astimezone()).isoformat(timespec="seconds")
    values = []
    for row in rows:
        identity, code, date = _identity(api, row)
        record_key = hashlib.sha256(identity.encode("utf-8", errors="ignore")).hexdigest()
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        values.append((api, record_key, identity[:1000], code or None, date or None, now,
                       sqlite3.Binary(zlib.compress(payload.encode("utf-8"), level=6))))
    db = _connect(path)
    try:
        for offset in range(0, len(values), 1000):
            db.executemany("""INSERT INTO v8_paid_archive_rows
              (api,record_key,identity_text,ts_code,data_date,fetched_at,payload_zlib)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(api,record_key) DO UPDATE SET
              fetched_at=excluded.fetched_at,payload_zlib=excluded.payload_zlib""", values[offset:offset+1000])
        db.commit()
    finally:
        db.close()
    return len(values)


def _state_get(key: str, default: str = "", path: Path = ARCHIVE_DB) -> str:
    initialize(path); db = _connect(path)
    row = db.execute("SELECT state_value FROM v8_paid_archive_state WHERE state_key=?", (key,)).fetchone()
    db.close()
    return str(row[0]) if row else default


def _state_set(key: str, value: str, path: Path = ARCHIVE_DB) -> None:
    initialize(path); db = _connect(path); now = datetime.now().astimezone().isoformat(timespec="seconds")
    db.execute("""INSERT INTO v8_paid_archive_state(state_key,state_value,updated_at) VALUES(?,?,?)
      ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value,updated_at=excluded.updated_at""",
      (key, value, now)); db.commit(); db.close()


def enqueue(api: str, scope: str, params: Mapping[str, Any], *, task_key: str,
            priority: int = 10, not_before: datetime | None = None, path: Path = ARCHIVE_DB) -> bool:
    initialize(path); db = _connect(path); now = datetime.now().astimezone()
    due = (not_before or now).isoformat(timespec="seconds")
    cur = db.execute("""INSERT OR IGNORE INTO v8_paid_archive_queue
      (task_key,api,scope,priority,params_json,status,not_before,created_at,updated_at)
      VALUES(?,?,?,?,?,'PENDING',?,?,?)""", (task_key, api, scope, int(priority),
      json.dumps(dict(params), ensure_ascii=False, default=str), due,
      now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
    db.commit(); added = cur.rowcount > 0; db.close(); return added


def _disk_allowed(path: Path = ARCHIVE_DB) -> tuple[bool, str]:
    try:
        db_gb = path.stat().st_size / 1024**3 if path.exists() else 0.0
        free_gb = shutil.disk_usage(path.parent if path.parent.exists() else ROOT).free / 1024**3
        if db_gb >= CONFIG.paid_archive_max_db_gb:
            return False, f"归档库达到上限{db_gb:.1f}GB"
        if free_gb < CONFIG.paid_archive_min_free_gb:
            return False, f"磁盘剩余仅{free_gb:.1f}GB"
        return True, f"归档库{db_gb:.2f}GB，磁盘剩余{free_gb:.1f}GB"
    except Exception as exc:
        return False, f"磁盘检查失败：{type(exc).__name__}"


def _claim(path: Path = ARCHIVE_DB) -> dict[str, Any] | None:
    initialize(path); db = _connect(path); now = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""SELECT * FROM v8_paid_archive_queue
          WHERE status IN ('PENDING','RETRY') AND not_before<=?
          ORDER BY priority DESC,id LIMIT 1""", (now,)).fetchone()
        if not row:
            db.rollback(); return None
        db.execute("UPDATE v8_paid_archive_queue SET status='RUNNING',attempts=attempts+1,updated_at=? WHERE id=?",
                   (now, row["id"])); db.commit(); return dict(row)
    finally:
        db.close()


def _finish(job: Mapping[str, Any], status: str, rows: int, latency_ms: float,
            error: str = "", path: Path = ARCHIVE_DB) -> None:
    db = _connect(path); now = datetime.now().astimezone(); attempts = int(job.get("attempts") or 0) + 1
    queue_status = status
    not_before = now
    if status == "DEFERRED":
        queue_status = "RETRY"; not_before = now + timedelta(minutes=30)
    elif status == "FAILED" and attempts < 4:
        queue_status = "RETRY"; not_before = now + timedelta(minutes=min(30, attempts * 5))
    db.execute("""UPDATE v8_paid_archive_queue SET status=?,row_count=?,last_error=?,not_before=?,updated_at=? WHERE id=?""",
      (queue_status, int(rows), error[:500] or None, not_before.isoformat(timespec="seconds"),
       now.isoformat(timespec="seconds"), job["id"]))
    run_id = hashlib.sha256(f"{job['task_key']}|{now.isoformat()}".encode()).hexdigest()
    db.execute("""INSERT INTO v8_paid_archive_runs
      (run_id,started_at,finished_at,task_key,api,status,row_count,latency_ms,last_error)
      VALUES(?,?,?,?,?,?,?,?,?)""", (run_id, str(job.get("updated_at") or now.isoformat()),
      now.isoformat(timespec="seconds"), job["task_key"], job["api"], queue_status,
      int(rows), round(latency_ms, 1), error[:500] or None))
    db.commit(); db.close()
    if queue_status == "DONE" and str(job.get("scope")) == "BACKFILL":
        params = json.loads(str(job["params_json"]))
        day = str(params.get("trade_date") or "")
        if day:
            previous = (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            _state_set(f"backfill_cursor:{job['api']}", previous, path)


def process_one(provider: Any = None, *, path: Path = ARCHIVE_DB) -> dict[str, Any]:
    allowed, reason = _disk_allowed(path)
    if not allowed:
        log_warning("paid_archive", reason)
        return {"status": "DISK_GUARD", "reason": reason}
    job = _claim(path)
    if not job:
        return {"status": "IDLE"}
    if provider is None:
        from .paid_data import PAID_DATA
        provider = PAID_DATA
    started = time.perf_counter(); rows = 0
    try:
        params = json.loads(str(job["params_json"]))
        value = provider.call(str(job["api"]), cache_key=f"v8:archive:{job['task_key']}",
                              ttl_seconds=1, priority="BACKGROUND", **params)
        rows = archive_rows(str(job["api"]), value, path=path)
        if rows == 0 and str(job.get("scope")) == "DAILY_CLOSE" and str(job.get("api")) in {
            "daily", "daily_basic", "moneyflow"
        }:
            latency = (time.perf_counter() - started) * 1000
            _finish(job,"DEFERRED",0,latency,"DATA_NOT_READY",path)
            return {"status":"DEFERRED","api":job["api"],"task_key":job["task_key"],
                    "reason":"DATA_NOT_READY","retry_after_minutes":30}
        latency = (time.perf_counter() - started) * 1000
        _finish(job, "DONE", rows, latency, path=path)
        return {"status": "DONE", "api": job["api"], "task_key": job["task_key"],
                "rows": rows, "latency_ms": round(latency, 1)}
    except Exception as exc:
        latency = (time.perf_counter() - started) * 1000
        _finish(job, "FAILED", rows, latency, type(exc).__name__, path)
        log_exception("paid_archive_job", exc, api=job.get("api"), task_key=job.get("task_key"))
        return {"status": "RETRY_OR_FAILED", "api": job.get("api"), "error": type(exc).__name__}


def _ts(code: str) -> str:
    clean = str(code).split(".")[0].zfill(6)
    return clean + (".SH" if clean.startswith(("5", "6", "9")) else ".BJ" if clean.startswith(("4", "8")) else ".SZ")


def target_pool(limit: int | None = None) -> list[str]:
    from .storage import connect, initialize as initialize_main
    initialize_main(); db = connect(); result: list[str] = []
    queries = (
        "SELECT code FROM v8_positions WHERE status='HOLDING' ORDER BY updated_at DESC",
        "SELECT code FROM v8_discovery_candidates ORDER BY last_seen_at DESC,opportunity_score DESC LIMIT 60",
        "SELECT code FROM v8_signal_decisions ORDER BY signal_time DESC LIMIT 40",
        "SELECT code FROM v8_miss_audit ORDER BY trade_date DESC,max_return_pct DESC LIMIT 20",
    )
    for sql in queries:
        for row in db.execute(sql).fetchall():
            code = str(row[0] or "").split(".")[0].zfill(6)
            if code.isdigit() and code not in result:
                result.append(code)
    db.close()
    return result[: int(limit or CONFIG.paid_archive_target_n)]


def seed_close_jobs(now: datetime | None = None, *, path: Path = ARCHIVE_DB) -> dict[str, int]:
    now = now or datetime.now().astimezone(); day = now.strftime("%Y%m%d"); added = 0
    market_jobs = (
        ("daily", {"trade_date": day}),
        ("daily_basic", {"trade_date": day}),
        ("moneyflow", {"trade_date": day}),
        ("trade_cal", {"exchange": "SSE", "start_date": day, "end_date": day}),
    )
    close_due = now.replace(hour=17, minute=30, second=0, microsecond=0)
    for api, params in market_jobs:
        added += int(enqueue(api, "DAILY_CLOSE", params, task_key=f"close:{api}:{day}", priority=40,
                             not_before=close_due, path=path))
    targets = target_pool()
    for code in targets:
        ts_code = _ts(code)
        added += int(enqueue("pro_bar", "TARGET_MINUTE", {
          "ts_code": ts_code, "freq": "1min", "start_date": f"{now:%Y-%m-%d} 09:30:00",
          "end_date": f"{now:%Y-%m-%d} 15:00:00"}, task_key=f"minute:{day}:{code}", priority=32, path=path))
        added += int(enqueue("stk_auction_tick", "TARGET_AUCTION", {
          "ts_code": ts_code, "start_date": day, "end_date": day},
          task_key=f"auction:{day}:{code}", priority=31, path=path))
    for code in targets[:CONFIG.paid_archive_chip_target_n]:
        ts_code = _ts(code)
        for api in ("cyq_perf", "cyq_chips"):
            added += int(enqueue(api, "TARGET_CHIP", {"ts_code": ts_code, "start_date": day, "end_date": day},
                task_key=f"chip:{api}:{day}:{code}", priority=30, path=path))
    # Metadata is large but changes slowly; refresh weekly.
    week = now.strftime("%G-W%V")
    one_year_ago = (now - timedelta(days=365)).strftime("%Y%m%d")
    for code in targets:
        ts_code = _ts(code)
        added += int(enqueue("forecast", "TARGET_EVENT_HISTORY", {
          "ts_code": ts_code, "start_date": one_year_ago, "end_date": day},
          task_key=f"target:{week}:forecast:{code}", priority=19, path=path))
        added += int(enqueue("fina_mainbz", "TARGET_BUSINESS_HISTORY", {
          "ts_code": ts_code, "start_date": one_year_ago, "end_date": day},
          task_key=f"target:{week}:fina_mainbz:{code}", priority=18, path=path))
    metadata = (
        ("stock_basic", {"list_status": "L", "limit": 10000}),
        ("index_basic", {"limit": 10000}),
        ("index_member_all", {"limit": 10000}),
        ("stock_company", {"exchange": "SSE", "limit": 10000}),
        ("stock_company", {"exchange": "SZSE", "limit": 10000}),
        ("stock_company", {"exchange": "BSE", "limit": 10000}),
    )
    for index, (api, params) in enumerate(metadata):
        added += int(enqueue(api, "WEEKLY_METADATA", params,
          task_key=f"metadata:{week}:{api}:{index}", priority=20, path=path))
    return {"added": added, "targets": len(targets)}


def seed_backfill(now: datetime | None = None, *, path: Path = ARCHIVE_DB) -> int:
    now = now or datetime.now().astimezone(); added = 0
    specs = {
        "daily": CONFIG.paid_archive_daily_days,
        "daily_basic": CONFIG.paid_archive_basic_days,
        "moneyflow": CONFIG.paid_archive_moneyflow_days,
    }
    for api, horizon in specs.items():
        cursor_text = _state_get(f"backfill_cursor:{api}", (now - timedelta(days=1)).strftime("%Y%m%d"), path)
        try: cursor = datetime.strptime(cursor_text, "%Y%m%d")
        except ValueError: cursor = now - timedelta(days=1)
        cutoff = now - timedelta(days=horizon)
        while cursor.weekday() >= 5:
            cursor -= timedelta(days=1)
        if cursor.date() < cutoff.date():
            continue
        day = cursor.strftime("%Y%m%d")
        added += int(enqueue(api, "BACKFILL", {"trade_date": day},
            task_key=f"backfill:{api}:{day}", priority=8, path=path))
    return added


def archive_coverage(path: Path = ARCHIVE_DB) -> dict[str, Any]:
    initialize(path); db = _connect(path)
    rows = [dict(row) for row in db.execute("""SELECT api,count(*) row_count,min(data_date) min_date,
      max(data_date) max_date FROM v8_paid_archive_rows GROUP BY api ORDER BY api""").fetchall()]
    queue = {str(row["status"]): int(row["n"]) for row in db.execute(
      "SELECT status,count(*) n FROM v8_paid_archive_queue GROUP BY status").fetchall()}
    failures = [dict(row) for row in db.execute("""SELECT api,count(*) n,max(last_error) last_error
      FROM v8_paid_archive_queue WHERE status='FAILED' GROUP BY api ORDER BY n DESC""").fetchall()]
    db.close(); allowed, disk = _disk_allowed(path)
    return {"apis": rows, "queue": queue, "failures": failures, "disk_allowed": allowed, "disk": disk}


def write_coverage_report(now: datetime | None = None, *, path: Path = ARCHIVE_DB) -> Path:
    now = now or datetime.now().astimezone(); data = archive_coverage(path)
    lines = ["# V8 付费接口本地归档日报", "", f"生成时间：{now.isoformat(timespec='seconds')}", "",
             f"磁盘保护：{'正常' if data['disk_allowed'] else '已暂停'}｜{data['disk']}", "",
             "## 接口覆盖", "", "| 接口 | 已保存行数 | 最早数据 | 最新数据 |", "|---|---:|---|---|"]
    for row in data["apis"]:
        lines.append(f"| {row['api']} | {row['row_count']} | {row['min_date'] or '-'} | {row['max_date'] or '-'} |")
    lines += ["", "## 任务队列", "", json.dumps(data["queue"], ensure_ascii=False), ""]
    if data["failures"]:
        lines += ["## 最终失败任务", ""] + [f"- {x['api']}：{x['n']}，{x['last_error']}" for x in data["failures"]]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = REPORT_DIR / f"V8付费接口归档日报_{now:%Y%m%d}.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


class PaidArchiveService:
    def __init__(self, path: Path = ARCHIVE_DB):
        self.path = path; self._stop = threading.Event(); self._last_seed_day = ""; self._last_report_day = ""

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        initialize(self.path)
        while not self._stop.is_set():
            now = datetime.now().astimezone(); clock = now.time().replace(tzinfo=None)
            try:
                market_open = now.weekday() < 5 and (dtime(9, 14) <= clock <= dtime(11, 35) or dtime(12, 55) <= clock <= dtime(15, 10))
                day = now.date().isoformat()
                if now.weekday() < 5 and clock >= dtime(15, 20) and self._last_seed_day != day:
                    seed_close_jobs(now, path=self.path); self._last_seed_day = day
                if not market_open:
                    db = _connect(self.path)
                    pending = int(db.execute("SELECT count(*) FROM v8_paid_archive_queue WHERE status IN ('PENDING','RETRY','RUNNING')").fetchone()[0])
                    db.close()
                    if pending < 4:
                        seed_backfill(now, path=self.path)
                    process_one(path=self.path)
                if clock >= dtime(22, 45) and self._last_report_day != day:
                    report = write_coverage_report(now, path=self.path); self._last_report_day = day
                    try:
                        from .storage import save_module_health
                        save_module_health("PAID_MONTH_ARCHIVE", "OK", now.isoformat(timespec="seconds"),
                                           {"report": str(report), **archive_coverage(self.path)})
                    except Exception:
                        pass
            except Exception as exc:
                log_exception("paid_archive_service", exc)
            self._stop.wait(CONFIG.paid_archive_worker_seconds)


SERVICE = PaidArchiveService()
