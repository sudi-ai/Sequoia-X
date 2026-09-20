# -*- coding: utf-8 -*-
"""仅用于工作台演示和离线测试，不进入真实交易。"""
from __future__ import annotations
import numpy as np
import pandas as pd

def research_frame(seed=42,dates=60,stocks=70):
    rng=np.random.default_rng(seed)
    rows=[]
    for d in range(dates):
        market=rng.normal(0,.6)
        for i in range(stocks):
            auction=rng.normal(50,15)
            flow=rng.normal(50,18)
            chip=rng.normal(50,14)
            trend=rng.normal(50,16)
            duplicate=trend*.92+rng.normal(0,4)
            noise=rng.normal(50,18)
            # 构造可解释的未来收益：趋势/资金/竞价/筹码有效，noise无效
            ret=(trend-50)*.025+(flow-50)*.018+(auction-50)*.012+(chip-50)*.010+market+rng.normal(0,1.2)
            rows.append({
                "date":f"2026{1+d//20:02d}{1+d%20:02d}",
                "ts_code":f"{i:06d}.SZ",
                "auction_quality":auction,
                "main_flow_score":flow,
                "chip_lock_score":chip,
                "trend_score":trend,
                "trend_duplicate":duplicate,
                "noise_factor":noise,
                "forward_return":ret,
            })
    return pd.DataFrame(rows)

def demo_summary():
    return {
      "factor_health":[
        {"name":"趋势评分","rank_ic":0.19,"status":"保留"},
        {"name":"主力资金","rank_ic":0.13,"status":"保留"},
        {"name":"竞价质量","rank_ic":0.09,"status":"保留"},
        {"name":"筹码锁定","rank_ic":0.07,"status":"观察"},
        {"name":"重复趋势因子","rank_ic":0.17,"status":"高相关剔除"},
        {"name":"噪声因子","rank_ic":0.00,"status":"剔除"},
      ],
      "ml":{"role":"Challenger","backend":"LightGBM优先","status":"未批准影响实盘","top_rank":"Top 2%"},
      "confidence":{"win_rate":"78.4%","ci":"72.1% ~ 84.0%","n":126},
      "monte_carlo":{"profit":"82%","target":"31%","dd20":"9%"},
      "drift":{"level":"OK","psi":"0.07","recent_win":"76%"},
      "execution":{"t1":"已模拟","limit":"涨跌停不可成交","fees":"手续费+印花税","slippage":"已模拟"},
    }
