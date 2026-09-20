from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .signal_lab import DEFAULT_DB, _LOCK, _connect

PORTFOLIO_STATES = {"PENDING_FILL", "HOLDING", "SIMULATED", "CLOSED"}
POSITION_TYPES = {"ACTUAL", "SIMULATED"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _code(v: Any) -> str:
    return str(v or "").split(".")[0].zfill(6)


def ensure_schema(db_path: Path = DEFAULT_DB) -> None:
    with _LOCK:
        c = _connect(db_path)
        c.executescript('''
        CREATE TABLE IF NOT EXISTS v72_positions(
          position_id TEXT PRIMARY KEY, ts_code TEXT NOT NULL,name TEXT,position_type TEXT NOT NULL,
          status TEXT NOT NULL,shares REAL NOT NULL DEFAULT 0,cost_price REAL,entry_date TEXT,
          manual_stop_price REAL,notes TEXT,realized_pnl REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,last_update_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_v72_positions_status ON v72_positions(status,ts_code);
        CREATE TABLE IF NOT EXISTS v72_position_trades(
          trade_id TEXT PRIMARY KEY,position_id TEXT NOT NULL,action TEXT NOT NULL,shares REAL NOT NULL,price REAL NOT NULL,
          traded_at TEXT NOT NULL,realized_pnl REAL,notes TEXT,FOREIGN KEY(position_id) REFERENCES v72_positions(position_id));
        CREATE TABLE IF NOT EXISTS v72_holding_state_current(
          position_id TEXT PRIMARY KEY,current_state TEXT NOT NULL DEFAULT 'NEUTRAL',pending_state TEXT,
          pending_count INTEGER NOT NULL DEFAULT 0,first_seen_at TEXT,confirmed_at TEXT,state_confidence REAL,
          evidence_strength TEXT,reason_json TEXT NOT NULL DEFAULT '[]',evidence_json TEXT NOT NULL DEFAULT '{}',
          data_time TEXT,last_update_at TEXT NOT NULL,FOREIGN KEY(position_id) REFERENCES v72_positions(position_id));
        CREATE TABLE IF NOT EXISTS v72_holding_state_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,position_id TEXT NOT NULL,from_state TEXT,to_state TEXT NOT NULL,
          first_seen_at TEXT,confirmed_at TEXT NOT NULL,confirmation_count INTEGER NOT NULL,
          state_confidence REAL,evidence_strength TEXT,reason_json TEXT NOT NULL DEFAULT '[]',evidence_json TEXT NOT NULL DEFAULT '{}',data_time TEXT,
          FOREIGN KEY(position_id) REFERENCES v72_positions(position_id));
        CREATE INDEX IF NOT EXISTS idx_v72_holding_history ON v72_holding_state_history(position_id,id);
        CREATE TABLE IF NOT EXISTS v72_portfolio_settings(
          key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS v72_portfolio_push_log(
          id INTEGER PRIMARY KEY AUTOINCREMENT,trade_date TEXT NOT NULL,position_id TEXT NOT NULL,state TEXT NOT NULL,
          attempted_at TEXT NOT NULL,success INTEGER NOT NULL DEFAULT 0,error TEXT,
          UNIQUE(trade_date,position_id,state,attempted_at));
        CREATE INDEX IF NOT EXISTS idx_v72_portfolio_push ON v72_portfolio_push_log(trade_date,position_id,state);
        ''')
        c.commit(); c.close()


def create_pending_fill(ts_code: str, name: str = "", notes: str = "", db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    ensure_schema(db_path); pid = str(uuid.uuid4()); now = _now()
    with _LOCK:
        c = _connect(db_path)
        c.execute('INSERT INTO v72_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (pid, _code(ts_code), name, "ACTUAL", "PENDING_FILL", 0, None, None, None, notes, 0, now, now))
        c.commit(); c.close()
    return get_position(pid, db_path)


def create_simulated(ts_code: str, name: str, shares: float, price: float, entry_date: str | None = None,
                     notes: str = "", db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    if shares <= 0 or price <= 0: raise ValueError("shares/price must be positive")
    ensure_schema(db_path); pid = str(uuid.uuid4()); now = _now(); day = entry_date or now[:10]
    with _LOCK:
        c = _connect(db_path)
        c.execute('INSERT INTO v72_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (pid, _code(ts_code), name, "SIMULATED", "SIMULATED", shares, price, day, None, notes, 0, now, now))
        c.commit(); c.close()
    return get_position(pid, db_path)


def confirm_fill(position_id: str, shares: float, price: float, traded_at: str | None = None, db_path: Path = DEFAULT_DB):
    if shares <= 0 or price <= 0: raise ValueError("shares/price must be positive")
    ensure_schema(db_path); t = traded_at or _now()
    with _LOCK:
        c = _connect(db_path); r = c.execute('select * from v72_positions where position_id=?', (position_id,)).fetchone()
        if not r: raise KeyError(position_id)
        if r['position_type'] != 'ACTUAL' or r['status'] not in ('PENDING_FILL', 'HOLDING'): raise ValueError('position not fillable')
        old_sh = float(r['shares'] or 0); old_cost = float(r['cost_price'] or 0); total = old_sh + shares
        cost = (old_sh * old_cost + shares * price) / total
        c.execute('update v72_positions set status="HOLDING",shares=?,cost_price=?,entry_date=coalesce(entry_date,?),last_update_at=? where position_id=?',
                  (total, cost, t[:10], t, position_id))
        c.execute('insert into v72_position_trades values(?,?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()), position_id, 'BUY', shares, price, t, None, ''))
        c.commit(); c.close()
    return get_position(position_id, db_path)


def reduce_position(position_id: str, shares: float, price: float, traded_at: str | None = None, db_path: Path = DEFAULT_DB):
    if shares <= 0 or price <= 0: raise ValueError('shares/price must be positive')
    t = traded_at or _now(); ensure_schema(db_path)
    with _LOCK:
        c = _connect(db_path); r = c.execute('select * from v72_positions where position_id=?', (position_id,)).fetchone()
        if not r or r['status'] not in ('HOLDING', 'SIMULATED'): raise ValueError('not holding')
        held = float(r['shares']); cost = float(r['cost_price'])
        if shares > held: raise ValueError('sell exceeds holding')
        pnl = (price - cost) * shares; remain = held - shares; status = 'CLOSED' if remain == 0 else r['status']
        c.execute('update v72_positions set shares=?,status=?,realized_pnl=realized_pnl+?,last_update_at=? where position_id=?',
                  (remain, status, pnl, t, position_id))
        c.execute('insert into v72_position_trades values(?,?,?,?,?,?,?,?)',
                  (str(uuid.uuid4()), position_id, 'SELL', shares, price, t, pnl, ''))
        c.commit(); c.close()
    return get_position(position_id, db_path)


def update_position(position_id: str, *, manual_stop_price: float | None = None, notes: str | None = None,
                    name: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    ensure_schema(db_path); fields = []; args: list[Any] = []
    if manual_stop_price is not None:
        fields.append('manual_stop_price=?'); args.append(float(manual_stop_price))
    if notes is not None:
        fields.append('notes=?'); args.append(str(notes))
    if name is not None:
        fields.append('name=?'); args.append(str(name))
    if not fields: return get_position(position_id, db_path)
    fields.append('last_update_at=?'); args.append(_now()); args.append(position_id)
    with _LOCK:
        c = _connect(db_path); c.execute('update v72_positions set '+','.join(fields)+' where position_id=?', tuple(args)); c.commit(); c.close()
    return get_position(position_id, db_path)


def get_position(position_id: str, db_path: Path = DEFAULT_DB):
    ensure_schema(db_path)
    with _LOCK:
        c = _connect(db_path); r = c.execute('select * from v72_positions where position_id=?', (position_id,)).fetchone(); c.close()
        return dict(r) if r else None


def list_positions(status: str | None = None, *, position_type: str | None = None, db_path: Path = DEFAULT_DB):
    ensure_schema(db_path); clauses=[]; args=[]
    if status: clauses.append('status=?'); args.append(status)
    if position_type: clauses.append('position_type=?'); args.append(position_type)
    q='select * from v72_positions'
    if clauses: q += ' where ' + ' and '.join(clauses)
    with _LOCK:
        c=_connect(db_path); rows=[dict(x) for x in c.execute(q+' order by last_update_at desc',tuple(args)).fetchall()]; c.close(); return rows


def list_position_trades(position_id: str, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); rows=[dict(r) for r in c.execute('select * from v72_position_trades where position_id=? order by traded_at,trade_id',(position_id,)).fetchall()]; c.close(); return rows


def set_setting(key: str, value: Any, db_path: Path = DEFAULT_DB) -> None:
    ensure_schema(db_path); now=_now()
    with _LOCK:
        c=_connect(db_path); c.execute('insert into v72_portfolio_settings(key,value,updated_at) values(?,?,?) on conflict(key) do update set value=excluded.value,updated_at=excluded.updated_at',
                                       (key, json.dumps(value, ensure_ascii=False), now)); c.commit(); c.close()


def get_setting(key: str, default: Any = None, db_path: Path = DEFAULT_DB) -> Any:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); r=c.execute('select value from v72_portfolio_settings where key=?',(key,)).fetchone(); c.close()
    if not r: return default
    try: return json.loads(r['value'])
    except Exception: return default


def confirm_portfolio_push(enabled: bool, db_path: Path = DEFAULT_DB) -> None:
    set_setting('portfolio_push_user_confirmed', bool(enabled), db_path)
