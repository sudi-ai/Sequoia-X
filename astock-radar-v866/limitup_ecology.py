# -*- coding: utf-8 -*-
"""
V8.4 连板生态模型

核心不是“谁板数最高”，而是识别：
1. 空间高度；
2. 各梯队数量；
3. 1进2 / 2进3 / 3进4 晋级率；
4. 炸板率 / 高标断板；
5. 板块梯队是否完整；
6. 旧高标退潮与新高标接班；
7. 当前阶段更适合首板/低位接力还是持仓风险管理。
"""
from __future__ import annotations
from collections import defaultdict
from statistics import mean
from config import LIMITUP_ECOLOGY

def ladder(rows):
    out=defaultdict(list)
    for x in rows:
        out[int(x.get("board",1))].append(x)
    return dict(sorted(out.items(),reverse=True))

def promotion_rates(today, yesterday):
    """
    yesterday中N板，today是否成为N+1板。
    返回各梯队晋级率与断板代码。
    """
    tmap={str(x["ts_code"]):int(x.get("board",1)) for x in today}
    groups=defaultdict(list)
    for x in yesterday:
        groups[int(x.get("board",1))].append(x)
    rates={}
    broken={}
    promoted={}
    for b,items in groups.items():
        eligible=len(items)
        ok=[]; fail=[]
        for x in items:
            code=str(x["ts_code"])
            if tmap.get(code,0)>=b+1:ok.append(code)
            else:fail.append(code)
        rates[f"{b}to{b+1}"]=len(ok)/eligible if eligible else None
        promoted[f"{b}to{b+1}"]=ok
        broken[f"{b}to{b+1}"]=fail
    return {"rates":rates,"promoted":promoted,"broken":broken}

def sector_ladders(rows):
    sectors=defaultdict(list)
    for x in rows:
        sec=x.get("sector") or x.get("reason") or "未分类"
        sectors[sec].append(x)
    out=[]
    for sec,items in sectors.items():
        boards=sorted([int(x.get("board",1)) for x in items],reverse=True)
        out.append({
            "sector":sec,
            "count":len(items),
            "max_board":max(boards) if boards else 0,
            "boards":boards,
            "codes":[x.get("ts_code") for x in items],
            "ladder_depth":len(set(boards)),
            "has_2plus":sum(1 for b in boards if b>=2),
        })
    return sorted(out,key=lambda x:(x["max_board"],x["count"],x["ladder_depth"]),reverse=True)

def ecology_score(today, yesterday=None, broken_rate=None, limit_down=0):
    ld=ladder(today)
    max_board=max(ld) if ld else 0
    total=len(today)
    multi=sum(len(v) for b,v in ld.items() if b>=2)
    score=45.0
    score+=min(max_board,7)*5
    score+=min(multi,15)*1.2
    if broken_rate is not None:
        score-=max(0,float(broken_rate)-20)*.7
    score-=min(float(limit_down),50)*.35

    promo=None
    if yesterday:
        promo=promotion_rates(today,yesterday)
        vals=[x for x in promo["rates"].values() if x is not None]
        if vals:
            avg=mean(vals)
            score+=(avg-.35)*35
    score=max(0,min(100,round(score,1)))

    if score>=78:phase="接力主升"
    elif score>=64:phase="梯队扩散"
    elif score>=50:phase="分歧博弈"
    elif score>=36:phase="高标退潮"
    else:phase="冰点/防守"
    return {"score":score,"phase":phase,"max_board":max_board,"total_limitups":total,
            "multi_board_count":multi,"promotion":promo}

def leader_risk(item, market=None):
    b=int(item.get("board",1))
    risk=10.0
    if b>=LIMITUP_ECOLOGY["leader_high_board_risk"]:risk+=20+(b-4)*8
    if b>=LIMITUP_ECOLOGY["leader_extreme_board_risk"]:risk+=18
    risk+=min(item.get("open_times",0),5)*8
    if item.get("turnover",0)>=25:risk+=12
    if item.get("seal_ratio",0) and item.get("seal_ratio",0)<20:risk+=12
    risk+=float(item.get("regulatory_risk",0))*0.35
    risk+=float(item.get("abnormal_move_risk",0))*0.30
    cum=float(item.get("cumulative_10d_pct",0) or 0)
    if cum>=80:risk+=22
    elif cum>=50:risk+=12
    if item.get("halt_warning"):risk+=25
    if market:
        if market.get("phase") in ("高标退潮","冰点/防守"):risk+=20
        if market.get("score",50)<45:risk+=10
    return round(max(0,min(100,risk)),1)

def identify_succession(today,yesterday):
    """
    识别“旧空间龙断板 + 新空间龙晋级”的接班。
    """
    if not yesterday:return {"happened":False}
    yld=ladder(yesterday); tld=ladder(today)
    if not yld or not tld:return {"happened":False}
    ytop=max(yld); ttop=max(tld)
    old_codes={x["ts_code"] for x in yld[ytop]}
    today_map={x["ts_code"]:x for x in today}
    old_survivors=[c for c in old_codes if c in today_map and int(today_map[c].get("board",1))>=ytop+1]
    new_leaders=tld.get(ttop,[])
    happened=(len(old_survivors)==0 and ttop>=3 and new_leaders)
    return {
        "happened":bool(happened),
        "old_height":ytop,
        "new_height":ttop,
        "old_leaders":list(old_codes),
        "new_leaders":[x["ts_code"] for x in new_leaders],
    }

def analyze(today,yesterday=None,broken_rate=None,limit_down=0):
    eco=ecology_score(today,yesterday,broken_rate,limit_down)
    eco["ladder"]=ladder(today)
    eco["sectors"]=sector_ladders(today)
    eco["succession"]=identify_succession(today,yesterday)
    return eco
