from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any

from .message import build_evening_review_message,build_heartbeat_message,build_portfolio_summary_message
from .paid_data import PAID_DATA
from .portfolio import list_positions
from .push import send_once
from .storage import connect,initialize


def _within(now:datetime,start:time,end:time)->bool:
    current=now.time().replace(tzinfo=None);return start<=current<=end


def _summary_positions()->list[dict[str,Any]]:
    positions=list_positions();initialize();c=connect();result=[]
    for position in positions:
        row=c.execute("select current_state,details_json from v8_position_state where position_id=?",(position["position_id"],)).fetchone()
        details={}
        if row:
            try: details=json.loads(row["details_json"] or "{}")
            except Exception: details={}
        result.append({**position,"current_state":row["current_state"] if row else "UNKNOWN","details":details})
    c.close();return result


def _review_stats(day:str)->dict[str,Any]:
    initialize();c=connect()
    actions={str(r["action"]):int(r["n"]) for r in c.execute(
      "select action,count(*) n from v8_signal_decisions where substr(signal_time,1,10)=? group by action",(day,)).fetchall()}
    audit={str(r["discovery_status"]):int(r["n"]) for r in c.execute(
      "select discovery_status,count(*) n from v8_miss_audit where trade_date=? group by discovery_status",(day,)).fetchall()}
    forward=[dict(r) for r in c.execute("""select horizon,count(*) n,avg(net_return_pct) mean
      from v8_execution_outcomes where track='FIXED' and matured=1
      and horizon in ('D1','D2','D3','D4','D5','D6','D7')
      group by horizon order by cast(substr(horizon,2) as integer)""").fetchall()]
    discovery=dict(c.execute("""select count(*) total,
      coalesce(sum(case when checkpoint_count>=3 then 1 else 0 end),0) completed,
      coalesce(sum(case when action='DEFENSE_STRONG' then 1 else 0 end),0) defense_strong,
      coalesce(sum(case when signal_family is not null and signal_family<>'' then 1 else 0 end),0) early_shadow,
      avg(first_seen_pct_chg) avg_first_seen_pct,
      avg(latest_live_pct_chg) avg_latest_pct,
      avg(case when confirmed_at is not null then
        (julianday(confirmed_at)-julianday(first_seen_at))*1440.0 end) avg_confirm_minutes
      from v8_discovery_candidates where trade_date=?""",(day,)).fetchone())
    delivery=dict(c.execute("""select count(*) pushed,
      avg(price_move_from_discovery_pct) avg_move,
      avg(case when price_move_from_discovery_pct>4 then 1.0 else 0.0 end)*100 late_over_4_pct
      from v8_signal_delivery where substr(evaluated_at,1,10)=? and state='CONFIRMED'
      and push_status='SENT'""",(day,)).fetchone())
    queue={str(r["status"]):int(r["n"]) for r in c.execute("""select q.status,count(*) n
      from v8_candidate_recheck_queue q join v8_discovery_candidates d on d.event_key=q.event_key
      where d.trade_date=? group by q.status""",(day,)).fetchall()}
    failures=[dict(r) for r in c.execute("""select coalesce(last_error,'UNKNOWN') reason,count(*) n
      from v8_candidate_recheck_queue q join v8_discovery_candidates d on d.event_key=q.event_key
      where d.trade_date=? and q.status='FAILED' group by coalesce(last_error,'UNKNOWN')
      order by n desc limit 5""",(day,)).fetchall()]
    c.close()
    from .event_performance import grouped_event_performance
    event_groups=grouped_event_performance()
    event_forward=[]
    for horizon in ('M3','M10','M30','M60','CLOSE','D1','D2','D3','D4','D5','D6','D7','D10'):
        value=event_groups.get(f'HORIZON:{horizon}')
        if value:event_forward.append({'horizon':horizon,**value})
    discovered=sum(value for key,value in audit.items() if key!="NOT_DISCOVERED")
    return {"actions":actions,"audit_total":sum(audit.values()),"discovered":discovered,
            "blocked":audit.get("BLOCKED",0),"missed":audit.get("NOT_DISCOVERED",0),"forward":forward,
            "event_forward":event_forward,"discovery":discovery,"recheck_queue":queue,
            "recheck_failures":failures,"delivery":delivery}


def _runtime_health()->dict[str,Any]:
    value=dict(PAID_DATA.health());initialize();c=connect()
    row=c.execute("select status,checked_at,details_json from v8_module_health where module='PORTFOLIO_MONITOR'").fetchone();c.close()
    if row:
        value["portfolio_status"]=row["status"];value["portfolio_checked_at"]=row["checked_at"]
        try:value["portfolio_details"]=json.loads(row["details_json"] or "{}")
        except Exception:value["portfolio_details"]={}
    return value


def run_scheduled_notifications(now:datetime|None=None)->list[tuple[str,bool,str]]:
    now=now or datetime.now().astimezone();day=now.date().isoformat();results=[]
    if now.weekday()>=5:return results
    holding_count=len(list_positions())
    slots=[
      ("HEARTBEAT_AM",time(9,30),time(9,40),lambda:build_heartbeat_message("上午",holding_count,_runtime_health())),
      ("SUMMARY_NOON",time(11,30),time(11,40),lambda:build_portfolio_summary_message("午间",_summary_positions())),
      ("HEARTBEAT_PM",time(13,0),time(13,10),lambda:build_heartbeat_message("下午",holding_count,_runtime_health())),
      ("SUMMARY_CLOSE",time(15,5),time(15,15),lambda:build_portfolio_summary_message("收盘",_summary_positions())),
      ("EVENING_REVIEW",time(15,20),time(23,59,59),lambda:build_evening_review_message(_review_stats(day))),
    ]
    for name,start,end,builder in slots:
        if _within(now,start,end):
            ok,detail=send_once(f"{day}|SCHEDULED|{name}",builder());results.append((name,ok,detail))
    return results
