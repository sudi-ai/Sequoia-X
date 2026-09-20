# -*- coding: utf-8 -*-
"""
首板正负样本构造。
正样本：首板后未来窗口内至少晋级到2板（可调）。
负样本：首板后未晋级/快速断板。
用于比较“成妖前”与“断板前”的真实差异，不自动训练黑盒模型。
"""
from __future__ import annotations
from config import FIRST_BOARD_TRAINING

def build_sample(first_board_item,future_limitup_rows,pre_features=None):
    code=str(first_board_item.get("ts_code",""))
    future=[x for x in future_limitup_rows if str(x.get("ts_code",""))==code]
    max_board=max([int(x.get("board",1)) for x in future],default=1)
    label=1 if max_board>=FIRST_BOARD_TRAINING["future_board_success"] else 0
    return {
        "trade_date":first_board_item.get("trade_date",""),
        "ts_code":code,
        "name":first_board_item.get("name",""),
        "label":label,
        "max_future_board":max_board,
        "features":dict(pre_features or {}),
        "first_board":first_board_item,
    }

def compare_groups(samples):
    """
    对正/负样本做可解释均值差，不急着上机器学习。
    """
    pos=[s for s in samples if s.get("label")==1]
    neg=[s for s in samples if s.get("label")==0]
    keys=set()
    for s in samples:keys.update(k for k,v in s.get("features",{}).items() if isinstance(v,(int,float)))
    out=[]
    for k in sorted(keys):
        pv=[float(s["features"].get(k)) for s in pos if s["features"].get(k) is not None]
        nv=[float(s["features"].get(k)) for s in neg if s["features"].get(k) is not None]
        if not pv or not nv:continue
        pm=sum(pv)/len(pv);nm=sum(nv)/len(nv)
        out.append({"feature":k,"positive_mean":round(pm,3),"negative_mean":round(nm,3),"diff":round(pm-nm,3)})
    return sorted(out,key=lambda x:abs(x["diff"]),reverse=True)

def ready_for_model(stats):
    return stats.get("positive",0)>=FIRST_BOARD_TRAINING["min_positive_samples"] and stats.get("negative",0)>=FIRST_BOARD_TRAINING["min_negative_samples"]
