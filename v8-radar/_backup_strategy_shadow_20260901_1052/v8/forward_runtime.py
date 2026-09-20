from __future__ import annotations

import json
from datetime import datetime,timedelta
from typing import Any

from .config import CONFIG
from .paid_data import PAID_DATA
from .storage import connect,save_execution_outcome


DECISION_DAILY_HORIZONS=tuple(f'D{day}' for day in range(1,8))
DECISION_INTRADAY_HORIZONS=('M3','M5','M10','M15','M30','M60','CLOSE')


def register_decision_outcomes(event_key:str,entry_price:float|None)->dict[str,int]:
    """Idempotently register every V8 independent confirmation for later settlement."""
    if not entry_price or float(entry_price)<=0:return {'inserted':0}
    c=connect();inserted=0
    for horizon in DECISION_DAILY_HORIZONS:
        cur=c.execute("""insert or ignore into v8_execution_outcomes
          (event_key,track,horizon,matured,entry_price,details_json) values(?,?,?,?,?,?)""",
          (event_key,'FIXED',horizon,0,float(entry_price),'{}'));inserted+=cur.rowcount
    for horizon in DECISION_INTRADAY_HORIZONS:
        cur=c.execute("""insert or ignore into v8_execution_outcomes
          (event_key,track,horizon,matured,entry_price,details_json) values(?,?,?,?,?,?)""",
          (event_key,'INTRADAY_FIXED',horizon,0,float(entry_price),'{}'));inserted+=cur.rowcount
    c.commit();c.close();return {'inserted':inserted}


def backfill_decision_outcomes()->dict[str,int]:
    """Repair historical V8 confirmations without changing their frozen scores."""
    c=connect();rows=c.execute("""select event_key,executable_price from v8_signal_decisions
      where action='SHADOW_ENTRY_CONFIRMED' and executable_price is not null""").fetchall();c.close()
    inserted=0
    for row in rows:inserted+=register_decision_outcomes(row['event_key'],row['executable_price'])['inserted']
    return {'signals':len(rows),'inserted':inserted}


def _records(value:Any)->list[dict[str,Any]]:
    if hasattr(value,'to_dict'):
        try:return [dict(x) for x in value.to_dict(orient='records')]
        except Exception:return []
    return [dict(x) for x in value] if isinstance(value,list) else []


def _num(v):
    try:return float(v)
    except (TypeError,ValueError):return None


def _bar_time(row:dict[str,Any])->datetime|None:
    raw=str(row.get('bar_time') or row.get('time') or row.get('trade_time') or row.get('datetime') or '')
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y%m%d %H:%M:%S','%Y-%m-%dT%H:%M:%S%z'):
        try:return datetime.strptime(raw,fmt)
        except ValueError:pass
    return None


def _ts_code(code:str)->str:
    clean=str(code).split('.')[0].zfill(6)
    if clean.startswith(('4','8','92')):suffix='BJ'
    elif clean.startswith(('5','6','9')):suffix='SH'
    else:suffix='SZ'
    return f'{clean}.{suffix}'


def _archived_minute_bars(code:str,signal_time:str)->list[dict[str,Any]]:
    """Read the frozen same-day archive before consuming another paid API call."""
    day=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).date().isoformat()
    ts=_ts_code(code);c=connect()
    rows=c.execute("""select bar_time,open,high,low,close,vol_raw,amount_yuan from v8_minute_bars
      where ts_code in (?,?) and substr(bar_time,1,10)=? order by bar_time""",
      (ts,str(code).split('.')[0].zfill(6),day)).fetchall();c.close()
    return [dict(row) for row in rows]


def _archived_daily_bars(code:str,signal_time:str)->list[dict[str,Any]]:
    start=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).strftime('%Y%m%d')
    ts=_ts_code(code);c=connect()
    rows=c.execute("""select trade_date,open,high,low,close,pre_close,pct_chg,vol_raw,amount_yuan
      from v8_daily_bars where ts_code in (?,?) and trade_date>? order by trade_date""",
      (ts,str(code).split('.')[0].zfill(6),start)).fetchall();c.close()
    return [dict(row) for row in rows]


