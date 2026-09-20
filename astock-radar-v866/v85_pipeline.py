# -*- coding: utf-8 -*-
"""
V8.5统一管线：
V8.4专家系统保持Champion
+
统计学习Challenger
+
样本外/模拟盘/治理后才允许升级。
"""
from __future__ import annotations
from v84_pipeline import V84Pipeline
from stat_learning_pipeline import StatisticalLearningPipeline
from model_governance import compare,approve_only_after_paper

class V85Pipeline:
    def __init__(self):
        self.trading=V84Pipeline()
        self.stats=StatisticalLearningPipeline()

    def factor_audit(self,*args,**kwargs):
        return self.stats.factor_audit(*args,**kwargs)

    def train_ml_challenger(self,*args,**kwargs):
        return self.stats.train_challenger(*args,**kwargs)

    def rule_ml_resonance(self,rule_pool,alpha_rank_pct,model_approved=False):
        return self.stats.combine_rule_ml(rule_pool,alpha_rank_pct,model_approved)

    def validate_challenger(self,champion_metrics,challenger_metrics,paper_days=0):
        r=compare(champion_metrics,challenger_metrics)
        r["paper_days"]=paper_days
        r["approval"]=approve_only_after_paper(r)
        return r
