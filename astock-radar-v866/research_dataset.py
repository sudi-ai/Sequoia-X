# -*- coding: utf-8 -*-
"""
把V8特征快照和结果拼成训练数据。
只接受decision_time之后的future return，防止未来数据进入特征。
"""
from __future__ import annotations
import json, sqlite3
import pandas as pd
from config import DATA_DIR

def build_from_records(feature_records,outcome_records,key_cols=("ts_code","decision_time")):
    f=pd.DataFrame(feature_records)
    o=pd.DataFrame(outcome_records)
    if f.empty or o.empty:return pd.DataFrame()
    return f.merge(o,on=list(key_cols),how="inner")

def validate_training_frame(df,date_col="date",target="forward_return"):
    problems=[]
    if df.empty:problems.append("训练集为空")
    if date_col not in df:problems.append(f"缺少{date_col}")
    if target not in df:problems.append(f"缺少{target}")
    if "decision_time" in df and "outcome_time" in df:
        bad=(pd.to_datetime(df["outcome_time"])<=pd.to_datetime(df["decision_time"])).sum()
        if bad:problems.append(f"{bad}条结果时间不晚于决策时间")
    return problems
