# -*- coding: utf-8 -*-
"""
V8.5 ML横截面 Alpha Ranker
- 默认优先 LightGBM 回归未来收益，再按当日横截面转成百分位Rank。
- LightGBM不可用时回退 sklearn HistGradientBoostingRegressor。
- 模型默认只作为 Challenger，不直接覆盖原V8规则评分。
"""
from __future__ import annotations
import json, pickle
from pathlib import Path
import numpy as np
import pandas as pd
from config import ML_RANKER, DATA_DIR
from sample_domains import require_discovery_training_frame

MODEL_DIR=DATA_DIR/"models"
MODEL_DIR.mkdir(exist_ok=True)

class AlphaRanker:
    def __init__(self,random_state=None):
        self.random_state=int(random_state or ML_RANKER["random_state"])
        self.model=None
        self.backend=""
        self.features=[]
        self.metadata={}

    def _build(self):
        try:
            from lightgbm import LGBMRegressor
            self.backend="lightgbm"
            return LGBMRegressor(
                n_estimators=260,
                learning_rate=.035,
                num_leaves=31,
                subsample=.85,
                colsample_bytree=.85,
                reg_alpha=.2,
                reg_lambda=.4,
                random_state=self.random_state,
                verbosity=-1,
            )
        except Exception:
            from sklearn.ensemble import HistGradientBoostingRegressor
            self.backend="sklearn_histgb"
            return HistGradientBoostingRegressor(
                learning_rate=.05,max_iter=220,max_leaf_nodes=31,
                l2_regularization=.4,random_state=self.random_state
            )

    def fit(self,df,features,target="forward_return",date_col="date"):
        require_discovery_training_frame(df, "全市场强度排序训练")
        x=df[[date_col,target]+list(features)].replace([np.inf,-np.inf],np.nan).dropna()
        if len(x)<ML_RANKER["min_train_rows"]:
            raise ValueError(f"训练样本不足: {len(x)} < {ML_RANKER['min_train_rows']}")
        if x[date_col].nunique()<ML_RANKER["min_train_dates"]:
            raise ValueError(f"训练交易日不足: {x[date_col].nunique()} < {ML_RANKER['min_train_dates']}")
        self.features=list(features)
        self.model=self._build()
        self.model.fit(x[self.features],x[target])
        self.metadata={
            "backend":self.backend,"features":self.features,"rows":len(x),
            "dates":int(x[date_col].nunique()),"target":target
        }
        return self

    def predict(self,df,date_col="date"):
        if self.model is None:raise RuntimeError("模型尚未训练/加载")
        out=df.copy()
        raw=np.asarray(self.model.predict(out[self.features]),dtype=float)
        out["alpha_raw"]=raw
        if date_col in out.columns:
            out["alpha_rank_pct"]=out.groupby(date_col)["alpha_raw"].rank(pct=True,method="average")
        else:
            out["alpha_rank_pct"]=pd.Series(raw).rank(pct=True,method="average").values
        return out

    def feature_importance(self):
        if self.model is None:return []
        vals=getattr(self.model,"feature_importances_",None)
        if vals is None:return []
        pairs=sorted(zip(self.features,[float(x) for x in vals]),key=lambda z:z[1],reverse=True)
        total=sum(v for _,v in pairs) or 1.0
        return [{"feature":k,"importance":round(v/total,5)} for k,v in pairs]

    def save(self,name="challenger_alpha"):
        if self.model is None:raise RuntimeError("没有可保存模型")
        p=MODEL_DIR/f"{name}.pkl"
        with open(p,"wb") as f:pickle.dump({"model":self.model,"backend":self.backend,"features":self.features,"metadata":self.metadata},f)
        return str(p)

    def load(self,path):
        with open(path,"rb") as f:x=pickle.load(f)
        self.model=x["model"];self.backend=x["backend"];self.features=x["features"];self.metadata=x.get("metadata",{})
        return self

def resonance(rule_pool,alpha_rank_pct,model_approved=False):
    """
    ML未被批准为Champion前：
    - 只做标签/排序，不否决原V8正式信号。
    ML批准后：
    - A级规则 + ML Top8% => A+
    - A级规则但ML很弱 => A保留但标记分歧，不能自动降级。
    """
    rank=float(alpha_rank_pct) if alpha_rank_pct is not None else None
    if rank is None:return {"grade":rule_pool,"ml_state":"NO_MODEL"}
    if rule_pool=="A" and rank>=1-ML_RANKER["top_rank_pct"]:
        return {"grade":"A+","ml_state":"STRONG_RESONANCE"}
    if rule_pool=="A" and rank>=1-ML_RANKER["resonance_rank_pct"]:
        return {"grade":"A","ml_state":"RESONANCE"}
    if rule_pool=="A":
        return {"grade":"A","ml_state":"DIVERGENCE" if model_approved else "CHALLENGER_DIVERGENCE"}
    return {"grade":rule_pool,"ml_state":"REFERENCE_ONLY"}
