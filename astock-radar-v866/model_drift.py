# -*- coding: utf-8 -*-
"""
模型漂移监控：
- PSI：特征分布变化
- 预测均值/标准差变化
- 实际胜率变化
"""
from __future__ import annotations
import numpy as np
from config import DRIFT

def psi(reference,current,bins=10):
    r=np.asarray([float(x) for x in reference if x is not None],dtype=float)
    c=np.asarray([float(x) for x in current if x is not None],dtype=float)
    if len(r)<20 or len(c)<20:return None
    edges=np.unique(np.quantile(r,np.linspace(0,1,bins+1)))
    if len(edges)<3:return 0.0
    edges[0]-=1e-9;edges[-1]+=1e-9
    rh,_=np.histogram(r,bins=edges);ch,_=np.histogram(c,bins=edges)
    rp=np.maximum(rh/rh.sum(),1e-6);cp=np.maximum(ch/ch.sum(),1e-6)
    val=float(np.sum((cp-rp)*np.log(cp/rp)))
    return round(val,5)

def drift_level(psi_value):
    if psi_value is None:return "UNKNOWN"
    if psi_value>=DRIFT["psi_alert"]:return "ALERT"
    if psi_value>=DRIFT["psi_warn"]:return "WARN"
    return "OK"

def performance_drift(baseline_returns,recent_returns):
    b=np.asarray([float(x) for x in baseline_returns if x is not None],dtype=float)
    r=np.asarray([float(x) for x in recent_returns if x is not None],dtype=float)
    if len(b)<20 or len(r)<10:return {"level":"UNKNOWN"}
    bw=float(np.mean(b>0));rw=float(np.mean(r>0))
    bm=float(np.mean(b));rm=float(np.mean(r))
    delta_win=rw-bw;delta_ret=rm-bm
    level="OK"
    if delta_win<=-.15 or delta_ret<=-1.0:level="ALERT"
    elif delta_win<=-.08 or delta_ret<=-.5:level="WARN"
    return {"level":level,"baseline_win_rate":round(bw,4),"recent_win_rate":round(rw,4),
            "win_rate_delta":round(delta_win,4),"mean_return_delta":round(delta_ret,4)}
