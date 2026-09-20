# -*- coding: utf-8 -*-
"""
V8.6 A+ Sniper 决策层。
只有在所有高精度条件同时满足时才允许A+。
“拒绝交易”是合法且重要的输出。
"""
from __future__ import annotations
from conformal_selective import selective_decision

def sniper_decision(primary_signal,meta_probability,conformal_result,
                    alpha_rank_pct=None,execution_ok=True,risk_veto=False,
                    market_phase=None,model_approved=False):
    # Primary必须至少是V8 A级。B/C不允许被ML“抬成”A+
    if primary_signal.get("pool")!="A":
        return {"decision":"ABSTAIN","grade":primary_signal.get("pool","C"),
                "reasons":["Primary Signal不是A级"]}

    # 未批准Meta模型：只生成研究标签，不改变实盘
    if not model_approved:
        return {
            "decision":"REFERENCE",
            "grade":"A",
            "reasons":["Meta模型尚未通过Champion/Challenger治理"],
            "meta_probability":float(meta_probability),
            "conformal":conformal_result,
        }

    x=selective_decision(meta_probability,conformal_result,alpha_rank_pct,execution_ok,risk_veto)
    # 高潮/退潮额外禁止A+，只保留A或放弃
    if x["decision"]=="EXECUTE" and x["grade"]=="A+" and market_phase in ("高潮","退潮"):
        x={"decision":"EXECUTE","grade":"A","reasons":["市场阶段不允许A+"]}
    x["meta_probability"]=float(meta_probability)
    x["conformal"]=conformal_result
    return x
