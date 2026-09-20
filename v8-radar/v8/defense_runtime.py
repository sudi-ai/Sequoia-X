from __future__ import annotations

import math
from datetime import datetime
from statistics import median
from typing import Any, Mapping


RULE_VERSION = "V8_DEFENSE_1"


def num(value: Any) -> float | None:
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (TypeError,ValueError):
        return None


def board_for(code: str) -> str:
    if code.startswith(("300","301")): return "创业板"
    if code.startswith("688"): return "科创板"
    return "主板"


def atr14(rows: list[Mapping[str,Any]]) -> float | None:
    ordered=sorted(rows,key=lambda x:str(x.get("trade_date") or ""))
    if len(ordered)<15:return None
    values=[]
    for index,row in enumerate(ordered[-15:]):
        high,low=num(row.get("high")),num(row.get("low"))
        previous=num(ordered[len(ordered)-15+index-1].get("close")) if index>0 else num(row.get("pre_close"))
        if high is None or low is None:continue
        values.append(max(high-low,abs(high-previous) if previous is not None else 0,
                          abs(low-previous) if previous is not None else 0))
    return round(sum(values[-14:])/14,6) if len(values)>=14 else None


def initial_stop(price:float|None,cost:float|None,atr:float|None,support:float|None,board:str)->float|None:
    if not price or price<=0:return None
    multiple={"主板":1.7,"创业板":2.0,"科创板":2.2}.get(board,1.7)
    candidates=[]
    if atr and atr>0:candidates.append(price-multiple*atr)
    if support and 0<support<price:candidates.append(support)
    candidates=[x for x in candidates if 0<x<price]
    return round(max(candidates),3) if candidates else None


def r_multiple(price:float|None,cost:float|None,initial:float|None)->float|None:
    if price is None or cost is None or initial is None or cost<=initial:return None
    return round((price-cost)/(cost-initial),4)


def profit_stage(r:float|None)->str:
    if r is None:return "UNKNOWN"
    if r>=3:return "R3"
    if r>=2:return "R2"
    if r>=1.5:return "R1_5"
    if r>=1:return "R1"
    return "PRE_R1"


def proposed_trailing(initial:float|None,previous:float|None,peak:float|None,cost:float|None,
                      atr:float|None,support:float|None,r:float|None)->tuple[float|None,str]:
    candidates=[x for x in (initial,previous) if x is not None]
    reason="INITIAL_STRUCTURE"
    if r is not None and r>=1 and support is not None:
        candidates.append(support);reason="R1_STRUCTURE_SUPPORT"
    if r is not None and r>=1.5 and cost is not None:
        candidates.append(cost);reason="R1_5_COST_PROTECTION"
    if r is not None and r>=2 and peak is not None and atr is not None:
        candidates.append(peak-1.6*atr);reason="R2_PROFIT_PROTECTION"
    if r is not None and r>=3 and peak is not None and atr is not None:
        candidates.append(peak-1.25*atr);reason="R3_TIGHT_PROTECTION"
    valid=[x for x in candidates if x is not None and x>0]
    return (round(max(valid),3),reason) if valid else (None,"UNKNOWN")


def break_buffer_pct(price:float|None,atr:float|None,board:str,spread_pct:float|None=None)->float:
    floor=.3 if board in {"创业板","科创板"} else .2
    atr_part=(.15*atr/price*100) if price and atr else 0
    spread_part=2*(spread_pct or 0)
    return round(max(floor,atr_part,spread_part),4)


def minute_detail(rows:list[Mapping[str,Any]])->dict[str,Any]:
    ordered=sorted(rows,key=lambda x:str(x.get("time") or x.get("trade_time") or ""))
    valid=[r for r in ordered if num(r.get("close")) is not None]
    if not valid:return {"status":"UNKNOWN"}
    last=valid[-1];price=num(last.get("close"));amounts=[num(x.get("amount")) or 0 for x in valid]
    vols=[num(x.get("vol")) or 0 for x in valid]
    total_amount=sum(amounts);total_vol=sum(vols);vwap=total_amount/total_vol if total_vol>0 else None
    recent_base=amounts[-21:-1]
    base=median(recent_base) if recent_base else None
    ratio=amounts[-1]/base if base and base>0 else None
    highs=[num(x.get("high")) or num(x.get("close")) for x in valid[-5:]]
    peak=max(x for x in highs if x is not None) if any(x is not None for x in highs) else price
    pullback=(price/peak-1)*100 if price and peak else None
    blowoff=bool(ratio is not None and ratio>=2.2 and pullback is not None and pullback<=-1.2)
    return {"status":"OK","price":price,"open_price":num(valid[0].get("open")) or num(valid[0].get("close")),
            "vwap":vwap,"below_vwap":bool(vwap and price and price<vwap),
            "amount_ratio":ratio,"low_volume":bool(ratio is not None and ratio<1),
            "pullback_from_5m_peak_pct":pullback,"blowoff_reversal":blowoff,
            "bar_time":last.get("time") or last.get("trade_time"),"rows":len(valid)}


def bar_is_fresh(value:Any,now:datetime,max_age_seconds:int=120)->bool:
    if not value:return False
    text=str(value).strip().replace("Z","+00:00")
    parsed=None
    try:parsed=datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y%m%d %H:%M:%S","%Y%m%d%H%M%S","%H:%M:%S"):
            try:
                parsed=datetime.strptime(text,fmt)
                if fmt=="%H:%M:%S":parsed=parsed.replace(year=now.year,month=now.month,day=now.day)
                break
            except ValueError:continue
    if parsed is None:return False
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=now.tzinfo)
    return 0<=((now-parsed.astimezone(now.tzinfo)).total_seconds())<=max_age_seconds


def sell_quantity(shares:float,stage:str,available:float|None=None)->int:
    available=max(0,float(shares if available is None else available))
    lots=int(available//100)*100
    if lots<200:return 0
    return min(100,lots) if stage in {"R2","R3"} else 0


def update_break(previous:Mapping[str,Any]|None,*,price:float|None,stop:float|None,buffer_pct:float,
                 below_vwap:bool,low_volume:bool,bar_time:str|None,hard_risk:bool=False,
                 gap_risk:bool=False)->dict[str,Any]:
    prior=dict(previous or {});count=int(prior.get("break_confirmation_count") or 0)
    effective=bool(price and stop and price<stop*(1-buffer_pct/100) and below_vwap)
    distinct=bool(bar_time and str(bar_time)!=str(prior.get("last_break_bar_time") or ""))
    if hard_risk or gap_risk:
        return {"effective":True,"count":99,"required":1,"confirmed":True,"first_break_time":prior.get("first_break_time"),"bar_time":bar_time}
    if effective and distinct:count+=1
    elif not effective:count=0
    required=3 if low_volume else 2
    return {"effective":effective,"count":count,"required":required,"confirmed":count>=required,
            "first_break_time":prior.get("first_break_time"),"bar_time":bar_time if effective else None}
