from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .experimental_engine import ENGINE_VERSION
from .signal_lab import DEFAULT_DB, _LOCK, _connect

CN_TZ = ZoneInfo("Asia/Shanghai")
STATES = {"WATCH", "PREOPEN_CHECK", "BUY_CONFIRMED", "CANCELLED", "EXPIRED", "CLOSED"}
ALLOWED = {
    None: {"WATCH", "CANCELLED"},
    "WATCH": {"PREOPEN_CHECK", "CANCELLED", "EXPIRED", "CLOSED"},
    "PREOPEN_CHECK": {"BUY_CONFIRMED", "CANCELLED", "EXPIRED", "CLOSED"},
    "BUY_CONFIRMED": {"CLOSED"},
    "CANCELLED": {"CLOSED"},
    "EXPIRED": {"CLOSED"},
    "CLOSED": set(),
}


def _stamp(value: str | None = None) -> str:
    if value:
        return value
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def upsert_candidate(*, trade_date: str, code: str, name: str = "", initial_state: str = "WATCH",
                     scores: Mapping[str, Any] | None = None, snapshot: Mapping[str, Any] | None = None,
                     evidence: Mapping[str, Any] | None = None, reasons: list[str] | None = None,
                     changed_at: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if initial_state not in STATES:
        raise ValueError(f"invalid state: {initial_state}")
    stamp = _stamp(changed_at)
    clean = str(code or "").split(".")[0].zfill(6)
    scores = dict(scores or {})
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM v71_candidate_states WHERE trade_date=? AND code=? AND strategy_version=?",
                (trade_date, clean, ENGINE_VERSION),
            ).fetchone()
            if row:
                return dict(row)
            cur = conn.execute(
                """INSERT INTO v71_candidate_states
                (trade_date,code,name,state,strategy_version,v66_score,v71_risk_score,v71_quality_score,
                 next_day_potential_score,potential_label,snapshot_json,evidence_json,reason_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade_date, clean, name, initial_state, ENGINE_VERSION,
                 scores.get("v66_score"), scores.get("v71_risk_score"), scores.get("v71_quality_score"),
                 scores.get("next_day_potential_score"), scores.get("next_day_potential_label"),
                 json.dumps(snapshot or {}, ensure_ascii=False, default=str),
                 json.dumps(evidence or {}, ensure_ascii=False, default=str),
                 json.dumps(reasons or [], ensure_ascii=False), stamp, stamp),
            )
            cid = int(cur.lastrowid)
            conn.execute("""INSERT INTO v71_state_history(candidate_id,from_state,to_state,changed_at,reason_json,evidence_json)
                          VALUES(?,?,?,?,?,?)""",
                         (cid, None, initial_state, stamp, json.dumps(reasons or [], ensure_ascii=False),
                          json.dumps(evidence or {}, ensure_ascii=False, default=str)))
            conn.commit()
            return dict(conn.execute("SELECT * FROM v71_candidate_states WHERE id=?", (cid,)).fetchone())
        finally:
            conn.close()


def get_candidate(*, trade_date: str, code: str, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM v71_candidate_states WHERE trade_date=? AND code=? AND strategy_version=?",
                               (trade_date, clean, ENGINE_VERSION)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def transition(*, trade_date: str, code: str, to_state: str, reasons: list[str] | None = None,
               evidence: Mapping[str, Any] | None = None, changed_at: str | None = None,
               db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if to_state not in STATES:
        raise ValueError(f"invalid state: {to_state}")
    stamp = _stamp(changed_at)
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM v71_candidate_states WHERE trade_date=? AND code=? AND strategy_version=?",
                               (trade_date, clean, ENGINE_VERSION)).fetchone()
            if not row:
                raise KeyError(f"candidate not found: {trade_date} {clean}")
            current = str(row["state"])
            if to_state == current:
                return dict(row)
            if to_state not in ALLOWED.get(current, set()):
                raise ValueError(f"illegal state transition: {current}->{to_state}")
            old_evidence = json.loads(row["evidence_json"] or "{}")
            if evidence:
                old_evidence.update(dict(evidence))
            conn.execute("""UPDATE v71_candidate_states SET state=?,evidence_json=?,reason_json=?,updated_at=? WHERE id=?""",
                         (to_state, json.dumps(old_evidence, ensure_ascii=False, default=str),
                          json.dumps(reasons or [], ensure_ascii=False), stamp, int(row["id"])))
            conn.execute("""INSERT INTO v71_state_history(candidate_id,from_state,to_state,changed_at,reason_json,evidence_json)
                          VALUES(?,?,?,?,?,?)""",
                         (int(row["id"]), current, to_state, stamp, json.dumps(reasons or [], ensure_ascii=False),
                          json.dumps(evidence or {}, ensure_ascii=False, default=str)))
            conn.commit()
            return dict(conn.execute("SELECT * FROM v71_candidate_states WHERE id=?", (int(row["id"]),)).fetchone())
        finally:
            conn.close()


def list_candidates(*, trade_date: str | None = None, state: str | None = None,
                    db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    clauses, args = ["strategy_version=?"], [ENGINE_VERSION]
    if trade_date:
        clauses.append("trade_date=?"); args.append(trade_date)
    if state:
        clauses.append("state=?"); args.append(state)
    with _LOCK:
        conn = _connect(db_path)
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM v71_candidate_states WHERE " + " AND ".join(clauses) + " ORDER BY next_day_potential_score DESC,id",
                tuple(args)).fetchall()]
        finally:
            conn.close()


def mark_push_once(*, trade_date: str, code: str, state: str, pushed_at: str | None = None,
                   db_path: Path = DEFAULT_DB) -> bool:
    """Legacy helper retained for compatibility: immediately marks a successful push."""
    stamp = _stamp(pushed_at)
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT success FROM v71_push_dedupe WHERE trade_date=? AND code=? AND state=?",
                               (trade_date, clean, state)).fetchone()
            if row and int(row["success"] or 0) == 1:
                return False
            conn.execute("""INSERT INTO v71_push_dedupe
                (trade_date,code,state,pushed_at,success,attempt_count,last_attempt_at,last_error)
                VALUES(?,?,?,?,1,1,?,NULL)
                ON CONFLICT(trade_date,code,state) DO UPDATE SET
                    pushed_at=excluded.pushed_at,success=1,
                    attempt_count=MAX(v71_push_dedupe.attempt_count,1),last_attempt_at=excluded.last_attempt_at,last_error=NULL
                """, (trade_date, clean, state, stamp, stamp))
            conn.commit()
            return True
        finally:
            conn.close()


def reserve_push_attempt(*, trade_date: str, code: str, state: str, max_attempts: int,
                         attempted_at: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Reserve one finite delivery attempt. A failed attempt does not become a dedupe success."""
    stamp = _stamp(attempted_at)
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM v71_push_dedupe WHERE trade_date=? AND code=? AND state=?",
                               (trade_date, clean, state)).fetchone()
            if row and int(row["success"] or 0) == 1:
                return {"allowed": False, "reason": "duplicate_state_push", "attempt_count": int(row["attempt_count"] or 0)}
            attempts = int(row["attempt_count"] or 0) if row else 0
            if attempts >= max_attempts:
                return {"allowed": False, "reason": "push_retry_limit_reached", "attempt_count": attempts}
            attempts += 1
            if row:
                conn.execute("""UPDATE v71_push_dedupe SET attempt_count=?,last_attempt_at=?,last_error=NULL
                                WHERE trade_date=? AND code=? AND state=?""",
                             (attempts, stamp, trade_date, clean, state))
            else:
                conn.execute("""INSERT INTO v71_push_dedupe
                    (trade_date,code,state,pushed_at,success,attempt_count,last_attempt_at,last_error)
                    VALUES(?,?,?,'',0,?,?,NULL)""",
                             (trade_date, clean, state, attempts, stamp))
            conn.commit()
            return {"allowed": True, "reason": "attempt_reserved", "attempt_count": attempts}
        finally:
            conn.close()


