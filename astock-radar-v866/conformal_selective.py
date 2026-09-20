# -*- coding: utf-8 -*-
"""
Split Conformal Classification + Selective Abstention

对二分类成功概率p：
- 校准集非一致性分数 = 1 - P(true class)
- 计算q_hat；
- 新样本预测集合可能是 {0}, {1}, {0,1}, 空集。
只有singleton {1} 且Meta概率达到门槛时才允许进入高精度执行层。
"""
from __future__ import annotations
import math
import numpy as np
from config import SELECTIVE
from sample_domains import SIGNAL_SAMPLE_DOMAIN, require_domain_name

class SplitConformalBinary:
    def __init__(self,alpha=.10):
        self.alpha=float(alpha)
        self.q_hat=None
        self.n=0

    def fit(self,prob_pos,y_true,sample_domain=None):
        require_domain_name(sample_domain, SIGNAL_SAMPLE_DOMAIN, "置信集合校准")
        p=np.asarray(prob_pos,dtype=float)
        y=np.asarray(y_true,dtype=int)
        if len(p)<30:
            raise ValueError("Conformal校准样本不足30")
        true_prob=np.where(y==1,p,1-p)
        scores=1-true_prob
        n=len(scores)
        # conformal有限样本修正
        q_level=min(1.0,math.ceil((n+1)*(1-self.alpha))/n)
        self.q_hat=float(np.quantile(scores,q_level,method="higher"))
        self.n=n
        return self

    def prediction_set(self,p):
        if self.q_hat is None:raise RuntimeError("Conformal未拟合")
        p=float(p)
        classes=[]
        if p <= self.q_hat: classes.append(0)        # 1-P(class0)=p
        if 1-p <= self.q_hat: classes.append(1)      # 1-P(class1)=1-p
        return classes

    def evaluate(self,p):
        s=self.prediction_set(p)
        return {"prediction_set":s,"singleton_positive":s==[1],
                "q_hat":round(self.q_hat,6),"calibration_n":self.n}

def selective_decision(meta_probability,conformal_result,alpha_rank_pct=None,
                       execution_ok=True,risk_veto=False):
    p=float(meta_probability)
    reasons=[]
    if p<SELECTIVE["min_prob"]:reasons.append("Meta概率不足")
    if not conformal_result.get("singleton_positive"):reasons.append("Conformal无法唯一确认正类")
    if not execution_ok:reasons.append("执行不可行")
    if risk_veto:reasons.append("风险否决")

    if reasons:
        return {"decision":"ABSTAIN","grade":"NO_TRADE","reasons":reasons}

    rank=float(alpha_rank_pct) if alpha_rank_pct is not None else None
    if p>=SELECTIVE["a_plus_prob"] and (rank is None or rank>=.98):
        return {"decision":"EXECUTE","grade":"A+","reasons":[]}
    return {"decision":"EXECUTE","grade":"A","reasons":[]}
