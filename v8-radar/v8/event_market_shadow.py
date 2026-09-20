from __future__ import annotations

import json
from datetime import datetime
from typing import Any,Mapping

from .storage import connect,initialize
from .sector_baseline import sector_response


def evaluate_event_market_shadow(event:Mapping[str,Any],now:datetime|None=None)->dict[str,Any]:
    """Record market response only. This function has no decision-engine dependency."""
    beneficiaries=event.get("beneficiaries") if isinstance(event.get("beneficiaries"),list) else []
    affected=event.get("affected_companies") if isinstance(event.get("affected_companies"),list) else []
    direction=str(event.get("direction") or "NEUTRAL")
    targets=affected if direction=="NEGATIVE" and affected else beneficiaries
    investable=[x for x in targets if str(x.get("tier") or "") in {"A","B"}]
    known=[float(x["day_pct"]) for x in investable if x.get("day_pct") is not None]
    initialize();db=connect();codes=[str(x.get('code') or '') for x in investable]
    industries=[]
    for code in codes:
        row=db.execute('select industry from v8_company_profiles where code=?',(code,)).fetchone()
        if row and row['industry']:industries.append(str(row['industry']))
    db.close();sector=sector_response(industries,now)
    up_ratio=sum(x>0 for x in known)/len(known)*100 if known else None
    leader=max(known) if known else None
    leader_confirmed=bool(leader is not None and leader>=7)
    sector_up=sector.get('up_ratio');combined_up=(float(sector_up)*100 if sector_up is not None and float(sector_up)<=1 else sector_up)
    breadth_confirmed=bool((combined_up if combined_up is not None else up_ratio) is not None and
                           (combined_up if combined_up is not None else up_ratio)>=60 and leader_confirmed)
    realization=str(event.get("realization_status") or "UNKNOWN")
    overheated=bool(known and sum(known)/len(known)>=7)  # Shadow marker, not a veto.
    state="EVENT_OVERHEATED" if overheated else ("EVENT_MARKET_RESPONSE" if breadth_confirmed else "EVENT_WATCH")
    result={"event_id":event.get("event_id"),"observed_at":(now or datetime.now().astimezone()).isoformat(timespec="seconds"),
      "event_state":state,"sample_count":len(investable),"known_count":len(known),"up_ratio_pct":up_ratio,
      "leader_pct":leader,"leader_confirmed":leader_confirmed,"breadth_confirmed":breadth_confirmed,
      "volume_ratio":sector.get('volume_ratio'),"vwap_support":None if sector.get('above_vwap_ratio') is None else sector.get('above_vwap_ratio')>=.6,
      "sector_response":sector,"expectation_status":"PRICE_IN" if overheated else realization,
      "mapping_direction":"承压验证" if direction=="NEGATIVE" else "受益验证",
      "action_permission":"ANNOTATION_ONLY","data_limitations":list(sector.get('data_limitations') or [])+
      (["关联公司实时行情不足"] if not known else [])+["该阈值仅用于研究记录，不影响建仓或否决"]}
    initialize();c=connect();c.execute("""insert into v8_event_market_shadow(event_id,observed_at,event_state,
      sample_count,known_count,up_ratio_pct,leader_pct,volume_ratio,vwap_support,expectation_status,details_json)
      values(?,?,?,?,?,?,?,?,?,?,?) on conflict(event_id) do update set observed_at=excluded.observed_at,
      event_state=excluded.event_state,sample_count=excluded.sample_count,known_count=excluded.known_count,
      up_ratio_pct=excluded.up_ratio_pct,leader_pct=excluded.leader_pct,volume_ratio=excluded.volume_ratio,
      vwap_support=excluded.vwap_support,expectation_status=excluded.expectation_status,details_json=excluded.details_json""",
      (result["event_id"],result["observed_at"],state,len(investable),len(known),combined_up if combined_up is not None else up_ratio,leader,result['volume_ratio'],result['vwap_support'],
       result["expectation_status"],json.dumps(result,ensure_ascii=False)));c.commit();c.close();return result
