from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import V72_CONFIG
from .data_layer import DATA_LAYER
from .holding_state_v72 import classify_holding, observe_holding_state
from .paid_enrichment import enrich_candidate
from .portfolio import get_setting, list_positions
from .research_v72 import evaluate_research
from .signal_lab import DEFAULT_DB

CN_TZ = ZoneInfo("Asia/Shanghai")


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None: return []
    if hasattr(value,"to_dict"):
        try: return [dict(x) for x in value.to_dict(orient="records")]
        except Exception: return []
    if isinstance(value,list): return [dict(x) for x in value if isinstance(x,Mapping)]
    return []


def _num(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None


def _ma(vals,n): return sum(vals[-n:])/n if len(vals)>=n else None


def _latest_sector_strength(code: str, db_path: Path = DEFAULT_DB) -> tuple[float | None, str]:
    """Reuse the latest V6/V7 sector snapshot; never invent a missing sector score."""
    try:
        from .signal_lab import _LOCK, _connect
        with _LOCK:
            c = _connect(db_path)
            row = c.execute(
                "select snapshot_json from signal_events where code=? order by signal_time desc,id desc limit 1",
                (str(code).split('.')[0].zfill(6),),
            ).fetchone()
            c.close()
        if not row:
            return None, 'UNAVAILABLE'
        snap = json.loads(row['snapshot_json'] or '{}')
        value = _num(snap.get('sector_score'))
        if value is None:
            sector = snap.get('sector') if isinstance(snap.get('sector'), Mapping) else {}
            value = _num(sector.get('score'))
        if value is None:
            return None, 'UNAVAILABLE'
        return max(0.0, min(1.0, value / 100.0 if value > 1 else value)), 'V6_V7_SHARED_SNAPSHOT'
    except Exception:
        return None, 'UNAVAILABLE'


def _exhaustion_score(features: Mapping[str,Any], minute: Mapping[str,Any]) -> float:
    """Conservative multi-evidence exhaustion score; missing evidence contributes zero."""
    score = 0.0
    if bool(minute.get('blowoff_reversal')):
        score += 0.45
    pullback = _num(minute.get('pullback_from_5m_peak_pct'))
    accel = _num(minute.get('amount_accel_3v3'))
    gap = _num(features.get('ma20_gap_pct'))
    vol = _num(features.get('volume_ratio'))
    if pullback is not None and pullback <= -1.2:
        score += 0.25
    if accel is not None and accel >= 2.2 and pullback is not None and pullback < 0:
        score += 0.15
    if gap is not None and gap >= 8 and vol is not None and vol >= 1.8:
        score += 0.20
    return round(min(1.0, score), 4)


def compute_daily_features(rows: list[Mapping[str,Any]]) -> dict[str,Any]:
    clean=[]
    for r in rows:
        c=_num(r.get('close')); h=_num(r.get('high')); l=_num(r.get('low')); o=_num(r.get('open')); vol=_num(r.get('vol') or r.get('volume')); amt=_num(r.get('amount'))
        d=str(r.get('trade_date') or '')
        if None in (c,h,l,o) or not d: continue
        clean.append({'trade_date':d,'close':c,'high':h,'low':l,'open':o,'vol':vol,'amount':amt})
    clean.sort(key=lambda r:r['trade_date'])
    closes=[r['close'] for r in clean]; highs=[r['high'] for r in clean]; lows=[r['low'] for r in clean]
    out={'missing_fields':[]}
    if not closes: return {**out,'missing_fields':['daily_history']}
    close=closes[-1]; out['close']=close
    for n in (5,10,20,30,60): out[f'ma{n}']=_ma(closes,n)
    if out['ma20']:
        out['ma20_gap_pct']=(close/out['ma20']-1)*100
    if out['ma60']:
        out['ma60_gap_pct']=(close/out['ma60']-1)*100
    if len(closes)>=30:
        prev=sum(closes[-30:-10])/20; out['ma20_slope10']=(out['ma20']/prev-1)*100 if prev else None
    for n in (5,20,60): out[f'ret{n}']=(close/closes[-n-1]-1)*100 if len(closes)>n else None
    out['drawdown20_pct']=(close/max(highs[-20:])-1)*100 if len(highs)>=20 else None
    if len(clean)>=15:
        trs=[]
        for i in range(1,len(clean)):
            pc=clean[i-1]['close']; r=clean[i]; trs.append(max(r['high']-r['low'],abs(r['high']-pc),abs(r['low']-pc)))
        atr=sum(trs[-14:])/14; out['atr14_pct']=atr/close*100 if close else None
    vols=[r['vol'] for r in clean if r['vol'] is not None]; amts=[r['amount'] for r in clean if r['amount'] is not None]
    out['volume_ratio']=vols[-1]/(sum(vols[-20:])/min(20,len(vols))) if vols else None
    out['amount_ratio']=amts[-1]/(sum(amts[-20:])/min(20,len(amts))) if amts else None
    return out


def fetch_daily_features(code:str, now:datetime|None=None)->dict[str,Any]:
    now=(now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    start=(now-timedelta(days=180)).strftime('%Y%m%d'); end=now.strftime('%Y%m%d'); ts=f'{code.split(".")[0].zfill(6)}.{"SH" if code.startswith("6") else "SZ"}'
    try:
        raw=DATA_LAYER.relay_call('daily',cache_key=f'v72:daily:{code}:{end}',ttl_seconds=3600,priority='holding',ts_code=ts,start_date=start,end_date=end)
        return compute_daily_features(_records(raw))
    except Exception:
        return {'missing_fields':['daily_history']}


def scan_position(position:Mapping[str,Any], *, now:datetime|None=None, db_path:Path=DEFAULT_DB)->dict[str,Any]:
    now=(now or datetime.now(CN_TZ)).astimezone(CN_TZ); code=str(position['ts_code'])
    paid=enrich_candidate(code, now=now, name=str(position.get("name") or ""), market_is_open=True, priority="holding")
    daily=fetch_daily_features(code,now)
    minute=paid.get('realtime_minute') if isinstance(paid.get('realtime_minute'),Mapping) else {}
    rtk=paid.get('realtime_daily') if isinstance(paid.get('realtime_daily'),Mapping) else {}
    current=_num(minute.get('last')) or _num(rtk.get('current_price')) or daily.get('close')
    features=dict(daily); features['board_type']='科创板' if code.startswith('688') else ('创业板' if code.startswith(('300','301')) else '主板')
    features['market_regime']=paid.get('macro',{}).get('macro_risk') if isinstance(paid.get('macro'),Mapping) else None
    research=evaluate_research(features)
    ann=paid.get('announcement') if isinstance(paid.get('announcement'),Mapping) else {}
    news=paid.get('news') if isinstance(paid.get('news'),Mapping) else {}
    chip=paid.get('chip_cost') if isinstance(paid.get('chip_cost'),Mapping) else {}
    report=paid.get('report') if isinstance(paid.get('report'),Mapping) else {}
    minute_fresh=str(minute.get('freshness_status') or '') == 'FRESH'
    sector_strength,sector_source=_latest_sector_strength(code,db_path)
    features['ma20_slope']=features.get('ma20_slope10')
    chip_winner=_num(chip.get('winner_rate'))
    chip_pressure=bool(chip_winner is not None and chip_winner>=85 and chip.get('freshness_status') in {'FRESH','RECENT'})
    evidence={**features,**research,'announcement_risk_level':ann.get('announcement_risk_level','UNKNOWN'),'data_fresh':minute_fresh,
              'minute_quality':minute.get('quality','UNKNOWN'),'blowoff_reversal':bool(minute.get('blowoff_reversal')),
              'sector_strength':sector_strength,'sector_strength_source':sector_source,
              'exhaustion_score':_exhaustion_score(features,minute),
              'news_risk_level':news.get('news_risk_level','UNKNOWN'),'news_risk_reasons':news.get('news_risk_reasons') or [],
              'chip_pressure_high':chip_pressure,'chip_winner_rate':chip_winner,
              'report_count_30d':report.get('report_count_30d'),'report_rating_change':report.get('rating_change'),
              'current_price':current,'data_time':minute.get('data_time') or rtk.get('data_time')}
    if str(position.get('status')) == 'CLOSED':
        ann_level=str(evidence.get('announcement_risk_level') or 'UNKNOWN')
        fresh=bool(evidence.get('data_fresh'))
        main=float(research.get('main_rise_similarity') or 0); td=float(research.get('true_decline_risk_similarity') or 0)
        slope=features.get('ma20_slope10'); gap=features.get('ma20_gap_pct')
        second_ok = V72_CONFIG.second_strength_enable and fresh and ann_level not in {'HARD_BLOCK','HIGH'} and main>=0.68 and td<0.5 and (slope is not None and slope>0) and (gap is not None and gap>=0)
        cls={'state':'SECOND_STRENGTH' if second_ok else 'NEUTRAL','confidence':main if second_ok else .4,
             'reasons':['退出后结构、趋势与实时数据重新改善'] if second_ok else ['退出后尚未形成二次转强确认'],
             'evidence_strength':'MEDIUM' if second_ok else 'LOW'}
    else:
        cls=classify_holding(evidence)
    state=observe_holding_state(str(position['position_id']),cls,evidence=evidence,data_time=evidence.get('data_time'),observed_at=now.isoformat(timespec='seconds'),db_path=db_path)
    cost=_num(position.get('cost_price')); shares=_num(position.get('shares')) or 0
    unrealized=(current-cost)*shares if current is not None and cost is not None else None
    unrealized_pct=(current/cost-1)*100 if current is not None and cost else None
    return {'position':dict(position),'current_price':current,'unrealized_pnl':unrealized,'unrealized_pct':unrealized_pct,'features':features,'research':research,'evidence':evidence,'classification':cls,'state':state,'paid':paid}


def scan_all_positions(*, now:datetime|None=None, db_path:Path=DEFAULT_DB, push:bool=True)->list[dict[str,Any]]:
    results=[]
    from datetime import datetime as _dt
    for pos in list_positions(db_path=db_path):
        if pos['status'] not in ('HOLDING','SIMULATED','CLOSED'): continue
        if pos['status']=='CLOSED':
            try:
                last=_dt.fromisoformat(str(pos.get('last_update_at'))).astimezone(CN_TZ)
                if ((now or _dt.now(CN_TZ)).astimezone(CN_TZ)-last).days > int(V72_CONFIG.second_strength_window_days*1.6):
                    continue
            except Exception:
                continue
        try:
            r=scan_position(pos,now=now,db_path=db_path); results.append(r)
            if push and r['state'].get('changed'):
                from .wework_shadow_push import send_portfolio_state
                send_portfolio_state(r,db_path=db_path)
        except Exception as exc:
            results.append({'position':pos,'status':'DATA_UNAVAILABLE','error':type(exc).__name__})
            try:
                from .runtime_log_v72 import log_event
                log_event("portfolio_scan", "单只持仓扫描失败", code=str(pos.get('ts_code') or ''), exc=exc, degraded=True, event="portfolio_scan")
            except Exception:
                pass
    return results