def _fetch_daily_bars(code:str,signal_time:str,now:datetime)->list[dict[str,Any]]:
    """Prefer local evidence and archive any paid fallback for later reproducibility."""
    local=_archived_daily_bars(code,signal_time)
    start=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).date()
    expected=max(0,(now.date()-start).days)
    # Calendar days deliberately over-estimate; only use the API when the local
    # archive is clearly too short. Settlement itself still counts trading rows.
    if local and (len(local)>=7 or len(local)>=min(2,expected)):
        return local
    try:
        raw=PAID_DATA.call('daily',cache_key=f'v8:forward:{code}:{now:%Y%m%d%H}',ttl_seconds=1800,
          priority='BACKGROUND',ts_code=_ts_code(code),
          start_date=(start-timedelta(days=3)).strftime('%Y%m%d'),end_date=now.strftime('%Y%m%d'))
        bars=_records(raw)
        if bars:
            from .market_archive import archive_daily
            archive_daily(_ts_code(code),bars,now)
        refreshed=_archived_daily_bars(code,signal_time)
        return refreshed or sorted(bars,key=lambda x:str(x.get('trade_date') or ''))
    except Exception:
        return local


def _fetch_minute_bars(code:str,signal_time:str,now:datetime)->list[dict[str,Any]]:
    local=_archived_minute_bars(code,signal_time)
    signal_day=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).date()
    # A normal archived session has hundreds of rows. A smaller set can still
    # settle early horizons, while the fallback may repair the current day.
    if len(local)>=3 or signal_day<now.date():
        return local
    try:
        raw=PAID_DATA.call('rt_min',cache_key=f'v8:decision-minute:{code}:{now:%Y%m%d%H%M}',ttl_seconds=300,
          priority='BACKGROUND',ts_code=_ts_code(code),freq='1MIN')
        bars=_records(raw)
        if bars:
            from .market_archive import archive_minute
            archive_minute(_ts_code(code),bars,now)
        refreshed = _archived_minute_bars(code, signal_time)
        # rt_min normally returns the current session.  Never use a current-day
        # response to settle an older signal when that signal day's archive is
        # missing; doing so would introduce forward-looking contamination.
        if refreshed:
            return refreshed
        return bars if signal_day == now.date() else local
    except Exception:
        return local


def _add_market_minutes(stamp:datetime,minutes:int)->datetime|None:
    current=stamp;left=minutes
    while left>0:
        current+=timedelta(minutes=1)
        hm=current.hour*60+current.minute
        if 690<hm<780:current=current.replace(hour=13,minute=0,second=0,microsecond=0);hm=780
        if hm>900:return None
        if 570<=hm<=690 or 780<=hm<=900:left-=1
    return current


def settle_intraday_from_bars(signal_time:str,entry_price:float,bars:list[dict[str,Any]],horizon:str,
                              cost_bps:float)->dict[str,Any]|None:
    signal=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).replace(tzinfo=None)
    parsed=[(t,row) for row in bars if (t:=_bar_time(row)) is not None and t>=signal]
    parsed.sort(key=lambda x:x[0])
    if not parsed:return None
    if horizon=='CLOSE' and (parsed[-1][0].hour,parsed[-1][0].minute)<(14,55):
        return None
    target=parsed[-1][0] if horizon=='CLOSE' else _add_market_minutes(signal,int(horizon[1:]))
    if target is None:return None
    eligible=[(t,row) for t,row in parsed if t<=target]
    after=[(t,row) for t,row in parsed if t>=target]
    if horizon!='CLOSE' and not after:return None
    exit_time,exit_row=(parsed[-1] if horizon=='CLOSE' else after[0]);exit_price=_num(exit_row.get('close'))
    window=[row for t,row in parsed if t<=exit_time]
    if not exit_price or not entry_price:return None
    highs=[x for x in (_num(row.get('high')) for row in window) if x is not None]
    lows=[x for x in (_num(row.get('low')) for row in window) if x is not None]
    gross=(exit_price/entry_price-1)*100;net=gross-cost_bps/100
    return {'matured':1,'return_pct':round(gross,4),'net_return_pct':round(net,4),'cost_bps':cost_bps,
      'mfe_pct':round((max(highs)/entry_price-1)*100,4) if highs else None,
      'mae_pct':round((min(lows)/entry_price-1)*100,4) if lows else None,
      'exit_price':exit_price,'exit_time':exit_time.isoformat(timespec='seconds'),'status':'MATURED'}


