# -*- coding: utf-8 -*-
"""
概率校准：检查“预测80%”是否真的约有80%兑现。
避免评分看起来很高、实际概率失真。
"""
from __future__ import annotations

def reliability_bins(predictions, outcomes, bins=10):
    pairs=[(float(p),1 if float(y)>0 else 0) for p,y in zip(predictions,outcomes) if p is not None and y is not None]
    out=[]
    for i in range(bins):
        lo=i/bins; hi=(i+1)/bins
        rows=[x for x in pairs if lo<=x[0]<(hi if i<bins-1 else hi+1e-9)]
        if not rows:continue
        out.append({
            "from":lo,"to":hi,"n":len(rows),
            "pred_mean":round(sum(x[0] for x in rows)/len(rows),4),
            "actual":round(sum(x[1] for x in rows)/len(rows),4)
        })
    return out

def brier_score(predictions,outcomes):
    rows=[(float(p),1 if float(y)>0 else 0) for p,y in zip(predictions,outcomes) if p is not None and y is not None]
    if not rows:return None
    return round(sum((p-y)**2 for p,y in rows)/len(rows),6)
