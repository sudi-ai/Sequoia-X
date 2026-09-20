# -*- coding: utf-8 -*-
"""
V8.3 错失机会自动复盘 / False Negative Review

核心指标不再只有“买了以后胜率”，同时统计：
- Big Mover Recall：后来大涨的股票，有多少曾被潜伏池提前覆盖；
- Lead Time：提前几天进入WATCH/READY/START；
- Miss Reason：被哪一道旧门槛漏掉；
- False Positive：进入潜伏池但后来没启动的比例。

这能防止系统只优化自己已经看到的股票。
"""
from __future__ import annotations
from datetime import datetime,timedelta
from config import MISSED_REVIEW
from latent_startup import analyze_kline
from latent_store import LatentStore

def _ret(closes,days):
    if len(closes)<=days or closes[-days-1]<=0:return None
    return round((closes[-1]/closes[-days-1]-1)*100,2)

def detect_big_mover(kline):
    closes=[float(x["close"]) for x in kline if x.get("close")]
    r5=_ret(closes,5);r10=_ret(closes,10)
    hit=(r5 is not None and r5>=MISSED_REVIEW["ret_5d_threshold"]) or (r10 is not None and r10>=MISSED_REVIEW["ret_10d_threshold"])
    return {"hit":hit,"ret_5d":r5,"ret_10d":r10}

def infer_miss_reason(pre):
    reasons=[]
    if pre.get("ma_cluster",0)<68:reasons.append("均线收敛不足")
    if pre.get("platform",0)<68:reasons.append("平台结构不足")
    if pre.get("breakout_proximity",0)<68:reasons.append("距离平台上沿较远")
    if pre.get("volume_dry",0)<55:reasons.append("量能结构不理想")
    if pre.get("first_expansion",0)<55:reasons.append("首次放量未出现")
    if pre.get("accumulation",50)<50:reasons.append("资金潜伏证据弱")
    if pre.get("sector_heat",50)<45:reasons.append("板块热度不足")
    if pre.get("chip_lock",50)<45:reasons.append("筹码锁定不足")
    return "；".join(reasons[:4]) or "结构接近，但总分未过阈值"

def review_case(ts_code,name,kline,extra_history=None,store=None,review_date=None):
    """
    kline 要按时间升序，最后一根是复盘日。
    使用大涨发生前的截面构建 pre_features，绝不使用未来K线。
    """
    store=store or LatentStore()
    bm=detect_big_mover(kline)
    if not bm["hit"]:
        return None

    # 取大涨窗口开始前一天作为“事前截面”
    pre_end=max(30,len(kline)-11)
    pre_kline=kline[:pre_end]
    if len(pre_kline)<30:return None

    extra={}
    if extra_history:
        extra=dict(extra_history(pre_kline[-1].get("date")) or {})
    pre=analyze_kline(pre_kline,extra)
    runup_start=str(kline[pre_end].get("date","")) if pre_end<len(kline) else ""

    # 查看系统历史上是否真的提前看到，不能事后用新模型冒充“当时发现”
    end_date=str(pre_kline[-1].get("date","")).replace("-","")
    start_dt=None
    try:
        d=datetime.strptime(end_date,"%Y%m%d")-timedelta(days=10)
        start_dt=d.strftime("%Y%m%d")
    except Exception:
        pass
    old=store.best_before(ts_code,end_date,start_dt)

    result={
        "review_date":review_date or datetime.now().strftime("%Y%m%d"),
        "ts_code":ts_code,"name":name,"runup_start":runup_start,
        "ret_5d":bm["ret_5d"],"ret_10d":bm["ret_10d"],
        "had_latent":bool(old),
        "best_latent_score":old["score"] if old else None,
        "best_latent_stage":old["stage"] if old else "",
        "miss_reason":"已提前覆盖" if old else infer_miss_reason(pre),
        "pre_features":pre,
    }
    store.save_missed(result)
    return result

def summary(cases):
    cases=[x for x in cases if x]
    if not cases:return {"n":0,"recall":None,"avg_5d":None,"avg_10d":None}
    covered=sum(1 for x in cases if x.get("had_latent"))
    r5=[x["ret_5d"] for x in cases if x.get("ret_5d") is not None]
    r10=[x["ret_10d"] for x in cases if x.get("ret_10d") is not None]
    return {
        "n":len(cases),
        "recall":round(covered/len(cases),4),
        "avg_5d":round(sum(r5)/len(r5),2) if r5 else None,
        "avg_10d":round(sum(r10)/len(r10),2) if r10 else None,
    }
