# -*- coding: utf-8 -*-
"""
Probabilistic / Deflated Sharpe 的轻量实现。
用于惩罚“试了很多策略后偶然挑到的最高Sharpe”。
"""
from __future__ import annotations
import math
import numpy as np
from scipy.stats import norm
from config import BACKTEST_OVERFIT

def sharpe_ratio(returns,periods=252):
    x=np.asarray([float(r) for r in returns if r is not None],dtype=float)
    if len(x)<2:return None
    sd=float(np.std(x,ddof=1))
    if sd<=0:return None
    return float(np.mean(x)/sd*math.sqrt(periods))

def probabilistic_sharpe_ratio(returns,benchmark_sr=0.0,periods=252):
    x=np.asarray([float(r) for r in returns if r is not None],dtype=float)
    if len(x)<3:return None
    sr=sharpe_ratio(x,periods)
    if sr is None:return None
    # 转为每期Sharpe用于有限样本近似
    sr_p=sr/math.sqrt(periods)
    bench_p=float(benchmark_sr)/math.sqrt(periods)
    skew=float(((x-x.mean())**3).mean()/(x.std()**3+1e-12))
    kurt=float(((x-x.mean())**4).mean()/(x.std()**4+1e-12))
    denom=math.sqrt(max(1e-12,(1-skew*sr_p+(kurt-1)/4*sr_p**2)/(len(x)-1)))
    z=(sr_p-bench_p)/denom
    return round(float(norm.cdf(z)),6)

def expected_max_sr(num_trials,mean_sr=0.0,std_sr=1.0):
    n=max(1,int(num_trials))
    # Bailey/Lopez de Prado常用近似：最大标准正态的期望
    gamma=0.5772156649
    z1=norm.ppf(1-1/n) if n>1 else 0
    z2=norm.ppf(1-1/(n*math.e)) if n>1 else 0
    return float(mean_sr+std_sr*((1-gamma)*z1+gamma*z2))

def deflated_sharpe_ratio(returns,num_trials,trial_sharpes=None,periods=252):
    x=np.asarray([float(r) for r in returns if r is not None],dtype=float)
    if len(x)<3:return None
    if trial_sharpes and len(trial_sharpes)>=2:
        mean_sr=float(np.mean(trial_sharpes));std_sr=float(np.std(trial_sharpes,ddof=1))
    else:
        mean_sr=0.0;std_sr=1.0
    bench=expected_max_sr(num_trials,mean_sr,std_sr)
    psr=probabilistic_sharpe_ratio(x,bench,periods)
    return {"deflated_sharpe_probability":psr,"benchmark_sharpe":round(bench,4),
            "num_trials":int(num_trials),
            "passed":bool(psr is not None and psr>=BACKTEST_OVERFIT["min_dsr"])}
