# -*- coding: utf-8 -*-
"""
Bootstrap统计置信度。
胜率80%只有在样本量和置信区间都站得住时才有意义。
"""
from __future__ import annotations
import numpy as np
from config import BOOTSTRAP
from sample_domains import SIGNAL_SAMPLE_DOMAIN, require_domain_name

def _ci(values,confidence):
    if not values:return (None,None)
    a=np.asarray(values,dtype=float)
    alpha=(1-confidence)/2
    return (float(np.quantile(a,alpha)),float(np.quantile(a,1-alpha)))

def bootstrap_trade_metrics(returns,iterations=None,confidence=None,seed=None,sample_domain=None):
    require_domain_name(sample_domain, SIGNAL_SAMPLE_DOMAIN, "交易胜率自助法统计")
    x=np.asarray([float(r) for r in returns if r is not None],dtype=float)
    if len(x)==0:return {"n":0}
    it=int(iterations or BOOTSTRAP["iterations"])
    conf=float(confidence or BOOTSTRAP["confidence"])
    rng=np.random.default_rng(BOOTSTRAP["seed"] if seed is None else seed)
    wr=[];avg=[];expectancy=[]
    for _ in range(it):
        s=rng.choice(x,size=len(x),replace=True)
        wins=s[s>0];losses=s[s<=0]
        p=float(np.mean(s>0))
        aw=float(np.mean(wins)) if len(wins) else 0.0
        al=abs(float(np.mean(losses))) if len(losses) else 0.0
        wr.append(p);avg.append(float(np.mean(s)));expectancy.append(p*aw-(1-p)*al)
    wrci=_ci(wr,conf);avgci=_ci(avg,conf);exci=_ci(expectancy,conf)
    return {
        "n":len(x),
        "win_rate":round(float(np.mean(x>0)),4),
        "win_rate_ci":[round(wrci[0],4),round(wrci[1],4)],
        "avg_return":round(float(np.mean(x)),5),
        "avg_return_ci":[round(avgci[0],5),round(avgci[1],5)],
        "expectancy_ci":[round(exci[0],5),round(exci[1],5)],
        "confidence":conf,
        "iterations":it,
    }
