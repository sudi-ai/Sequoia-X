# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime
from tushare_client import get_pro
ALIASES={
"code":["ts_code","code","symbol"],"price":["price","match_price","latest","close"],
"pct":["pct_chg","pct_change","change_pct"],"vol":["vol","volume","match_vol","matched_vol"],
"amount":["amount","match_amount","matched_amount"],"bid_vol":["bid_vol","buy_vol","bid_volume","buy_volume"],
"ask_vol":["ask_vol","sell_vol","ask_volume","sell_volume"],"unmatched":["unmatched","unmatch_vol","unmatched_vol","diff_vol"],
"time":["time","tick_time","trade_time"]}
def _pick(r,key,default=0):
    for k in ALIASES[key]:
        if k in r and r[k] not in (None,""): return r[k]
    return default
def fetch_auction_tick(trade_date=None,ts_code=None):
    fn=getattr(get_pro(),"stk_auction_tick"); kw={}
    if trade_date:kw["trade_date"]=trade_date
    if ts_code:kw["ts_code"]=ts_code
    try:return fn(**kw)
    except TypeError:
        if ts_code:return fn(ts_code=ts_code)
        return fn(trade_date=trade_date or datetime.now().strftime("%Y%m%d"))
def normalize(df):
    if df is None or len(df)==0:return []
    out=[]
    for r in df.to_dict("records"):
        bid=float(_pick(r,"bid_vol",0) or 0); ask=float(_pick(r,"ask_vol",0) or 0)
        out.append({"ts_code":str(_pick(r,"code","")),"price":float(_pick(r,"price",0) or 0),"pct":float(_pick(r,"pct",0) or 0),
                    "vol":float(_pick(r,"vol",0) or 0),"amount":float(_pick(r,"amount",0) or 0),"bid_vol":bid,"ask_vol":ask,
                    "imbalance":round((bid-ask)/max(bid+ask,1),4),"unmatched":float(_pick(r,"unmatched",0) or 0),
                    "time":str(_pick(r,"time","")),"raw":r})
    return out
def quality_score(x,prev=None):
    s=50.; pct=x.get("pct",0); amt=x.get("amount",0); imb=x.get("imbalance",0)
    if .5<=pct<=5:s+=10
    elif pct>7:s-=12
    elif pct<-2:s-=8
    if amt>=50_000_000:s+=15
    elif amt>=10_000_000:s+=8
    elif amt<2_000_000:s-=8
    s+=max(-12,min(12,imb*20))
    if prev:
        old=max(prev.get("amount",0),1); growth=(amt-old)/old
        if growth>.25:s+=8
        elif growth<-.10:s-=12
        oldbid=max(prev.get("bid_vol",0),1); cancel=(prev.get("bid_vol",0)-x.get("bid_vol",0))/oldbid
        if cancel>.30:s-=18
    return max(0,min(100,round(s,1)))