def settle_event_intraday(now:datetime|None=None)->dict[str,int]:
    now=now or datetime.now().astimezone();c=connect();rows=c.execute("""select * from v8_event_forward
      where matured=0 and entry_price is not null and horizon in ('M3','M10','M30','M60','CLOSE')""").fetchall();c.close()
    grouped={}
    for row in rows:grouped.setdefault((row['event_id'],row['code'],row['signal_time'],row['entry_price']),[]).append(row['horizon'])
    matured=errors=0
    for (event_id,code,signal_time,entry),horizons in grouped.items():
        try:
            ts=f"{code}.{'SH' if str(code).startswith('6') else ('BJ' if str(code).startswith(('4','8','92')) else 'SZ')}"
            raw=PAID_DATA.call('rt_min',cache_key=f'v8:event-minute:{code}:{now:%Y%m%d%H%M}',ttl_seconds=300,
                               priority='BACKGROUND',ts_code=ts,freq='1MIN')
            bars=_records(raw);c=connect()
            for horizon in horizons:
                result=settle_intraday_from_bars(str(signal_time),float(entry),bars,str(horizon),CONFIG.forward_cost_bps)
                if not result:continue
                c.execute("""update v8_event_forward set matured=1,return_pct=?,mfe_pct=?,mae_pct=?,exit_time=?,exit_price=?,
                  cost_bps=?,net_return_pct=?,status='MATURED',details_json=? where event_id=? and code=? and horizon=?""",
                  (result['return_pct'],result['mfe_pct'],result['mae_pct'],result['exit_time'],result['exit_price'],
                   result['cost_bps'],result['net_return_pct'],json.dumps(result,ensure_ascii=False),event_id,code,horizon));matured+=1
            c.commit();c.close()
        except Exception:errors+=1
    return {'pending':len(rows),'matured':matured,'errors':errors}


def backfill_event_forward_horizons()->dict[str,int]:
    """Add missing research horizons; never rewrite a matured historical outcome."""
    c=connect();rows=c.execute("""select event_id,code,max(name) name,max(signal_time) signal_time,
      max(entry_price) entry_price from v8_event_forward group by event_id,code""").fetchall();inserted=reset=0
    for row in rows:
        signal=str(row['signal_time']);entry=row['entry_price'];entry_time=signal;status='NOT_MATURED'
        try:
            stamp=datetime.fromisoformat(signal.replace('Z','+00:00')).astimezone();minute=stamp.hour*60+stamp.minute
            if stamp.weekday()>=5 or not (565<=minute<=690 or 780<=minute<=900):
                entry=None;entry_time=None;status='PENDING_EXECUTABLE'
                cur=c.execute("""update v8_event_forward set entry_price=null,entry_time=null,status='PENDING_EXECUTABLE'
                  where event_id=? and code=? and matured=0""",(row['event_id'],row['code']));reset+=cur.rowcount
        except Exception:entry=None;entry_time=None;status='PENDING_EXECUTABLE'
        for horizon in ('M3','M10','M30','M60','CLOSE','D1','D3','D5','D10'):
            cur=c.execute("""insert or ignore into v8_event_forward(event_id,code,name,signal_time,entry_price,horizon,
              entry_time,status) values(?,?,?,?,?,?,?,?)""",(row['event_id'],row['code'],row['name'],signal,entry,horizon,entry_time,status));inserted+=cur.rowcount
    c.commit();c.close();return {'groups':len(rows),'inserted':inserted,'reset_unmatured':reset}


