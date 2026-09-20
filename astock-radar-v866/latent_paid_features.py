# -*- coding: utf-8 -*-
"""
把已购买的数据能力转成潜伏雷达可用的“证据”，最大化利用但不强依赖某一接口。
字段不存在时返回中性值，不让一个付费端点异常拖垮潜伏扫描。
"""
from __future__ import annotations

def _records(df):
    if df is None:return []
    if isinstance(df,list):return df
    try:return df.to_dict("records")
    except Exception:return []

def _num(r,*keys,default=0.0):
    for k in keys:
        if k in r and r[k] not in (None,""):
            try:return float(r[k])
            except Exception:pass
    return float(default)

def moneyflow_accumulation_score(df):
    """
    支持 Tushare moneyflow / moneyflow_ths 常见字段。
    侧重连续性，而不是单日绝对大单。
    """
    rows=_records(df)
    if not rows:return 50.0
    vals=[]
    for r in rows[:10]:
        net=_num(r,"net_mf_amount","net_amount","net_d5_amount","net_amount_rate",default=0)
        # 若没有净额字段，用大/特大单买卖差近似
        if net==0:
            buy=_num(r,"buy_lg_amount","buy_elg_amount","buy_large_amount","buy_elg_vol","buy_lg_vol",default=0)
            sell=_num(r,"sell_lg_amount","sell_elg_amount","sell_large_amount","sell_elg_vol","sell_lg_vol",default=0)
            net=buy-sell
        vals.append(net)
    pos=sum(1 for x in vals if x>0); neg=sum(1 for x in vals if x<0)
    score=50+(pos-neg)*5
    if vals and sum(vals)>0:score+=10
    return round(max(0,min(100,score)),1)

def liquidity_score(daily_basic_row=None,daily_row=None):
    b=daily_basic_row or {}; d=daily_row or {}
    turnover=_num(b,"turnover_rate","turnover_rate_f",default=0)
    amount=_num(d,"amount",default=0)
    # Tushare daily amount 常见单位为千元，此处只作相对等级，不用于财务精确计算
    score=50
    if 1<=turnover<=10:score+=25
    elif turnover>20:score-=10
    elif 0<turnover<.5:score-=20
    if amount>500000:score+=15
    elif 0<amount<30000:score-=15
    return round(max(0,min(100,score)),1)

def risk_from_unlock(df,ts_code=None):
    rows=_records(df)
    if not rows:return 0.0
    risk=0
    for r in rows:
        if ts_code and str(r.get("ts_code",""))!=str(ts_code):continue
        ratio=_num(r,"float_ratio","float_share_ratio","ratio",default=0)
        if ratio>=10:risk=max(risk,80)
        elif ratio>=5:risk=max(risk,65)
        elif ratio>0:risk=max(risk,40)
    return float(risk)

def top_list_evidence(df,ts_code=None):
    rows=_records(df)
    if not rows:return {"score":50.0,"institution_net":0.0}
    found=[r for r in rows if not ts_code or str(r.get("ts_code",""))==str(ts_code)]
    if not found:return {"score":50.0,"institution_net":0.0}
    score=55.0; net=0.0
    for r in found:
        net+=_num(r,"net_amount","net_buy","net_amt",default=0)
    if net>0:score+=15
    elif net<0:score-=15
    return {"score":round(max(0,min(100,score)),1),"institution_net":net}

def build_extra(*,moneyflow_df=None,daily_basic_row=None,daily_row=None,share_float_df=None,
                top_list_df=None,ts_code=None,sector_heat=50,chip_lock_score=50,
                event_risk=0,distribution_risk=0,data_quality=1.0):
    top=top_list_evidence(top_list_df,ts_code)
    accum=moneyflow_accumulation_score(moneyflow_df)
    # 龙虎榜仅作辅助，不能把一天上榜直接当成长期吸筹
    accum=round(accum*.85+top["score"]*.15,1)
    return {
        "accumulation_score":accum,
        "sector_heat":float(sector_heat),
        "chip_lock_score":float(chip_lock_score),
        "event_risk":float(event_risk),
        "unlock_risk":risk_from_unlock(share_float_df,ts_code),
        "distribution_risk":float(distribution_risk),
        "liquidity_score":liquidity_score(daily_basic_row,daily_row),
        "data_quality":float(data_quality),
        "top_list_score":top["score"],
    }
