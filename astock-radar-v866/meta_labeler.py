# -*- coding: utf-8 -*-
"""
Meta-Labeling：V8规则负责“发现买点”，Meta模型负责“这次买点值不值得执行”。

默认：
- LightGBM分类器优先；
- 无LightGBM时回退 sklearn HistGradientBoostingClassifier；
- 只预测Primary Signal成功概率；
- 未经治理批准不能覆盖原V8。
"""
from __future__ import annotations
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
from config import META_LABEL, DATA_DIR
from sample_domains import require_trade_signal_training_frame

MODEL_DIR=DATA_DIR/"models"
MODEL_DIR.mkdir(exist_ok=True)

class MetaLabeler:
    def __init__(self,random_state=None):
        self.random_state=int(random_state or META_LABEL["random_state"])
        self.model=None
        self.backend=""
        self.features=[]
        self.metadata={}

    def _build(self):
        try:
            from lightgbm import LGBMClassifier
            self.backend="lightgbm"
            return LGBMClassifier(
                n_estimators=260,learning_rate=.035,num_leaves=31,
                subsample=.85,colsample_bytree=.85,reg_alpha=.25,reg_lambda=.45,
                random_state=self.random_state,verbosity=-1
            )
        except Exception:
            from sklearn.ensemble import HistGradientBoostingClassifier
            self.backend="sklearn_histgb"
            return HistGradientBoostingClassifier(
                learning_rate=.05,max_iter=220,max_leaf_nodes=31,l2_regularization=.4,
                random_state=self.random_state
            )

    def fit(self,df,features,label_col="meta_label",date_col="date",sample_weight_col=None):
        require_trade_signal_training_frame(df, "元标签训练")
        cols=[date_col,label_col]+list(features)
        if sample_weight_col:cols.append(sample_weight_col)
        x=df[cols].replace([np.inf,-np.inf],np.nan).dropna()
        if len(x)<META_LABEL["min_train_rows"]:
            raise ValueError(f"训练样本不足:{len(x)} < {META_LABEL['min_train_rows']}")
        if x[date_col].nunique()<META_LABEL["min_train_dates"]:
            raise ValueError(f"训练交易日不足:{x[date_col].nunique()} < {META_LABEL['min_train_dates']}")
        if x[label_col].nunique()<2:
            raise ValueError("标签只有一个类别，无法训练")
        self.features=list(features)
        self.model=self._build()
        sw=x[sample_weight_col].values if sample_weight_col else None
        try:
            self.model.fit(x[self.features],x[label_col].astype(int),sample_weight=sw)
        except TypeError:
            self.model.fit(x[self.features],x[label_col].astype(int))
        self.metadata={"backend":self.backend,"features":self.features,"rows":len(x),
                       "dates":int(x[date_col].nunique()),"positive_rate":float(x[label_col].mean())}
        return self

    def predict_proba(self,df):
        if self.model is None:raise RuntimeError("Meta模型尚未训练")
        p=self.model.predict_proba(df[self.features])[:,1]
        out=df.copy()
        out["meta_probability"]=np.asarray(p,dtype=float)
        return out

    def save(self,name="meta_challenger"):
        if self.model is None:raise RuntimeError("没有可保存模型")
        p=MODEL_DIR/f"{name}.pkl"
        with open(p,"wb") as f:
            pickle.dump({"model":self.model,"backend":self.backend,
                         "features":self.features,"metadata":self.metadata},f)
        return str(p)

    def load(self,path):
        with open(path,"rb") as f:x=pickle.load(f)
        self.model=x["model"];self.backend=x["backend"];self.features=x["features"];self.metadata=x.get("metadata",{})
        return self
