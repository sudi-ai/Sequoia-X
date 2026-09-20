# -*- coding: utf-8 -*-
from datetime import datetime
from config import A_GATE,B_GATE
from t1_model import t1_probability,overnight_risk
from risk_veto import veto

def score_candidate(f):
    s=50
    s+=(f.get("market_temp",50)-50)*.15
    s+=(f.get("sector_strength",50)-50)*.20
    s+=(f.get("auction_quality",50)-50)*.12
    s+=f.get("main_flow_score",0)*.14
    s+=(f.get("chip_lock_score",50)-50)*.16
    s+=(f.get("trend_score",50)-50)*.16
    s+=(f.get("buy_timing_score",50)-50)*.18
    s-=f.get("distribution_risk",0)*.15
    s-=f.get("event_risk",0)*.10
    s+=(f.get("data_quality",1.0)-1.0)*12
    return round(max(0,min(100,s)),1)

def classify_candidate(features):
    f=dict(features)
    f["score"]=score_candidate(f)
    f["t1_probability"]=t1_probability(f)
    f["overnight_risk"]=overnight_risk(f)
    reasons=veto(f)
    if f.get("pit_violations"):reasons.extend(list(f["pit_violations"]))
    if float(f.get("data_quality",1.0))<.65:reasons.append("关键数据质量不足")
    if reasons:pool="C"
    elif (f["score"]>=A_GATE["min_score"] and
          f["t1_probability"]>=A_GATE["min_t1_probability"] and
          f.get("rr",0)>=A_GATE["min_rr"] and
          f.get("daily_pct",0)<=A_GATE["max_daily_pct"] and
          f["overnight_risk"]<=A_GATE["max_overnight_risk"]):
        pool="A"
    elif f["score"]>=B_GATE["min_score"] and f["t1_probability"]>=B_GATE["min_t1_probability"]:
        pool="B"
    else:pool="C"
    f["pool"]=pool
    f["veto"]="；".join(reasons)
    f["needs_deep_review"]=(pool=="A")
    f["ts"]=datetime.now().isoformat(timespec="seconds")
    f["trade_date"]=datetime.now().strftime("%Y%m%d")
    return f
