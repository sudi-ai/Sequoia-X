from __future__ import annotations

import json
import math
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import V72_CONFIG
from .research_v72 import assert_no_future_fields
from .signal_lab import DEFAULT_DB, _LOCK, _connect

HORIZONS = (1,2,3,4,5,10)


def net_return_pct(entry: float, exit: float, *, commission_bps: float | None = None,
                   stamp_duty_bps: float | None = None, entry_slippage_bps: float | None = None,
                   exit_slippage_bps: float | None = None, other_cost_bps: float | None = None) -> dict[str, Any]:
    if entry <= 0 or exit <= 0:
        raise ValueError("prices must be positive")
    commission_bps = V72_CONFIG.commission_bps if commission_bps is None else commission_bps
    stamp_duty_bps = V72_CONFIG.stamp_duty_bps if stamp_duty_bps is None else stamp_duty_bps
    entry_slippage_bps = V72_CONFIG.entry_slippage_bps if entry_slippage_bps is None else entry_slippage_bps
    exit_slippage_bps = V72_CONFIG.exit_slippage_bps if exit_slippage_bps is None else exit_slippage_bps
    other_cost_bps = V72_CONFIG.other_cost_bps if other_cost_bps is None else other_cost_bps
    gross = (exit / entry - 1) * 100
    total_bps = commission_bps * 2 + stamp_duty_bps + entry_slippage_bps + exit_slippage_bps + other_cost_bps
    return {
        "gross_return_pct": gross,
        "net_return_pct": gross - total_bps / 100,
        "total_cost_bps": total_bps,
        "commission_bps": commission_bps,
        "stamp_duty_bps": stamp_duty_bps,
        "entry_slippage_bps": entry_slippage_bps,
        "exit_slippage_bps": exit_slippage_bps,
        "other_cost_bps": other_cost_bps,
        "cost_config_version": V72_CONFIG.forward_cost_config_version,
    }


def t1_exit_allowed(entry_trade_date: str, exit_trade_date: str) -> bool:
    return bool(entry_trade_date and exit_trade_date and exit_trade_date > entry_trade_date)


def ensure_schema(db_path: Path = DEFAULT_DB) -> None:
    with _LOCK:
        c = _connect(db_path)
        # signal_lab stage1 may already have v72_episodes without direction; migrate before creating indexes.
        ep_cols={r[1] for r in c.execute('pragma table_info(v72_episodes)').fetchall()}
        if ep_cols and 'direction' not in ep_cols:
            c.execute("alter table v72_episodes add column direction TEXT NOT NULL DEFAULT 'LONG'")
        c.executescript('''
        CREATE TABLE IF NOT EXISTS v72_forward_snapshots(
          snapshot_id TEXT PRIMARY KEY,episode_id TEXT,trade_id TEXT,code TEXT NOT NULL,signal_time TEXT NOT NULL,
          signal_trade_date TEXT NOT NULL,entry_reference_price REAL,board_type TEXT,market_regime TEXT,
          current_score REAL,research_score REAL,feature_json TEXT NOT NULL,research_json TEXT NOT NULL,
          data_cutoff_time TEXT NOT NULL,cost_config_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_v72_forward_code_date ON v72_forward_snapshots(code,signal_trade_date);
        CREATE TABLE IF NOT EXISTS v72_forward_outcomes(
          snapshot_id TEXT NOT NULL,horizon INTEGER NOT NULL,matured INTEGER NOT NULL DEFAULT 0,
          mature_trade_date TEXT,close_return_pct REAL,net_return_pct REAL,mfe_pct REAL,mae_pct REAL,
          max_drawdown_pct REAL,holding_trade_days INTEGER,fill_status TEXT,verification_level TEXT,
          details_json TEXT NOT NULL DEFAULT '{}',updated_at TEXT NOT NULL,
          PRIMARY KEY(snapshot_id,horizon),FOREIGN KEY(snapshot_id) REFERENCES v72_forward_snapshots(snapshot_id));
        CREATE TABLE IF NOT EXISTS v72_episodes(
          episode_id TEXT PRIMARY KEY,code TEXT NOT NULL,direction TEXT NOT NULL,episode_start_date TEXT NOT NULL,
          dedupe_key TEXT NOT NULL,signal_type TEXT NOT NULL,parent_episode_id TEXT,status TEXT NOT NULL DEFAULT 'OPEN',created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_v72_episode_code ON v72_episodes(code,direction,episode_start_date);
        CREATE TABLE IF NOT EXISTS v72_trades(
          trade_id TEXT PRIMARY KEY,episode_id TEXT NOT NULL,entry_attempt_no INTEGER NOT NULL,signal_type TEXT NOT NULL,
          entry_time TEXT,entry_price REAL,exit_time TEXT,exit_price REAL,status TEXT NOT NULL,realized_return_pct REAL,
          FOREIGN KEY(episode_id) REFERENCES v72_episodes(episode_id));
        ''')
        # backward-compatible migration for earlier stage1 tables
        def add(table: str, name: str, decl: str):
            cols={r[1] for r in c.execute(f'pragma table_info({table})').fetchall()}
            if name not in cols: c.execute(f'alter table {table} add column {name} {decl}')
        add('v72_episodes','direction',"TEXT NOT NULL DEFAULT 'LONG'")
        c.commit(); c.close()


