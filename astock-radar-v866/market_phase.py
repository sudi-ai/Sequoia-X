# -*- coding: utf-8 -*-
def market_temperature(m):
    s=50.0
    s+=(m.get("advance_rate",50)-50)*.35
    s+=min(m.get("limit_up",0),120)*.16
    s-=min(m.get("limit_down",0),80)*.35
    s-=min(m.get("broken_rate",20),80)*.22
    s+=min(m.get("advance_height",2),8)*2.0
    return max(0,min(100,round(s,1)))

def classify_market_phase(m):
    t=market_temperature(m); broken=m.get("broken_rate",20); h=m.get("advance_height",2); breadth=m.get("advance_rate",50)
    if t>=78 and broken>=28: phase="高潮"
    elif t>=65 and breadth>=55 and h>=3: phase="主升"
    elif t>=55 and breadth>=50: phase="启动"
    elif t>=42: phase="修复"
    elif broken>=40 or m.get("limit_down",0)>=30: phase="退潮"
    else: phase="冰点"
    attack={"冰点":.15,"修复":.35,"启动":.70,"主升":1.0,"高潮":.55,"退潮":.10}[phase]
    return {"temperature":t,"phase":phase,"attack_factor":attack}
