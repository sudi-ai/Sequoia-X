# -*- coding: utf-8 -*-
import math
def _sigmoid(x): return 1/(1+math.exp(-max(-20,min(20,x))))
def t1_probability(f):
    z=-.45
    z+=(f.get("market_temp",50)-50)/35
    z+=(f.get("sector_strength",50)-50)/28
    z+=(f.get("auction_quality",50)-50)/40
    z+=max(-1.2,min(1.2,f.get("main_flow_score",0)/60))
    z+=max(-1,min(1,(f.get("chip_lock_score",50)-50)/42))
    z+=max(-1,min(1,(f.get("close_strength",50)-50)/35))
    z-=max(0,f.get("daily_pct",0)-6)/4
    z-=f.get("event_risk",0)/45
    z-=f.get("distribution_risk",0)/55
    return round(max(.05,min(.95,_sigmoid(z))),3)
def overnight_risk(f):
    r=45+max(0,f.get("daily_pct",0)-6)*5+f.get("distribution_risk",0)*.35+f.get("event_risk",0)*.4
    r+=max(0,50-f.get("close_strength",50))*.35
    r-=max(0,f.get("main_flow_score",0))*.2
    r-=max(0,f.get("chip_lock_score",50)-50)*.22
    return round(max(0,min(100,r)),1)
