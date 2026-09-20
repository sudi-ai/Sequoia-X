# -*- coding: utf-8 -*-
"""
V8.5 统计学习总管线
规则Champion永远优先；ML默认Challenger。
"""
from __future__ import annotations
from factor_lab import lab_report
from alpha_ranker import AlphaRanker,resonance
from bootstrap_confidence import bootstrap_trade_metrics
from monte_carlo import simulate_trade_returns
from model_drift import performance_drift
from regime_router import route

class StatisticalLearningPipeline:
    def __init__(self,ranker=None):
        self.ranker=ranker or AlphaRanker()

    def factor_audit(self,df,factors,target="forward_return",date_col="date"):
        return lab_report(df,factors,target,date_col)

    def train_challenger(self,train_df,features,target="forward_return",date_col="date"):
        self.ranker.fit(train_df,features,target,date_col)
        return {"metadata":self.ranker.metadata,"importance":self.ranker.feature_importance()}

    def score_cross_section(self,df,date_col="date"):
        return self.ranker.predict(df,date_col)

    def combine_rule_ml(self,rule_pool,alpha_rank_pct,model_approved=False):
        return resonance(rule_pool,alpha_rank_pct,model_approved)

    def confidence(self,returns):
        return bootstrap_trade_metrics(returns)

    def capital_risk(self,returns,target_return_pct=50):
        return simulate_trade_returns(returns,target_return_pct=target_return_pct)

    def drift(self,baseline_returns,recent_returns):
        return performance_drift(baseline_returns,recent_returns)

    def model_route(self,market_phase,registry=None):
        return route(market_phase,registry)
