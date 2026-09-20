# -*- coding: utf-8 -*-
"""
V8.5 因子实验室
目标：删除“看起来有道理但没有统计增益”的重复因子。

输入DataFrame至少需要：
- date: 交易日
- forward_return: 未来T+1/T+3收益
- 若干因子列

输出：
- Pearson IC / Rank IC
- IC稳定性 / ICIR
- 分位数组收益与单调性
- 因子方向
- 高相关因子去重建议
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from config import FACTOR_LAB

def _safe_corr(a,b,method="pearson"):
    x=pd.DataFrame({"a":a,"b":b}).replace([np.inf,-np.inf],np.nan).dropna()
    if len(x)<3 or x["a"].nunique()<2 or x["b"].nunique()<2:
        return np.nan
    return x["a"].corr(x["b"],method=method)

def daily_ic(df,factor,target="forward_return",date_col="date",method="pearson"):
    vals=[]
    for _,g in df[[date_col,factor,target]].groupby(date_col,sort=True):
        if len(g)<FACTOR_LAB["min_cross_section"]:
            continue
        c=_safe_corr(g[factor],g[target],method)
        if pd.notna(c):
            vals.append(float(c))
    return vals

def quantile_forward_returns(df,factor,target="forward_return",date_col="date",q=None):
    q=int(q or FACTOR_LAB["quantiles"])
    rows=[]
    for d,g in df[[date_col,factor,target]].replace([np.inf,-np.inf],np.nan).dropna().groupby(date_col):
        if len(g)<max(q*4,FACTOR_LAB["min_cross_section"]) or g[factor].nunique()<q:
            continue
        try:
            bucket=pd.qcut(g[factor].rank(method="first"),q,labels=False,duplicates="drop")
        except Exception:
            continue
        z=g.assign(_q=bucket).groupby("_q")[target].mean()
        for k,v in z.items():
            rows.append({"date":d,"quantile":int(k)+1,"return":float(v)})
    if not rows:
        return {"quantile_mean":{},"spread":None,"monotonicity":None}
    x=pd.DataFrame(rows)
    means=x.groupby("quantile")["return"].mean().to_dict()
    ordered=[means[k] for k in sorted(means)]
    spread=(ordered[-1]-ordered[0]) if len(ordered)>=2 else None
    mono=None
    if len(ordered)>=3:
        mono=float(pd.Series(range(len(ordered))).corr(pd.Series(ordered),method="spearman"))
    return {
        "quantile_mean":{int(k):round(float(v),6) for k,v in means.items()},
        "spread":None if spread is None else round(float(spread),6),
        "monotonicity":None if mono is None or math.isnan(mono) else round(mono,4),
    }

def factor_report(df,factor,target="forward_return",date_col="date"):
    pearson=daily_ic(df,factor,target,date_col,"pearson")
    rank=daily_ic(df,factor,target,date_col,"spearman")
    q=quantile_forward_returns(df,factor,target,date_col)
    def stats(vals):
        if not vals:return (None,None,None,None)
        a=np.asarray(vals,dtype=float)
        mean=float(np.mean(a));std=float(np.std(a,ddof=1)) if len(a)>1 else 0.0
        pos=float(np.mean(a>0))
        icir=(mean/std*math.sqrt(252)) if std>0 else None
        return mean,std,pos,icir
    pmean,pstd,ppos,pir=stats(pearson)
    rmean,rstd,rpos,rir=stats(rank)
    direction=1 if (rmean or 0)>=0 else -1
    return {
        "factor":factor,
        "dates":len(rank),
        "ic_mean":None if pmean is None else round(pmean,5),
        "ic_std":None if pstd is None else round(pstd,5),
        "ic_positive_rate":None if ppos is None else round(ppos,4),
        "icir_annualized":None if pir is None else round(pir,3),
        "rank_ic_mean":None if rmean is None else round(rmean,5),
        "rank_ic_std":None if rstd is None else round(rstd,5),
        "rank_ic_positive_rate":None if rpos is None else round(rpos,4),
        "rank_icir_annualized":None if rir is None else round(rir,3),
        "direction":direction,
        **q,
    }

def correlation_prune(df,factors,reports=None,threshold=None):
    """
    贪心保留：
    优先保留 |Rank IC| 更高的因子；与已保留因子绝对相关>=阈值时剔除。
    """
    threshold=float(threshold or FACTOR_LAB["corr_threshold"])
    reports=reports or [factor_report(df,f) for f in factors]
    quality={r["factor"]:abs(r.get("rank_ic_mean") or 0) for r in reports}
    ordered=sorted(factors,key=lambda f:quality.get(f,0),reverse=True)
    corr=df[ordered].replace([np.inf,-np.inf],np.nan).corr(method="spearman")
    keep=[];dropped=[]
    for f in ordered:
        conflicts=[]
        for k in keep:
            c=corr.loc[f,k] if f in corr.index and k in corr.columns else np.nan
            if pd.notna(c) and abs(float(c))>=threshold:
                conflicts.append((k,float(c)))
        if conflicts:
            dropped.append({"factor":f,"reason":"high_correlation","with":conflicts})
        else:
            keep.append(f)
    return {"keep":keep,"dropped":dropped,"corr_threshold":threshold}

def lab_report(df,factors,target="forward_return",date_col="date"):
    reps=[factor_report(df,f,target,date_col) for f in factors]
    prune=correlation_prune(df,factors,reps)
    return {"factors":reps,"prune":prune}
