from __future__ import annotations

from datetime import datetime,timedelta
from typing import Any,Mapping
from zoneinfo import ZoneInfo

from .defense_runtime import (RULE_VERSION,atr14,bar_is_fresh,board_for,break_buffer_pct,initial_stop,minute_detail,
                              profit_stage,proposed_trailing,r_multiple,sell_quantity,update_break)
from .market_archive import archive_daily,archive_minute
from .message import build_holding_message,build_state_transition_message
from .paid_data import PAID_DATA
from .portfolio import list_positions
from .push import send_once
from .runtime_log import log_exception
from .storage import (atomic_update_position_defense,connect,get_position_defense,get_position_state,
                      save_module_health,save_portfolio_risk,transition_position_state)
from .trade_policy import holding_action

CN=ZoneInfo("Asia/Shanghai")


def _num(v:Any)->float|None:
    try:return float(v)
    except (TypeError,ValueError):return None


def _records(value:Any)->list[dict[str,Any]]:
    if hasattr(value,"to_dict"):
        try:return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:return []
    return [dict(x) for x in value] if isinstance(value,list) else []


def _ts(code:str)->str:
    return f"{code}.{'SH' if code.startswith('6') else 'SZ'}"


def _daily_features(code:str,now:datetime)->dict[str,Any]:
    try:
        raw=PAID_DATA.call("daily",cache_key=f"v8:holding:daily:{code}:{now.date()}",ttl_seconds=1800,
          priority="CRITICAL",
          ts_code=_ts(code),start_date=(now-timedelta(days=220)).strftime("%Y%m%d"),end_date=now.strftime("%Y%m%d"))
        rows=sorted(_records(raw),key=lambda x:str(x.get("trade_date") or ""));archive_daily(_ts(code),rows,now)
        closes=[_num(x.get("close")) for x in rows];closes=[x for x in closes if x is not None]
        lows=[_num(x.get("low")) for x in rows[-10:]];lows=[x for x in lows if x is not None]
        if len(closes)<21:return {"status":"UNKNOWN"}
        ma20=sum(closes[-20:])/20;ma60=sum(closes[-60:])/60 if len(closes)>=60 else None
        return {"status":"OK","rows":rows,"close":closes[-1],"ma20":ma20,"ma60":ma60,
                "support":min(lows) if lows else None,"atr14":atr14(rows),"trade_date":rows[-1].get("trade_date"),
                "last_amount_yuan":(_num(rows[-1].get("amount")) or 0)*1000}
    except Exception as exc:
        log_exception("portfolio_daily_features",exc,code=code);return {"status":"UNKNOWN","error":type(exc).__name__}


def _minute_features(code:str,now:datetime)->dict[str,Any]:
    if now.hour*60+now.minute<570:return {"status":"PREOPEN","bar_time":None}
    try:
        raw=PAID_DATA.call("rt_min",cache_key=f"v8:holding:min:{code}:{now:%Y%m%d%H%M}",ttl_seconds=45,
          priority="CRITICAL",
                           ts_code=_ts(code),freq="1MIN")
        rows=_records(raw);archive_minute(_ts(code),rows,now)
        return {**minute_detail(rows),"rows_raw":rows}
    except Exception as exc:
        log_exception("portfolio_minute_features",exc,code=code);return {"status":"UNKNOWN","error":type(exc).__name__}


def _hard_event_risk(code:str)->bool:
    try:
        c=connect();row=c.execute("""select 1 from v8_event_radar where code=? and direction='NEGATIVE'
          and risk_level in ('HIGH','HARD_BLOCK') and created_at>=datetime('now','-7 days') limit 1""",(code,)).fetchone();c.close()
        return bool(row)
    except Exception:return False


