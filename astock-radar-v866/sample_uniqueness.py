# -*- coding: utf-8 -*-
"""
重叠事件的 Sample Uniqueness 权重。
同一时间窗口里大量重叠信号，不应被当成完全独立样本。
"""
from __future__ import annotations
from collections import defaultdict
from config import SAMPLE_UNIQUENESS

def uniqueness_weights(events):
    """
    events: [{"id", "start", "end"}, ...]
    start/end要求可比较（整数bar索引或时间戳字符串）。
    对离散整数bar实现精确并发权重；时间字符串按排序位置映射。
    """
    if not events:return {}
    # 若start/end是整数，直接使用bar区间
    if all(isinstance(e.get("start"),int) and isinstance(e.get("end"),int) for e in events):
        concurrency=defaultdict(int)
        for e in events:
            for t in range(e["start"],e["end"]+1):
                concurrency[t]+=1
        weights={}
        for i,e in enumerate(events):
            vals=[1/concurrency[t] for t in range(e["start"],e["end"]+1) if concurrency[t]>0]
            w=sum(vals)/len(vals) if vals else 1.0
            weights[e.get("id",i)]=max(float(SAMPLE_UNIQUENESS["min_weight"]),round(w,6))
        return weights

    # 通用可比较时间：构建所有端点的排序网格
    points=sorted(set([e["start"] for e in events]+[e["end"] for e in events]))
    pidx={p:i for i,p in enumerate(points)}
    mapped=[]
    for i,e in enumerate(events):
        mapped.append({"id":e.get("id",i),"start":pidx[e["start"]],"end":pidx[e["end"]]})
    return uniqueness_weights(mapped)

def attach_weights(rows,start_col="event_start",end_col="event_end",id_col="event_id"):
    events=[]
    for i,r in enumerate(rows):
        events.append({"id":r.get(id_col,i),"start":r[start_col],"end":r[end_col]})
    w=uniqueness_weights(events)
    return [{**r,"sample_weight":w.get(r.get(id_col,i),1.0)} for i,r in enumerate(rows)]
