# -*- coding: utf-8 -*-
"""
交易收益Monte Carlo。
比单看胜率更接近“资金曲线最终会怎样”。
"""
from __future__ import annotations
import numpy as np
from config import MONTE_CARLO

def _max_drawdown(equity):
    peak=np.maximum.accumulate(equity)
    dd=equity/peak-1
    return float(dd.min()*100)

def simulate_trade_returns(returns,initial_equity=1.0,paths=None,trades_per_path=None,
                           target_return_pct=50.0,drawdown_threshold_pct=None,seed=42):
    x=np.asarray([float(r) for r in returns if r is not None],dtype=float)
    if len(x)==0:return {"n":0}
    paths=int(paths or MONTE_CARLO["paths"])
    n=int(trades_per_path or MONTE_CARLO["trades_per_path"])
    dd_thr=float(drawdown_threshold_pct or MONTE_CARLO["drawdown_threshold_pct"])
    rng=np.random.default_rng(seed)
    terminal=[];dds=[]
    for _ in range(paths):
        sample=rng.choice(x,size=n,replace=True)/100.0
        eq=initial_equity*np.cumprod(1+sample)
        terminal.append(float(eq[-1]/initial_equity-1)*100)
        dds.append(_max_drawdown(eq))
    t=np.asarray(terminal);d=np.asarray(dds)
    return {
        "n":len(x),"paths":paths,"trades_per_path":n,
        "profit_probability":round(float(np.mean(t>0)),4),
        "target_probability":round(float(np.mean(t>=target_return_pct)),4),
        "drawdown_breach_probability":round(float(np.mean(d<=-dd_thr)),4),
        "terminal_return_p10":round(float(np.quantile(t,.10)),2),
        "terminal_return_p50":round(float(np.quantile(t,.50)),2),
        "terminal_return_p90":round(float(np.quantile(t,.90)),2),
        "max_drawdown_p50":round(float(np.quantile(d,.50)),2),
        "max_drawdown_p10":round(float(np.quantile(d,.10)),2),
    }