def scan_position(position:Mapping[str,Any],now:datetime|None=None)->dict[str,Any]:
    now=(now or datetime.now(CN)).astimezone(CN);code=str(position["code"]);pid=str(position.get("position_id") or code)
    daily=_daily_features(code,now);minute=_minute_features(code,now)
    price=_num(minute.get("price")) or _num(daily.get("close"));cost=_num(position.get("cost_price"));shares=float(position.get("shares") or 0)
    pnl=(price/cost-1)*100 if price and cost else None;board=board_for(code);previous=get_position_defense(pid) or {}
    atr=_num(daily.get("atr14"));support=_num(daily.get("support"));initial=_num(previous.get("initial_stop_price"))
    if initial is None:initial=initial_stop(price,cost,atr,support,board)
    peak=max(x for x in (_num(previous.get("highest_price_since_entry")),price) if x is not None) if price else _num(previous.get("highest_price_since_entry"))
    current_r=r_multiple(price,cost,initial);stage=profit_stage(current_r)
    proposed,stop_reason=proposed_trailing(initial,_num(previous.get("current_trailing_stop")),peak,cost,atr,support,current_r)
    buffer=break_buffer_pct(price,atr,board)
    fresh=bar_is_fresh(minute.get("bar_time"),now);clock=now.hour*60+now.minute
    auction={"status":"NOT_IN_WINDOW"}
    if 565<=clock<570:
        try:
            from .auction_runtime import fetch_assessment
            auction=fetch_assessment(code,str(position.get("name") or ""),now,_num(daily.get("close")),_num(daily.get("last_amount_yuan")))
        except Exception as exc:
            log_exception("portfolio_auction",exc,code=code);auction={"status":"UNKNOWN"}
    if auction.get("status")=="OK" and _num(auction.get("final_price")):
        price=_num(auction.get("final_price"));peak=max(x for x in (_num(previous.get("highest_price_since_entry")),price) if x is not None)
        current_r=r_multiple(price,cost,initial);stage=profit_stage(current_r)
        proposed,stop_reason=proposed_trailing(initial,_num(previous.get("current_trailing_stop")),peak,cost,atr,support,current_r)
    previous_close=_num(daily.get("close"));open_price=_num(minute.get("open_price"))
    if auction.get("status")=="OK":open_price=_num(auction.get("final_price"))
    gap_pct=(open_price/previous_close-1)*100 if open_price and previous_close else None
    gap_limit=max(5,(1.5*atr/previous_close*100) if atr and previous_close else 5)
    before_0936=clock<=576
    weak_gap=bool(before_0936 and gap_pct is not None and gap_pct<=-gap_limit and proposed and price and price<proposed)
    auction_warning=bool(565<=clock<570 and weak_gap)
    gap_risk=bool(570<=clock<=576 and weak_gap)
    hard=_hard_event_risk(code);break_info=update_break(previous,price=price,stop=proposed,buffer_pct=buffer,
      below_vwap=bool(fresh and minute.get("below_vwap")),low_volume=bool(minute.get("low_volume")),
      bar_time=str(minute.get("bar_time") or "") or None,hard_risk=hard,gap_risk=gap_risk)
    bought_today=str(position.get("entry_date") or "")==now.date().isoformat();available=0 if bought_today else shares
    suggested=sell_quantity(shares,stage,available)
    main_rise=bool(price and daily.get("ma20") and daily.get("ma60") and price>daily["ma20"]>daily["ma60"])
    evidence={"price":price,"entry_price":cost,"pnl_pct":pnl,"atr14":atr,"support":support,"ma20":daily.get("ma20"),
      "main_rise":main_rise,"washout":bool(pnl is not None and pnl<0 and not break_info["effective"] and not minute.get("blowoff_reversal")),
      "minute_bad":bool(minute.get("blowoff_reversal")),"sector_bad":False,"exhaustion":bool(minute.get("blowoff_reversal")),
      "hard_risk":hard,"bought_today":bought_today,"break_warning":break_info["effective"] and not break_info["confirmed"],
      "break_confirmed":break_info["confirmed"],"break_count":break_info["count"],"break_required":break_info["required"],
      "break_buffer_pct":buffer,"vwap":minute.get("vwap"),"below_vwap":minute.get("below_vwap"),
      "amount_ratio":minute.get("amount_ratio"),"gap_pct":gap_pct,"gap_risk":gap_risk,
      "auction_warning":auction_warning,"auction":auction,
      "initial_stop":initial,"trailing_stop":proposed,
      "highest_price":peak,"current_r_multiple":current_r,"profit_stage":stage,"shares":shares,
      "available_shares":available,"suggested_sell_shares":suggested,"data_time":minute.get("bar_time"),
      "data_freshness":"FRESH" if fresh else "STALE_OR_UNKNOWN"}
    action=holding_action(evidence)
    defense=atomic_update_position_defense(pid,now.isoformat(timespec="seconds"),{
      **evidence,"initial_stop_price":initial,"current_trailing_stop":proposed,"highest_price_since_entry":peak,
      "highest_close_since_entry":daily.get("close"),"first_break_time":previous.get("first_break_time") or (now.isoformat(timespec="seconds") if break_info["effective"] else None),
      "break_confirmation_count":break_info["count"],"last_break_bar_time":break_info["bar_time"],
      "current_action":action["action"],"action_priority":action["priority"],"stop_update_reason":stop_reason,
      "rule_version":RULE_VERSION})
    evidence["trailing_stop"]=defense.get("current_trailing_stop");evidence["stop_changed"]=defense.get("stop_changed")
    previous_state=get_position_state(pid);previous_code=str(previous_state.get("current_state")) if previous_state else None
    if action["action"] in {"EXIT","T1_LOCKED_RISK_ALERT"}:state_code="TRUE_WEAKNESS"
    elif previous_code=="TRUE_WEAKNESS" and main_rise and not evidence["minute_bad"]:state_code="SECOND_STRENGTH"
    elif main_rise and not evidence["minute_bad"]:state_code="MAIN_RISE"
    elif evidence["washout"]:state_code="WASHOUT"
    else:state_code="NORMAL"
    names={"TRUE_WEAKNESS":"真实转弱","SECOND_STRENGTH":"二次转强","MAIN_RISE":"主升延续","WASHOUT":"健康洗盘","NORMAL":"正常观察"}
    evidence["state"]=names[state_code];evidence["state_reason"]=str(action.get("reason") or "多证据状态确认")
    changed,_=transition_position_state(pid,state_code,now.isoformat(timespec="seconds"),evidence)
    state_push=(False,"unchanged")
    if changed and daily.get("status")=="OK" and state_code in {"MAIN_RISE","WASHOUT","SECOND_STRENGTH"}:
        state_push=send_once(f"{pid}|STATE|{state_code}|{now.date()}",build_state_transition_message(position,state_code,evidence))
    important=action["action"] not in {"HOLD","OBSERVE"}
    key=f"{now.date()}|{code}|DEFENSE|{action['action']}|{break_info['count']}|{stage}"
    pushed,detail=send_once(key,build_holding_message(position,action,evidence)) if important else (False,"quiet")
    if defense.get("stop_changed") and previous.get("current_trailing_stop") is not None and not important:
        pushed,detail=send_once(f"{now.date()}|{code}|STOP_RAISED|{defense.get('current_trailing_stop')}",
          build_holding_message(position,{"action":"STOP_RAISED","reason":"自动防守价已上移，只升不降"},evidence))
    elif defense.get("stop_changed") and previous.get("current_trailing_stop") is None and not important:
        pushed,detail=send_once(f"{now.date()}|{code}|STOP_ESTABLISHED|{defense.get('current_trailing_stop')}",
          build_holding_message(position,{"action":"STOP_ESTABLISHED","reason":"已依据ATR与近期结构建立自动防守"},evidence))
    return {"position":dict(position),"evidence":evidence,"action":action,"defense":defense,
            "state_push":state_push,"pushed":pushed,"push_detail":detail}


def scan_all(now:datetime|None=None)->list[dict[str,Any]]:
    now=(now or datetime.now(CN)).astimezone(CN);started=datetime.now(CN);results=[]
    for position in list_positions():
        try:results.append(scan_position(position,now))
        except Exception as exc:
            log_exception("portfolio_scan",exc,code=position.get("code"));results.append({"position":position,"status":"DATA_UNAVAILABLE","error":type(exc).__name__})
    elapsed=(datetime.now(CN)-started).total_seconds();status="OK" if len(results)==len(list_positions()) and elapsed<=90 else "DEGRADED"
    save_module_health("PORTFOLIO_MONITOR",status,datetime.now(CN).isoformat(timespec="seconds"),
      {"positions":len(results),"elapsed_seconds":round(elapsed,2),"errors":sum("error" in x for x in results)})
    risk="DEFENSIVE" if any(x.get("action",{}).get("action") in {"EXIT","T1_LOCKED_RISK_ALERT"} for x in results) else "NORMAL"
    save_portfolio_risk(now.isoformat(timespec="seconds"),0,{},None,None,"UNKNOWN",risk,{"positions":len(results),"elapsed_seconds":elapsed})
    return results
