from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

from .storage import connect, initialize,save_execution_outcome
from .config import CONFIG
from datetime import datetime


def upsert_position(code:str,name:str,shares:float,cost_price:float,*,entry_date:str|None=None,
                    manual_stop_price:float|None=None,notes:str="",position_id:str|None=None,path:Path|None=None)->str:
    initialize(path); pid=position_id or uuid.uuid4().hex[:12]; c=connect(path)
    c.execute("""INSERT INTO v8_positions(position_id,code,name,shares,cost_price,entry_date,status,manual_stop_price,notes)
      VALUES(?,?,?,?,?,?,'HOLDING',?,?) ON CONFLICT(position_id) DO UPDATE SET code=excluded.code,name=excluded.name,
      shares=excluded.shares,cost_price=excluded.cost_price,entry_date=excluded.entry_date,status='HOLDING',
      manual_stop_price=excluded.manual_stop_price,notes=excluded.notes,updated_at=CURRENT_TIMESTAMP""",
      (pid,code.split('.')[0].zfill(6),name,float(shares),float(cost_price),entry_date or date.today().isoformat(),manual_stop_price,notes))
    c.commit(); c.close(); return pid


def list_positions(*,path:Path|None=None,status:str="HOLDING")->list[dict[str,Any]]:
    initialize(path); c=connect(path)
    rows=c.execute("select * from v8_positions where status=? order by created_at",(status,)).fetchall(); c.close()
    return [dict(r) for r in rows]


def close_position(position_id:str,*,exit_price:float|None=None,reason:str='MANUAL_EXIT',shares:float|None=None,path:Path|None=None)->None:
    initialize(path); c=connect(path)
    row=c.execute('select * from v8_positions where position_id=?',(position_id,)).fetchone()
    if not row:c.close();raise KeyError('position_not_found')
    sell_shares=min(float(shares if shares is not None else row['shares']),float(row['shares']))
    if exit_price is not None:
        c.execute("insert into v8_position_trades(position_id,trade_time,side,shares,price,reason,cost_bps) values(?,?,?,?,?,?,?)",
          (position_id,datetime.now().astimezone().isoformat(timespec='seconds'),'SELL',sell_shares,float(exit_price),reason,CONFIG.forward_cost_bps))
    remaining=float(row['shares'])-sell_shares
    status='CLOSED' if remaining<=0 else 'HOLDING'
    c.execute("update v8_positions set shares=?,status=?,updated_at=CURRENT_TIMESTAMP where position_id=?",(max(0,remaining),status,position_id))
    c.commit(); c.close()
    if exit_price is not None:
        gross=(float(exit_price)/float(row['cost_price'])-1)*100;net=gross-CONFIG.forward_cost_bps/100
        save_execution_outcome(f"POSITION:{position_id}",'DYNAMIC','FINAL' if status=='CLOSED' else 'PARTIAL',{
          'matured':True,'entry_price':row['cost_price'],'exit_time':datetime.now().astimezone().isoformat(timespec='seconds'),
          'exit_price':float(exit_price),'exit_reason':reason,'gross_return_pct':round(gross,4),'cost_bps':CONFIG.forward_cost_bps,
          'net_return_pct':round(net,4),'holding_trade_days':None,'shares':sell_shares},path)
