from __future__ import annotations

import threading,time
from collections import deque
from datetime import datetime
from typing import Any,Mapping

from .config import CONFIG

ALLOWED_APIS={'index_basic','stock_basic','stock_company','index_member_all','fina_mainbz','daily','rt_k','rt_min','rt_min_daily','anns_d','news','major_news','report_rc','cyq_perf',
              'cyq_chips','daily_basic','moneyflow','forecast','trade_cal','stk_auction_o','stk_auction_tick','us_tbr','pro_bar'}


class V8PaidData:
    """V8-local budget guard around the copied, already verified V7.2 relay adapter."""
    def __init__(self):
        self._calls=deque(); self._lock=threading.RLock(); self._cache={}
        self._circuits:dict[str,dict[str,Any]]={}

    @staticmethod
    def _priority(value:str)->str:
        value=str(value or "NORMAL").upper()
        return value if value in {"CRITICAL","CHECKPOINT","NORMAL","BACKGROUND"} else "NORMAL"

    def _available_limit(self,priority:str)->int:
        total=CONFIG.max_paid_calls_per_minute
        if priority=="CRITICAL": return total
        if priority=="CHECKPOINT": return max(1,total-CONFIG.paid_reserve_critical)
        if priority=="NORMAL": return max(1,total-CONFIG.paid_reserve_critical-CONFIG.paid_reserve_checkpoint)
        return max(1,total-CONFIG.paid_reserve_critical-CONFIG.paid_reserve_checkpoint-CONFIG.paid_reserve_normal)

    def _allow(self,api:str="UNKNOWN",units:int=1,priority:str="NORMAL"):
        now=time.monotonic()
        api=str(api or "UNKNOWN");priority=self._priority(priority)
        with self._lock:
            circuit=self._circuits.get(api) or {}
            if now<float(circuit.get("disabled_until") or 0):
                raise RuntimeError(f'V8_PAID_CIRCUIT_OPEN:{api}')
            from .storage import initialize,connect
            initialize(); c=connect()
            try:
                c.execute('BEGIN IMMEDIATE')
                c.execute('delete from v8_api_budget where called_monotonic<?',(now-60,))
                used=int(c.execute('select coalesce(sum(units),0) from v8_api_budget').fetchone()[0])
                if used+units>self._available_limit(priority):
                    c.rollback(); raise RuntimeError('V8_PAID_BUDGET_EXHAUSTED')
                c.execute('insert into v8_api_budget(called_monotonic,units,api,priority) values(?,?,?,?)',
                          (now,units,api,priority)); c.commit()
            finally:c.close()
            self._calls.extend([(now,api,priority)]*units)

    def call(self,api:str,*,cache_key:str,ttl_seconds:int=60,priority:str="NORMAL",**kwargs):
        if api not in ALLOWED_APIS: raise ValueError(f'V8_API_NOT_ALLOWED:{api}')
        now=time.monotonic()
        with self._lock:
            # Historical archive jobs can return thousands of rows.  Remove
            # expired frames before looking up the next key so a month-long
            # backfill cannot retain every response in process memory.
            for key in [key for key,value in self._cache.items() if value[0]<=now]:
                self._cache.pop(key,None)
            cached=self._cache.get(cache_key)
        if cached and cached[0]>now: return cached[1]
        self._allow(api,1,priority)
        try:
            from .tushare_init import call,pro_bar
            value=pro_bar(**kwargs) if api=='pro_bar' else call(api,**kwargs)
            with self._lock:
                self._cache[cache_key]=(now+max(1,ttl_seconds),value)
                self._circuits.pop(api,None)
            return value
        except Exception as exc:
            with self._lock:
                prior=self._circuits.get(api) or {}
                failures=int(prior.get("failures") or 0)+1
                self._circuits[api]={"disabled_until":time.monotonic()+min(120,20*failures),
                                    "failures":failures,"last_error":type(exc).__name__}
            raise

    def candidate_snapshot(self,code:str,name:str='',now:datetime|None=None)->dict[str,Any]:
        """Reuse the mature parser, but keep failure neutral and V8 output isolated."""
        try:
            # News can consume two endpoints; reserve a conservative candidate budget.
            self._allow("candidate_snapshot",10,"CHECKPOINT")
            from v7.paid_enrichment import (_announcement_factor,_minute_factor,_realtime_daily_factor,
                _chip_factor,_auction_factor,_news_factor,_report_factor,_macro_factor)
            return {'status':'OK','announcement':_announcement_factor(code,now),
              'realtime_minute':_minute_factor(code,now,market_is_open=True),
              'realtime_daily':_realtime_daily_factor(code,now,market_is_open=True),
              'chip_cost':_chip_factor(code,now),'auction':_auction_factor(code,now),
              'news':_news_factor(code,now,name=name),'report':_report_factor(code,now),'macro':_macro_factor(now)}
        except Exception as exc:
            return {'status':'DATA_UNAVAILABLE','error':type(exc).__name__}

    def minute_snapshot(self,code:str,now:datetime|None=None)->dict[str,Any]:
        """One-endpoint observation used for outcome settlement; never performs full candidate enrichment."""
        try:
            self._allow("rt_min",1,"CHECKPOINT")
            from v7.paid_enrichment import _minute_factor
            value=_minute_factor(code,now,market_is_open=True)
            return dict(value) if isinstance(value,Mapping) else {'status':'DATA_UNAVAILABLE'}
        except Exception as exc:
            return {'status':'DATA_UNAVAILABLE','error':type(exc).__name__}

    def health(self)->dict[str,Any]:
        now=time.monotonic()
        try:
            from .storage import connect
            c=connect(); count=int(c.execute('select coalesce(sum(units),0) from v8_api_budget where called_monotonic>=?',(now-60,)).fetchone()[0]); c.close()
        except Exception:
            with self._lock: count=len([x for x in self._calls if now-float(x[0])<60])
        with self._lock:
            circuits={api:{"seconds_remaining":max(0,round(float(value.get("disabled_until") or 0)-now,1)),
                           "failures":int(value.get("failures") or 0),
                           "last_error":value.get("last_error")}
                      for api,value in self._circuits.items()
                      if now<float(value.get("disabled_until") or 0)}
        return {'calls_last_minute':count,'limit':CONFIG.max_paid_calls_per_minute,
                'circuit_open':bool(circuits),'circuits':circuits,'cache_items':len(self._cache)}


PAID_DATA=V8PaidData()