def complete_push_attempt(*, trade_date: str, code: str, state: str, success: bool,
                          error: str = "", completed_at: str | None = None, db_path: Path = DEFAULT_DB) -> None:
    stamp = _stamp(completed_at)
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            if success:
                conn.execute("""UPDATE v71_push_dedupe SET success=1,pushed_at=?,last_attempt_at=?,last_error=NULL
                                WHERE trade_date=? AND code=? AND state=?""",
                             (stamp, stamp, trade_date, clean, state))
            else:
                conn.execute("""UPDATE v71_push_dedupe SET success=0,last_attempt_at=?,last_error=?
                                WHERE trade_date=? AND code=? AND state=?""",
                             (stamp, str(error)[:240], trade_date, clean, state))
            conn.commit()
        finally:
            conn.close()


def get_push_status(*, trade_date: str, code: str, state: str, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM v71_push_dedupe WHERE trade_date=? AND code=? AND state=?",
                               (trade_date, clean, state)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

def history(*, trade_date: str, code: str, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    clean = str(code or "").split(".")[0].zfill(6)
    with _LOCK:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT id FROM v71_candidate_states WHERE trade_date=? AND code=? AND strategy_version=?",
                               (trade_date, clean, ENGINE_VERSION)).fetchone()
            if not row:
                return []
            return [dict(r) for r in conn.execute("SELECT * FROM v71_state_history WHERE candidate_id=? ORDER BY id",
                                                  (int(row["id"]),)).fetchall()]
        finally:
            conn.close()
