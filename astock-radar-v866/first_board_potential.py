# -*- coding: utf-8 -*-
"""
首板 -> 连板潜力模型
目标：不是追4/5/6板，而是在首板/1进2阶段识别“有没有成为2板/3板的质量”。

输入全部要求是首板当天收盘前/收盘时已经可见的数据。
"""
from __future__ import annotations
from config import LIMITUP_ECOLOGY

def _clip(v):return round(max(0,min(100,float(v))),1)

def potential(features):
    """
    推荐字段：
    latent_score: 首板前潜伏分
    first_seal_score: 首封时间质量0-100（越早通常越强，但9:25秒板需防一字）
    reseal_quality: 炸板回封质量0-100
    seal_strength: 封单强度0-100
    turnover_quality: 换手结构0-100
    sector_ladder: 板块梯队0-100
    sector_heat: 板块热度0-100
    auction_quality: 次日竞价质量0-100（如果还未到次日，默认50）
    main_flow: 主力/主动资金0-100
    catalyst: 题材催化0-100
    crowding_risk: 拥挤/一致性风险0-100
    event_risk: 公告/监管/解禁风险0-100
    """
    f=dict(features)
    s=(
      f.get("latent_score",50)*.12+
      f.get("first_seal_score",50)*.11+
      f.get("reseal_quality",50)*.10+
      f.get("seal_strength",50)*.12+
      f.get("turnover_quality",50)*.08+
      f.get("sector_ladder",50)*.14+
      f.get("sector_heat",50)*.08+
      f.get("auction_quality",50)*.08+
      f.get("main_flow",50)*.10+
      f.get("catalyst",50)*.07
    )
    s-=f.get("crowding_risk",0)*.10
    s-=f.get("event_risk",0)*.12
    s=_clip(s)

    hard=[]
    if f.get("st_or_delist"):hard.append("ST/退市")
    if f.get("regulatory_risk",0)>=75:hard.append("监管风险")
    if f.get("unlock_risk",0)>=75:hard.append("解禁风险")
    if f.get("event_risk",0)>=80:hard.append("重大事件风险")
    if f.get("broken_rate_market",0)>=LIMITUP_ECOLOGY["max_broken_rate"]:hard.append("市场炸板率过高")

    if hard:grade="REJECT"
    elif s>=LIMITUP_ECOLOGY["strong_board_potential"]:grade="A"
    elif s>=LIMITUP_ECOLOGY["min_board_potential"]:grade="B"
    else:grade="C"

    return {**f,"board_potential":s,"board_grade":grade,"veto":"；".join(hard),
            "is_buy_signal":False,
            "action":{
                "A":"高连板潜力：进入次日竞价重点监控，仍不直接买",
                "B":"保留1进2观察，等竞价/回封质量确认",
                "C":"普通首板，后台记录",
                "REJECT":"风险否决"
            }[grade]}
