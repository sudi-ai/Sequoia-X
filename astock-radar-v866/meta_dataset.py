# -*- coding: utf-8 -*-
"""
Primary Signal -> Triple Barrier Meta标签数据集。
不对全A所有股票造标签，只对“原V8实际产生的Primary Signal”学习：
这次信号值不值得执行？
"""
from __future__ import annotations
import pandas as pd
from triple_barrier import label_path
from sample_uniqueness import uniqueness_weights

def build_meta_rows(primary_signals,bars_by_code,feature_cols):
    """
    primary_signals:
      event_id / date / ts_code / event_index / entry_price / + feature_cols
    bars_by_code:
      code -> ordered bars
    """
    rows=[]
    events=[]
    for i,s in enumerate(primary_signals):
        code=s["ts_code"];idx=int(s["event_index"])
        bars=bars_by_code.get(code,[])
        future=bars[idx+1:]
        lab=label_path(s["entry_price"],future)
        if lab.get("label") is None:
            continue
        row={k:s.get(k) for k in feature_cols}
        row.update({
            "event_id":s.get("event_id",i),
            "date":s.get("date"),
            "ts_code":code,
            "meta_label":int(lab["label"]),
            "barrier":lab.get("barrier"),
            "realized_return":lab.get("return_pct"),
            "event_start":idx,
            "event_end":idx+int(lab.get("bars",1)),
        })
        rows.append(row)
        events.append({"id":row["event_id"],"start":row["event_start"],"end":row["event_end"]})

    weights=uniqueness_weights(events)
    for r in rows:
        r["sample_weight"]=weights.get(r["event_id"],1.0)
    return pd.DataFrame(rows)