def settle_pending(now:datetime|None=None)->dict[str,int]:
    now=now or datetime.now().astimezone(); c=connect()
    rows=c.execute("""select d.event_key,d.code,d.signal_time,d.executable_price,o.horizon from v8_signal_decisions d
      join v8_execution_outcomes o on o.event_key=d.event_key where o.track='FIXED' and o.matured=0""").fetchall();c.close()
    grouped={}
    for row in rows:grouped.setdefault((row['event_key'],row['code'],row['signal_time'],row['executable_price']),[]).append(row['horizon'])
    matured=errors=0
    for (event_key,code,signal_time,entry),horizons in grouped.items():
        try:
            start=datetime.fromisoformat(signal_time).date()
            bars=_fetch_daily_bars(str(code),str(signal_time),now)
            future=[x for x in bars if str(x.get('trade_date') or '')>start.strftime('%Y%m%d')]
            for horizon in horizons:
                days=int(str(horizon)[1:])
                if len(future)<days or not entry:continue
                window=future[:days];exit_price=_num(window[-1].get('close'))
                highs=[_num(x.get('high')) for x in window];lows=[_num(x.get('low')) for x in window]
                highs=[x for x in highs if x is not None];lows=[x for x in lows if x is not None]
                if exit_price is None:continue
                gross=(exit_price/float(entry)-1)*100;net=gross-CONFIG.forward_cost_bps/100
                peaks=[];max_dd=0.0
                for bar in window:
                    h=_num(bar.get('high'));lo=_num(bar.get('low'))
                    if h is not None:peaks.append(h)
                    if peaks and lo is not None:max_dd=min(max_dd,(lo/max(peaks)-1)*100)
                save_execution_outcome(event_key,'FIXED',horizon,{'matured':True,'entry_price':entry,
                  'exit_time':str(window[-1].get('trade_date')),'exit_price':exit_price,'exit_reason':f'FIXED_{horizon}',
                  'gross_return_pct':round(gross,4),'cost_bps':CONFIG.forward_cost_bps,'net_return_pct':round(net,4),
                  'mfe_pct':round((max(highs)/float(entry)-1)*100,4) if highs else None,
                  'mae_pct':round((min(lows)/float(entry)-1)*100,4) if lows else None,'max_drawdown_pct':round(max_dd,4),
                  'holding_trade_days':days});matured+=1
        except Exception:errors+=1
    return {'pending':len(rows),'matured':matured,'errors':errors}


def settle_decision_intraday(now:datetime|None=None)->dict[str,int]:
    """Settle independent V8 intraday checkpoints from the frozen signal price."""
    now=now or datetime.now().astimezone();c=connect();rows=c.execute("""select d.event_key,d.code,d.signal_time,
      o.entry_price,o.horizon from v8_signal_decisions d join v8_execution_outcomes o on o.event_key=d.event_key
      where o.track='INTRADAY_FIXED' and o.matured=0""").fetchall();c.close()
    grouped={}
    for row in rows:
        grouped.setdefault((row['event_key'],row['code'],row['signal_time'],row['entry_price']),[]).append(row['horizon'])
    matured=errors=0
    for (event_key,code,signal_time,entry),horizons in grouped.items():
        try:
            bars=_fetch_minute_bars(str(code),str(signal_time),now)
            for horizon in horizons:
                result=settle_intraday_from_bars(str(signal_time),float(entry),bars,str(horizon),CONFIG.forward_cost_bps)
                if not result:continue
                save_execution_outcome(event_key,'INTRADAY_FIXED',str(horizon),{
                  'matured':True,'entry_price':entry,'exit_time':result['exit_time'],'exit_price':result['exit_price'],
                  'exit_reason':f'INTRADAY_{horizon}','gross_return_pct':result['return_pct'],
                  'cost_bps':CONFIG.forward_cost_bps,'net_return_pct':result['net_return_pct'],
                  'mfe_pct':result['mfe_pct'],'mae_pct':result['mae_pct']});matured+=1
        except Exception:errors+=1
    return {'pending':len(rows),'matured':matured,'errors':errors}


