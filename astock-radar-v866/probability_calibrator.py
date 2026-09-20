# -*- coding: utf-8 -*-
"""
Meta概率校准：优先Isotonic；样本较少可用Logistic/Platt。
校准集必须与训练集时间隔离。
"""
from __future__ import annotations
import numpy as np

class ProbabilityCalibrator:
    def __init__(self,method="isotonic"):
        self.method=method
        self.model=None

    def fit(self,raw_prob,y):
        p=np.asarray(raw_prob,dtype=float)
        y=np.asarray(y,dtype=int)
        if len(p)<30 or len(np.unique(y))<2:
            raise ValueError("校准样本不足或标签单一")
        if self.method=="isotonic":
            from sklearn.isotonic import IsotonicRegression
            self.model=IsotonicRegression(out_of_bounds="clip")
            self.model.fit(p,y)
        else:
            from sklearn.linear_model import LogisticRegression
            eps=1e-6
            logit=np.log(np.clip(p,eps,1-eps)/(1-np.clip(p,eps,1-eps))).reshape(-1,1)
            self.model=LogisticRegression()
            self.model.fit(logit,y)
        return self

    def predict(self,raw_prob):
        if self.model is None:raise RuntimeError("校准器未训练")
        p=np.asarray(raw_prob,dtype=float)
        if self.method=="isotonic":
            return np.asarray(self.model.predict(p),dtype=float)
        eps=1e-6
        logit=np.log(np.clip(p,eps,1-eps)/(1-np.clip(p,eps,1-eps))).reshape(-1,1)
        return self.model.predict_proba(logit)[:,1]
