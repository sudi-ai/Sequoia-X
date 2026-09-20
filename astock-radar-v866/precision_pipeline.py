# -*- coding: utf-8 -*-
"""
V8.6 Precision Engine Facade

正式链路：
V8 Primary A
→ Meta Labeler
→ Probability Calibration
→ Split Conformal
→ Selective Abstention
→ Alpha Rank
→ Execution Feasibility
→ Risk Veto
→ A+ Sniper

默认安全原则：
未批准Meta模型时，只返回REFERENCE，不影响正式V8。
"""
from __future__ import annotations
from v85_pipeline import V85Pipeline
from precision_sniper import sniper_decision

class PrecisionEngine:
    def __init__(self):
        self.base=V85Pipeline()

    def final_decision(self,primary_signal,meta_probability,conformal_result,
                       alpha_rank_pct=None,execution_ok=True,risk_veto=False,
                       market_phase=None,model_approved=False):
        return sniper_decision(
            primary_signal,meta_probability,conformal_result,alpha_rank_pct,
            execution_ok,risk_veto,market_phase,model_approved
        )
