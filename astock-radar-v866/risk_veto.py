# -*- coding: utf-8 -*-
def veto(f):
    r=[]
    if f.get("st_or_delist"): r.append("ST/退市风险")
    if f.get("unlock_risk",0)>=70:r.append("临近大比例解禁")
    if f.get("reduction_risk",0)>=70:r.append("减持风险")
    if f.get("regulatory_risk",0)>=70:r.append("监管/立案风险")
    if f.get("distribution_risk",0)>=78:r.append("派发风险高")
    if f.get("market_phase")=="退潮":r.append("市场退潮")
    if f.get("rr",0)<1.2:r.append("盈亏比不足")
    if f.get("daily_pct",0)>12:r.append("当日涨幅过高")
    return r