def settle_all_due(now:datetime|None=None)->dict[str,Any]:
    """Idempotent catch-up cycle used after restarts and at both close stages."""
    now=now or datetime.now().astimezone()
    backfill=backfill_decision_outcomes()
    intraday=settle_decision_intraday(now)
    daily=settle_pending(now)
    result={'checked_at':now.isoformat(timespec='seconds'),'backfill':backfill,
            'intraday':intraday,'daily':daily}
    try:
        from .storage import save_module_health
        remaining=intraday['pending']-intraday['matured']+daily['pending']-daily['matured']
        status='OK' if not intraday['errors'] and not daily['errors'] else 'DEGRADED'
        save_module_health('FORWARD_SETTLEMENT',status,now.isoformat(timespec='seconds'),
                           {**result,'remaining_rows':remaining})
    except Exception:
        pass
    return result


def settle_event_forward(now:datetime|None=None)->dict[str,int]:
    now=now or datetime.now().astimezone();c=connect()
    rows=c.execute("select * from v8_event_forward where matured=0 and horizon in ('D1','D3','D5','D10')").fetchall();c.close()
    grouped={}
    for row in rows:grouped.setdefault((row['event_id'],row['code'],row['name'],row['signal_time'],row['entry_price']),[]).append(row['horizon'])
    matured=errors=0
    for (event_id,code,name,signal_time,entry),horizons in grouped.items():
        try:
            signal_stamp=datetime.fromisoformat(str(signal_time).replace('Z','+00:00')).astimezone();start=signal_stamp.date();ts=f"{code}.{'SH' if str(code).startswith('6') else ('BJ' if str(code).startswith(('4','8','92')) else 'SZ')}"
            raw=PAID_DATA.call('daily',cache_key=f'v8:eventforward:{code}:{now.date()}',ttl_seconds=1800,
                               priority='BACKGROUND',ts_code=ts,
              start_date=(start-timedelta(days=3)).strftime('%Y%m%d'),end_date=now.strftime('%Y%m%d'))
            bars=_records(raw);bars.sort(key=lambda x:str(x.get('trade_date') or ''))
            future=[x for x in bars if str(x.get('trade_date') or '')>start.strftime('%Y%m%d')]
            c=connect()
            if entry is None and future:
                entry=_num(future[0].get('open'));entry_time=str(future[0].get('trade_date'))+' 09:25:00'
                if entry:
                    c.execute("update v8_event_forward set entry_price=?,entry_time=?,status='NOT_MATURED' where event_id=? and code=? and matured=0",
                              (entry,entry_time,event_id,code))
            for horizon in horizons:
                days=int(str(horizon)[1:])
                if len(future)<days or not entry:continue
                window=future[:days];exit_price=_num(window[-1].get('close'))
                highs=[_num(x.get('high')) for x in window];lows=[_num(x.get('low')) for x in window]
                highs=[x for x in highs if x is not None];lows=[x for x in lows if x is not None]
                if exit_price is None:continue
                ret=(exit_price/float(entry)-1)*100;net=ret-CONFIG.forward_cost_bps/100
                c.execute("""update v8_event_forward set matured=1,return_pct=?,mfe_pct=?,mae_pct=?,exit_time=?,exit_price=?,
                  cost_bps=?,net_return_pct=?,status='MATURED',details_json=? where event_id=? and code=? and horizon=?""",(round(ret,4),
                  round((max(highs)/float(entry)-1)*100,4) if highs else None,
                  round((min(lows)/float(entry)-1)*100,4) if lows else None,
                  str(window[-1].get('trade_date')),exit_price,CONFIG.forward_cost_bps,round(net,4),
                  json.dumps({'exit_price':exit_price,'mature_date':window[-1].get('trade_date'),'net_return_pct':round(net,4)},ensure_ascii=False),
                  event_id,code,horizon));matured+=1
            c.commit();c.close()
        except Exception:errors+=1
    return {'pending':len(rows),'matured':matured,'errors':errors}
