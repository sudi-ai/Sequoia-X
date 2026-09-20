from __future__ import annotations

import threading
import time
from datetime import datetime,time as dtime,timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .message import build_decision_message
from .paid_data import PAID_DATA
from .persistence_scheduler import DueCheck, PersistenceScheduler
from .push import send_once
from .shadow_engine import V8ShadowEngine
from .storage import save_counterfactual, save_observation,save_execution_outcome,connect,initialize
from .config import CONFIG
from .live_context import market_context,sector_context
from .data_validation import validate_quote
from .event_semantics import announcement_texts,classify_event
from .runtime_log import log_exception

CN = ZoneInfo("Asia/Shanghai")
ENGINE = V8ShadowEngine()
SCHEDULER = PersistenceScheduler()
OUTCOME_SCHEDULER = PersistenceScheduler((3,5,10,15,30,60))
_STATE: dict[str, dict[str, Any]] = {}
_OUTCOME_CODES: dict[str,set[str]]={}
_WORKER_STARTED = False
_RECOVERED=False
_WORKER_LOCK = threading.Lock()


def n(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _paid_values(paid: Mapping[str, Any]) -> dict[str, Any]:
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    news = paid.get("news") if isinstance(paid.get("news"), Mapping) else {}
    return {"minute": minute, "ann": ann, "news": news}


def _add_trading_minutes(stamp:datetime,minutes:int)->datetime|None:
    """Add A-share session minutes; lunch is skipped and no horizon rolls into the next day."""
    current=stamp;remaining=int(minutes);morning_end=datetime.combine(stamp.date(),dtime(11,30),tzinfo=CN)
    afternoon_start=datetime.combine(stamp.date(),dtime(13,0),tzinfo=CN);close=datetime.combine(stamp.date(),dtime(15,0),tzinfo=CN)
    if current<datetime.combine(stamp.date(),dtime(9,30),tzinfo=CN):current=datetime.combine(stamp.date(),dtime(9,30),tzinfo=CN)
    while remaining>0:
        if current>=close:return None
        if morning_end<=current<afternoon_start:current=afternoon_start;continue
        session_end=morning_end if current<morning_end else close
        available=int((session_end-current).total_seconds()//60)
        if remaining<=available:return current+timedelta(minutes=remaining)
        remaining-=available;current=afternoon_start if session_end==morning_end else close
    return current


def _checkpoint(job: DueCheck) -> dict[str, Any]:
    payload = dict(job.payload)
    result = handle_v8_legacy_event(**payload, checkpoint_minute=job.checkpoint_minute)
    if result.get("paid_status") == "DATA_UNAVAILABLE" and int(payload.get("_retry") or 0) < 2:
        payload["_retry"] = int(payload.get("_retry") or 0) + 1
        job.payload = payload
        SCHEDULER.retry(job, datetime.now(CN), 30)
    return result


def _outcome_checkpoint(job:DueCheck)->dict[str,Any]:
    payload=dict(job.payload);minute=PAID_DATA.minute_snapshot(str(payload['code']),datetime.now(CN))
    price=n(minute.get('last'));attempt=int(payload.get('_retry') or 0)
    fresh=str(minute.get('freshness_status') or '').upper()=='FRESH'
    if price is None or not fresh:
        if attempt<2:
            payload['_retry']=attempt+1;job.payload=payload;OUTCOME_SCHEDULER.retry(job,datetime.now(CN),30)
        return {'event_key':job.event_key,'horizon':payload['horizon'],'status':'DATA_UNAVAILABLE'}
    entry=n(payload.get('entry_price'));gross=(price/entry-1)*100 if entry else None
    save_execution_outcome(job.event_key,'INTRADAY_FIXED',str(payload['horizon']),{
      'matured':True,'entry_price':entry,'exit_time':datetime.now(CN).isoformat(timespec='seconds'),'exit_price':price,
      'exit_reason':'OBSERVATION_ONLY_T1','gross_return_pct':round(gross,4) if gross is not None else None,
      'cost_bps':0,'net_return_pct':round(gross,4) if gross is not None else None,'holding_trade_days':0,
      'data_time':minute.get('data_time'),'quality':minute.get('quality'),'freshness_status':minute.get('freshness_status')})
    return {'event_key':job.event_key,'horizon':payload['horizon'],'status':'MATURED','return_pct':gross}


def _worker() -> None:
    while True:
        try:
            SCHEDULER.run_due(datetime.now(CN), _checkpoint, max_jobs=4)
            OUTCOME_SCHEDULER.run_due(datetime.now(CN),_outcome_checkpoint,max_jobs=8)
        except Exception as exc:
            log_exception('persistence_worker',exc)
        time.sleep(5)


def _ensure_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        threading.Thread(target=_worker, name="V8PersistenceWorker", daemon=True).start()
        _WORKER_STARTED = True


def _recover_outcome_jobs(now:datetime)->None:
    global _RECOVERED
    if _RECOVERED:return
    _RECOVERED=True
    try:
        initialize()
        c=connect();rows=c.execute("""select o.event_key,o.horizon,o.entry_price,d.code,d.signal_time from v8_execution_outcomes o
          join v8_signal_decisions d on d.event_key=o.event_key where o.track='INTRADAY_FIXED' and o.matured=0""").fetchall();c.close()
        for row in rows:
            signal=datetime.fromisoformat(str(row['signal_time']));signal=signal if signal.tzinfo else signal.replace(tzinfo=CN)
            horizon=str(row['horizon']);due=(datetime.combine(signal.date(),dtime(15,0),tzinfo=CN) if horizon=='CLOSE'
              else _add_trading_minutes(signal,int(horizon[1:])))
            if due is not None and due>now:
                checkpoint=999 if horizon=='CLOSE' else int(horizon[1:])
                OUTCOME_SCHEDULER.register_at(str(row['event_key']),due,checkpoint,
                  {'code':row['code'],'entry_price':row['entry_price'],'horizon':horizon})
    except Exception as exc:
        log_exception('recover_outcome_jobs',exc)


def handle_v8_legacy_event(*, candidate: Mapping[str, Any], deep: Mapping[str, Any], tier: str,
                           source: str, sector_item: Mapping[str, Any] | None = None,
                           market_regime: str = "正常", now: datetime | None = None,
                           checkpoint_minute: int = 0, _retry: int = 0) -> dict[str, Any]:
    """Consume copied V6.6 discoveries without changing their score or legacy return path."""
    now = (now or datetime.now(CN)).astimezone(CN)
    code = str(candidate.get("code") or "").split(".")[0].zfill(6)
    day = now.date().isoformat()
    key = f"{day}|{source}|{code}"
    is_new = key not in _STATE
    state = _STATE.setdefault(key, {"first_seen": now, "observations": [], "last_action": None})
    if is_new:
        _ensure_worker()
        _recover_outcome_jobs(now)
        SCHEDULER.register(key, now, {"candidate": dict(candidate), "deep": dict(deep), "tier": tier,
          "source": source, "sector_item": dict(sector_item or {}), "market_regime": market_regime})
        for horizon in ('D1','D3','D5','D10'):
            save_execution_outcome(key,'FIXED',horizon,{'matured':False,'entry_price':n(candidate.get('price'))})
        day_codes=_OUTCOME_CODES.setdefault(day,set())
        if code in day_codes or len(day_codes)<CONFIG.max_enriched_candidates:
            day_codes.add(code);entry=n(candidate.get('price'))
            for minute_h in (3,5,10,15,30,60):
                due=_add_trading_minutes(now,minute_h)
                if due is None:continue
                horizon=f'M{minute_h}';save_execution_outcome(key,'INTRADAY_FIXED',horizon,{'matured':False,'entry_price':entry})
                OUTCOME_SCHEDULER.register_at(key,due,minute_h,{'code':code,'entry_price':entry,'horizon':horizon})
            close_at=datetime.combine(now.date(),dtime(15,0),tzinfo=CN)
            if now<close_at:
                save_execution_outcome(key,'INTRADAY_FIXED','CLOSE',{'matured':False,'entry_price':entry})
                OUTCOME_SCHEDULER.register_at(key,close_at,999,{'code':code,'entry_price':entry,'horizon':'CLOSE'})
        else:
            save_counterfactual(key,'OUTCOME_NOT_SCHEDULED_BUDGET','WATCH','超过每日盘中多周期跟踪上限',{'code':code})

    paid = PAID_DATA.candidate_snapshot(code, str(candidate.get("name") or ""), now) if tier in {"confirm", "fast"} else {}
    pv = _paid_values(paid)
    minute, sector = pv["minute"], dict(sector_item or {})
    live_market=market_context(now);live_sector=sector_context(str(sector.get('sector') or ''),now)
    price = n(minute.get("last")) or n(candidate.get("price"))
    obs = {"price":price,"vwap":n(minute.get("vwap")),
           "above_vwap": bool(price and n(minute.get("vwap")) and price >= n(minute.get("vwap"))),
           "order_imbalance": n(minute.get("order_imbalance")), "amount_delta": n(candidate.get("amount_delta")),
           "sector_score": n(sector.get("score") or sector.get("sector_score")),
           "blowoff_reversal": bool(minute.get("blowoff_reversal")),
           "checkpoint_minute":int(checkpoint_minute),
           "evaluation_time":now.isoformat(timespec="seconds"),
           "data_time":minute.get("data_time")}
    state["observations"].append(obs)
    state["observations"] = state["observations"][-4:]
    if checkpoint_minute:
        save_observation(key, checkpoint_minute, now.isoformat(timespec="seconds"),
                         {**obs, "price": price, "vwap": n(minute.get("vwap"))})

    prices=[n(x.get('price')) for x in state['observations'] if n(x.get('price')) is not None]
    derived_pullback=False; derived_second=False
    if len(prices)>=3:
        first,peak,last=prices[0],max(prices),prices[-1]
        derived_pullback=peak>=first*1.005 and peak*0.985<=last<=peak*0.997 and bool(obs['above_vwap'])
        middle_min=min(prices[1:-1]) if len(prices)>2 else first
        derived_second=middle_min<=first*0.997 and last>=first*1.002 and bool(obs['above_vwap'])

    flow = deep.get("flow") if isinstance(deep.get("flow"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep.get("trade"), Mapping) else {}
    quote_validation=(validate_quote(primary_price=n(minute.get('last')),secondary_price=n(candidate.get('price')),
      primary_time=minute.get('data_time'),secondary_time=candidate.get('quote_time')) if checkpoint_minute==0 else
      {'status':'NOT_COMPARABLE_TIME','deviation_pct':None,'sources':2,'reason':'持续度复核时不拿当前价与初始信号价做行情源冲突判断'})
    event_semantic=classify_event(announcement_texts(pv['ann']))
    evidence = {
      "market": {"advance_ratio": live_market.get('advance_ratio') if live_market.get('advance_ratio') is not None else candidate.get("market_advance_ratio"),
        "limit_up_count":live_market.get('limit_up_count'),"limit_down_count":live_market.get('limit_down_count'),
        "index_trend_score": candidate.get("market_score"),"source_status":live_market.get('source_status'),"data_time":live_market.get('data_time')},
      "sector": {"up_ratio": live_sector.get('up_ratio') if live_sector.get('up_ratio') is not None else (sector.get("up_ratio") or sector.get("breadth")),
        "median_return": sector.get("median_return"),"amount_acceleration": sector.get("amount_acceleration"),
        "leader_strength": live_sector.get('leader_strength') if live_sector.get('leader_strength') is not None else sector.get("leader_strength"),
        "second_third_strength":sector.get("second_third_strength"),
        "fund_persistence":live_sector.get('fund_persistence') if live_sector.get('fund_persistence') is not None else sector.get("fund_persistence"),
        "source_status":live_sector.get('source_status'),"data_time":live_sector.get('data_time')},
      "trend": {"ma20_slope10": candidate.get("ma20_slope10"), "ma60_gap_pct": candidate.get("ma60_gap_pct"),
        "ma20_gap_pct": candidate.get("ma20_gap_pct"), "close_position": candidate.get("close_position"),
        "vwap_support": 1 if obs["above_vwap"] else 0, "pullback_support": 1 if candidate.get("pullback_confirmed") else 0,
        "breakout_quality": deep.get("trend_score")},
      "fund": {"active_buy_ratio": minute.get("active_buy_ratio"), "order_imbalance": minute.get("order_imbalance"),
        "amount_acceleration": candidate.get("amount_acceleration"), "large_order_direction": flow.get("large_order_direction"),
        "fund_persistence": flow.get("score")},
      "risk": {"announcement_risk": pv["ann"].get("announcement_risk_level"),
        "news_risk": pv["news"].get("news_risk_level"), "true_decline_risk": candidate.get("true_decline_risk"),
        "limit_up_locked": candidate.get("is_limit_up_locked"), "not_tradeable": candidate.get("not_tradeable"),
        "st_or_delisting":candidate.get("st_or_delisting") or candidate.get("is_st") or candidate.get("delisting_risk"),
        "spread_pct":minute.get("spread_pct") if minute.get("spread_pct") is not None else candidate.get("spread_pct"),
        "liquidity_bad":candidate.get("liquidity_bad") or minute.get("liquidity_bad"),
        "data_validation_status":quote_validation.get('status'),"event_semantic_risk":event_semantic.get('risk_level')},
      "data_validation":quote_validation,
      "event_semantic":event_semantic,
      "board_type": "科创板" if code.startswith("688") else ("创业板" if code.startswith(("300", "301")) else "主板")}
    event = {"event_key": key, "signal_time": state["first_seen"].isoformat(timespec="seconds"), "code": code,
             "evaluation_time":now.isoformat(timespec='seconds'),
             "name": candidate.get("name"), "sector": sector.get("sector"), "source": source,
             "executable_price": price, "price": price, "atr14": candidate.get("atr14"),
             "structural_support": trade.get("defense"), "zone_low": trade.get("zone_low"),
             "zone_high": trade.get("zone_high"), "pullback_confirmed":bool(candidate.get("pullback_confirmed") or derived_pullback),
             "second_strength_confirmed":bool(candidate.get("second_strength_confirmed") or derived_second),
             "checkpoint_minute":int(checkpoint_minute),"data_time":minute.get("data_time"),
             "legacy_tier": tier, "legacy_score": deep.get("buy_score")}
    result = ENGINE.evaluate(candidate=event, evidence=evidence, observations=state["observations"], persist=True)
    action = result["decision"]["action"]
    group = "V8_CONFIRMED" if action == "SHADOW_ENTRY_CONFIRMED" else ("V8_BLOCKED" if action == "BLOCKED" else "V8_WATCH")
    save_counterfactual(key, group, action,
                        ";".join(result["decision"].get("vetoes") or result["decision"].get("reasons") or []), evidence)
    pushed, detail = False, "unchanged"
    if action != state.get("last_action"):
        try:
            pushed, detail = send_once(f"{day}|{code}|{action}", build_decision_message(event, result["decision"]))
        except Exception as exc:
            pushed, detail = False, f"message_error:{type(exc).__name__}"
        state["last_action"] = action
    return {"status": "RECORDED", "event_key": key, "action": action, "checkpoint_minute": checkpoint_minute,
            "paid_status": paid.get("status"), "pushed": pushed, "push_detail": detail,
            "paid_health": PAID_DATA.health()}
