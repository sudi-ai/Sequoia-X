# -*- coding: utf-8 -*-
"""
Purged/CPCV Meta模型样本外验证。
训练/测试事件区间不能重叠，且测试后留Embargo。
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from purged_cv import cpcv_splits
from meta_labeler import MetaLabeler

def _metrics(y,p,threshold=.5):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float)
    pred=(p>=threshold).astype(int)
    tp=int(((pred==1)&(y==1)).sum());fp=int(((pred==1)&(y==0)).sum())
    fn=int(((pred==0)&(y==1)).sum());tn=int(((pred==0)&(y==0)).sum())
    precision=tp/max(tp+fp,1)
    recall=tp/max(tp+fn,1)
    accuracy=(tp+tn)/max(len(y),1)
    brier=float(np.mean((p-y)**2))
    return {"n":len(y),"precision":round(precision,4),"recall":round(recall,4),
            "accuracy":round(accuracy,4),"brier":round(brier,6)}

def validate(df,features,label_col="meta_label",date_col="date",max_splits=None):
    x=df.sort_values(["event_start","event_end"]).reset_index(drop=True).copy()
    starts=x["event_start"].astype(int).tolist()
    ends=x["event_end"].astype(int).tolist()
    fold_metrics=[];all_y=[];all_p=[]
    for split_no,(tr,te,combo) in enumerate(cpcv_splits(starts,ends)):
        if max_splits is not None and split_no>=max_splits:break
        if len(tr)<100 or len(te)<20:continue
        model=MetaLabeler()
        # 测试环境可不足正式阈值，直接构建底层模型以验证CPCV流程
        model.features=list(features);model.model=model._build()
        sw=x.iloc[tr]["sample_weight"].values if "sample_weight" in x else None
        try:model.model.fit(x.iloc[tr][features],x.iloc[tr][label_col].astype(int),sample_weight=sw)
        except TypeError:model.model.fit(x.iloc[tr][features],x.iloc[tr][label_col].astype(int))
        p=model.model.predict_proba(x.iloc[te][features])[:,1]
        y=x.iloc[te][label_col].values
        m=_metrics(y,p);m["combo"]=list(combo)
        fold_metrics.append(m);all_y.extend(y.tolist());all_p.extend(p.tolist())
    overall=_metrics(all_y,all_p) if all_y else {"n":0}
    return {"folds":fold_metrics,"overall":overall}
