# -*- coding: utf-8 -*-
"""
Champion / Challenger 升级门槛。
任何ML模型都不能因为一次回测好看就替换正式规则模型。
"""
from __future__ import annotations

def compare(champion,challenger):
    """
    metrics推荐:
    n / win_rate / expectancy / max_drawdown / rank_ic / brier
    """
    reasons=[]
    if challenger.get("n",0)<max(50,int(champion.get("n",0)*.5)):
        reasons.append("Challenger样本不足")
    if challenger.get("expectancy",-999)<=0:
        reasons.append("Challenger期望收益非正")
    if champion.get("expectancy") is not None and challenger.get("expectancy",-999)<=champion.get("expectancy",-999):
        reasons.append("期望收益未超过Champion")
    if champion.get("max_drawdown") is not None and challenger.get("max_drawdown",-999)<champion.get("max_drawdown",-999)-3:
        reasons.append("最大回撤明显恶化")
    if champion.get("win_rate") is not None and challenger.get("win_rate",0)<champion.get("win_rate",0)-.03:
        reasons.append("胜率显著下降")
    if challenger.get("rank_ic") is not None and challenger.get("rank_ic",0)<=0:
        reasons.append("Rank IC无效")
    return {"promotable":not reasons,"reasons":reasons}

def approve_only_after_paper(result,paper_days=10):
    if not result.get("promotable"):
        return {"approved":False,"reason":"样本外指标未通过"}
    if int(result.get("paper_days",0))<paper_days:
        return {"approved":False,"reason":f"模拟盘不足{paper_days}个交易日"}
    return {"approved":True,"reason":"样本外+模拟盘均通过，可人工审批升级"}
