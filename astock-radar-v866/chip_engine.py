# -*- coding: utf-8 -*-
def chip_lock_score(f):
    s=50.0
    hc=f.get("holders_change_pct")
    if hc is not None: s+=max(-18,min(18,-hc*1.5))
    gap=abs(f.get("cost_gap_pct",0))
    if gap<=4:s+=10
    elif gap>=15:s-=10
    to=f.get("turnover_rate",0)
    if to>=20:s-=10
    elif 2<=to<=8:s+=4
    width=f.get("chip_90_width_pct")
    if width is not None:
        if width<=12:s+=12
        elif width>=30:s-=10
    return round(max(0,min(100,s)),1)
