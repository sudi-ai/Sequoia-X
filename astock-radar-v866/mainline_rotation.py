# -*- coding: utf-8 -*-
"""
主线/接班判断：利用板块连板梯队，而不是只看板块涨幅。
"""
from __future__ import annotations

def rank_sectors(sector_ladders, previous=None):
    prev={x["sector"]:x for x in (previous or [])}
    out=[]
    for x in sector_ladders:
        p=prev.get(x["sector"],{})
        score=40
        score+=x.get("max_board",0)*8
        score+=min(x.get("count",0),8)*4
        score+=x.get("ladder_depth",0)*6
        score+=x.get("has_2plus",0)*5
        delta=x.get("count",0)-p.get("count",0)
        score+=max(-15,min(15,delta*5))
        if x.get("max_board",0)>=3 and x.get("count",0)>=2:stage="主线扩散"
        elif x.get("max_board",0)>=2 and x.get("count",0)>=2:stage="启动扩散"
        elif x.get("count",0)>=2:stage="首板发酵"
        else:stage="独立个股"
        out.append({**x,"rotation_score":round(max(0,min(100,score)),1),"stage":stage,"count_delta":delta})
    return sorted(out,key=lambda z:(z["rotation_score"],z["max_board"]),reverse=True)

def early_mainline_candidates(ranked):
    """
    对盈利更有价值的是“2~3板+低位补涨”的早期结构，而不是最高6~7板。
    """
    return [x for x in ranked if x["stage"] in ("启动扩散","主线扩散") and x["max_board"]<=4][:5]
