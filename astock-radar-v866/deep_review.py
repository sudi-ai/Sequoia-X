# -*- coding: utf-8 -*-
"""
A级候选深度复核接口。
原则：AI/Agent 只做“反方审查/风险补充”，不直接替代量化发动机。
第三方 TradingAgents 等建议放独立环境，避免依赖冲突。
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class ReviewResult:
    passed:bool
    downgrade:bool
    reasons:list
    evidence:list

class DeepReviewer:
    def review(self,candidate,context=None):
        # 默认本地规则复核；后续可实现 TradingAgentsAdapter。
        reasons=[]; evidence=[]
        if candidate.get("event_risk",0)>=60:reasons.append("事件风险偏高")
        if candidate.get("unlock_risk",0)>=60:reasons.append("解禁风险偏高")
        if candidate.get("distribution_risk",0)>=65:reasons.append("派发证据增强")
        if candidate.get("daily_pct",0)>9:reasons.append("追高风险")
        if candidate.get("market_phase") in ("高潮","退潮"):reasons.append("市场阶段不利于追涨")
        return ReviewResult(passed=not reasons,downgrade=bool(reasons),reasons=reasons,evidence=evidence)
