# -*- coding: utf-8 -*-
import math
from latent_startup import analyze_kline

def _series(seed_shift=0.0, boost=0.0):
    rows=[]
    for i in range(70):
        if i<45:
            p=6.5+i*.012+math.sin(i/4+seed_shift)*.025
            spread=.12;vol=150000-500*i
        else:
            p=7.42+(i-45)*.006+math.sin(i/3+seed_shift)*.02
            spread=.07;vol=max(52000,85000-(i-45)*950)
        if i==69:
            p=7.58+boost;vol=95000
        rows.append({"date":f"2026{6+(i//28):02d}{(i%28)+1:02d}",
                     "open":p-.02,"high":p+spread/2,"low":p-spread/2,"close":p,"volume":vol})
    return rows

def latent_demo():
    defs=[
      ("国芳型样本","601086.SH",0.0,0.0,{"chip_lock_score":75,"accumulation_score":68,"sector_heat":58}),
      ("平台蓄势A","000001.SZ",.8,.02,{"chip_lock_score":72,"accumulation_score":61,"sector_heat":63}),
      ("启动临界B","002001.SZ",1.3,.04,{"chip_lock_score":82,"accumulation_score":77,"sector_heat":72}),
    ]
    out=[]
    for name,code,shift,boost,extra in defs:
        x=analyze_kline(_series(shift,boost),{**extra,"event_risk":5,"unlock_risk":4,"distribution_risk":8,"data_quality":.98})
        x.update({"name":name,"ts_code":code})
        out.append(x)
    return out