def freeze_snapshot(*, code: str, signal_time: str, entry_reference_price: float | None,
                    features: Mapping[str, Any], research: Mapping[str, Any], current_score: float | None = None,
                    research_score: float | None = None, board_type: str | None = None, market_regime: str | None = None,
                    episode_id: str | None = None, trade_id: str | None = None,
                    db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    assert_no_future_fields(features)
    assert_no_future_fields(research)
    ensure_schema(db_path)
    sid=str(uuid.uuid4()); now=datetime.now().astimezone().isoformat(timespec='seconds')
    clean=str(code).split('.')[0].zfill(6)
    costs={
      'commission_bps':V72_CONFIG.commission_bps,'stamp_duty_bps':V72_CONFIG.stamp_duty_bps,
      'entry_slippage_bps':V72_CONFIG.entry_slippage_bps,'exit_slippage_bps':V72_CONFIG.exit_slippage_bps,
      'other_cost_bps':V72_CONFIG.other_cost_bps,'cost_config_version':V72_CONFIG.forward_cost_config_version}
    with _LOCK:
        c=_connect(db_path)
        c.execute('''insert into v72_forward_snapshots
        (snapshot_id,episode_id,trade_id,code,signal_time,signal_trade_date,entry_reference_price,board_type,market_regime,current_score,research_score,feature_json,research_json,data_cutoff_time,cost_config_json,created_at)
        values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (sid,episode_id,trade_id,clean,signal_time,signal_time[:10],entry_reference_price,board_type,market_regime,current_score,research_score,
         json.dumps(dict(features),ensure_ascii=False,default=str),json.dumps(dict(research),ensure_ascii=False,default=str),signal_time,json.dumps(costs,ensure_ascii=False),now))
        c.commit(); row=c.execute('select * from v72_forward_snapshots where snapshot_id=?',(sid,)).fetchone(); c.close(); return dict(row)


def _clean_daily_rows(rows: Iterable[Mapping[str, Any]], signal_date: str) -> list[dict[str, Any]]:
    out=[]
    for r0 in rows:
        r=dict(r0); d=str(r.get('trade_date') or '').replace('-','')[:8]
        if not d: continue
        iso=f'{d[:4]}-{d[4:6]}-{d[6:8]}'
        if iso <= signal_date:  # strict T+1 / no D0 outcome
            continue
        try:
            o,h,l,c=[float(r[k]) for k in ('open','high','low','close')]
            if not all(math.isfinite(x) and x>0 for x in (o,h,l,c)): continue
        except Exception: continue
        out.append({'trade_date':iso,'open':o,'high':h,'low':l,'close':c,
                    'suspended':bool(r.get('suspended')),'limit_up_locked':bool(r.get('limit_up_locked') or r.get('one_word_limit_up')),
                    'limit_down_locked':bool(r.get('limit_down_locked') or r.get('one_word_limit_down'))})
    out.sort(key=lambda r:r['trade_date'])
    # dedupe by trading date deterministically: keep last sorted input occurrence
    unique={r['trade_date']:r for r in out}
    return [unique[d] for d in sorted(unique)]


def settle_snapshot(snapshot_id: str, daily_rows: Sequence[Mapping[str, Any]], *, db_path: Path = DEFAULT_DB,
                    verification_level: str = 'daily_approx') -> list[dict[str, Any]]:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); snap=c.execute('select * from v72_forward_snapshots where snapshot_id=?',(snapshot_id,)).fetchone(); c.close()
    if not snap: raise KeyError(snapshot_id)
    entry=float(snap['entry_reference_price'] or 0)
    if entry<=0: raise ValueError('snapshot has no valid entry_reference_price')
    rows=_clean_daily_rows(daily_rows,str(snap['signal_trade_date']))
    results=[]; now=datetime.now().astimezone().isoformat(timespec='seconds')
    for h in HORIZONS:
        mature=len(rows)>=h
        subset=rows[:h] if mature else rows
        close_ret=net_ret=mfe=mae=max_dd=None; mature_date=None; fill_status='MATURE' if mature else 'IMMATURE'
        if mature:
            target=subset[-1]; mature_date=target['trade_date']; close_ret=(target['close']/entry-1)*100
            costs=json.loads(snap['cost_config_json'] or '{}')
            nr=net_return_pct(entry,target['close'],commission_bps=costs.get('commission_bps'),stamp_duty_bps=costs.get('stamp_duty_bps'),
                              entry_slippage_bps=costs.get('entry_slippage_bps'),exit_slippage_bps=costs.get('exit_slippage_bps'),other_cost_bps=costs.get('other_cost_bps'))
            net_ret=nr['net_return_pct']; mfe=(max(r['high'] for r in subset)/entry-1)*100; mae=(min(r['low'] for r in subset)/entry-1)*100; max_dd=mae
            if target.get('limit_down_locked'): fill_status='UNFILLABLE_EXIT'
        details={'rows_used':len(subset),'signal_trade_date':snap['signal_trade_date'],'strict_t1':True,'verification_level':verification_level}
        with _LOCK:
            c=_connect(db_path); c.execute('''insert into v72_forward_outcomes(snapshot_id,horizon,matured,mature_trade_date,close_return_pct,net_return_pct,mfe_pct,mae_pct,max_drawdown_pct,holding_trade_days,fill_status,verification_level,details_json,updated_at)
            values(?,?,?,?,?,?,?,?,?,?,?,?,?,?) on conflict(snapshot_id,horizon) do update set matured=excluded.matured,mature_trade_date=excluded.mature_trade_date,close_return_pct=excluded.close_return_pct,net_return_pct=excluded.net_return_pct,mfe_pct=excluded.mfe_pct,mae_pct=excluded.mae_pct,max_drawdown_pct=excluded.max_drawdown_pct,holding_trade_days=excluded.holding_trade_days,fill_status=excluded.fill_status,verification_level=excluded.verification_level,details_json=excluded.details_json,updated_at=excluded.updated_at''',
            (snapshot_id,h,int(mature),mature_date,close_ret,net_ret,mfe,mae,max_dd,h if mature else len(subset),fill_status,verification_level,json.dumps(details,ensure_ascii=False),now)); c.commit(); row=c.execute('select * from v72_forward_outcomes where snapshot_id=? and horizon=?',(snapshot_id,h)).fetchone(); c.close(); results.append(dict(row))
    return results


def get_or_create_episode(code: str, trade_date: str, *, direction: str='LONG', signal_type: str='FIRST_SIGNAL',
                          parent_episode_id: str | None=None, db_path: Path=DEFAULT_DB) -> dict[str,Any]:
    ensure_schema(db_path); clean=str(code).split('.')[0].zfill(6); now=datetime.now().astimezone().isoformat(timespec='seconds')
    # Use prior daily_prices rows to measure true trading-day distance when available.
    with _LOCK:
        c=_connect(db_path); prior=c.execute('select * from v72_episodes where code=? and direction=? and status="OPEN" order by episode_start_date desc limit 1',(clean,direction)).fetchone()
        reuse=False
        if prior:
            cnt=c.execute('select count(distinct trade_date) n from daily_prices where code=? and trade_date>? and trade_date<=?',(clean,prior['episode_start_date'].replace('-',''),trade_date.replace('-',''))).fetchone()['n']
            if cnt and int(cnt)<=V72_CONFIG.episode_dedupe_days: reuse=True
            elif not cnt:
                from datetime import date
                try: reuse=(date.fromisoformat(trade_date)-date.fromisoformat(prior['episode_start_date'])).days <= 30
                except Exception: reuse=False
        if reuse:
            row=dict(prior); c.close(); return row
        eid=str(uuid.uuid4()); key=f'{clean}|{direction}|{trade_date}'
        c.execute('insert into v72_episodes(episode_id,code,direction,episode_start_date,dedupe_key,signal_type,parent_episode_id,status,created_at) values(?,?,?,?,?,?,?,?,?)',
                  (eid,clean,direction,trade_date,key,signal_type,parent_episode_id,'OPEN',now)); c.commit(); row=c.execute('select * from v72_episodes where episode_id=?',(eid,)).fetchone(); c.close(); return dict(row)


def start_trade(episode_id: str, *, signal_type: str, entry_time: str | None=None, entry_price: float | None=None,
                db_path: Path=DEFAULT_DB) -> dict[str,Any]:
    ensure_schema(db_path); now=entry_time or datetime.now().astimezone().isoformat(timespec='seconds'); tid=str(uuid.uuid4())
    with _LOCK:
        c=_connect(db_path); n=c.execute('select count(*) n from v72_trades where episode_id=?',(episode_id,)).fetchone()['n']; attempt=int(n)+1
        c.execute('insert into v72_trades(trade_id,episode_id,entry_attempt_no,signal_type,entry_time,entry_price,status) values(?,?,?,?,?,?,?)',
                  (tid,episode_id,attempt,signal_type,now,entry_price,'OPEN')); c.commit(); row=c.execute('select * from v72_trades where trade_id=?',(tid,)).fetchone(); c.close(); return dict(row)


def close_trade(trade_id: str, *, exit_time: str, exit_price: float, db_path: Path=DEFAULT_DB) -> dict[str,Any]:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); r=c.execute('select * from v72_trades where trade_id=?',(trade_id,)).fetchone()
        if not r: c.close(); raise KeyError(trade_id)
        if not r['entry_time'] or not t1_exit_allowed(str(r['entry_time'])[:10],exit_time[:10]): c.close(); raise ValueError('T+1: D0 exit is not allowed')
        ret=net_return_pct(float(r['entry_price']),float(exit_price))['net_return_pct'] if r['entry_price'] else None
        c.execute('update v72_trades set exit_time=?,exit_price=?,status="CLOSED",realized_return_pct=? where trade_id=?',(exit_time,exit_price,ret,trade_id)); c.commit(); row=c.execute('select * from v72_trades where trade_id=?',(trade_id,)).fetchone(); c.close(); return dict(row)

def settle_all_from_daily_prices(*, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Settle all V7.2 snapshots from locally ingested daily_prices only.

    This function never requests future data during signal creation. It is intended
    for after-close maintenance after daily_prices has been validated/ingested.
    """
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); snaps=[dict(r) for r in c.execute('select * from v72_forward_snapshots order by signal_time').fetchall()]; c.close()
    settled=0; errors=0
    for s in snaps:
        with _LOCK:
            c=_connect(db_path); rows=[dict(r) for r in c.execute('select * from daily_prices where code=? order by trade_date',(s['code'],)).fetchall()]; c.close()
        try:
            settle_snapshot(s['snapshot_id'],rows,db_path=db_path); settled+=1
        except Exception as exc:
            errors+=1
            try:
                from .runtime_log_v72 import log_event
                log_event("forward_settle", "Forward结算失败", code=str(s.get('code') or ''), exc=exc, degraded=True, event="forward_settle")
            except Exception:
                pass
    return {'snapshots':len(snaps),'settled':settled,'errors':errors}
