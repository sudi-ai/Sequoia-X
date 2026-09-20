# -*- coding: utf-8 -*-
"""
潜伏雷达必须同时看 Precision 和 Recall：
- Precision：进入潜伏池后，未来是否真的启动；
- Recall：后来大涨的票，提前覆盖了多少；
避免为了漂亮胜率把系统调成“不发信号”。
"""
from __future__ import annotations

def evaluate(latent_outcomes, missed_cases):
    """
    latent_outcomes: [{"future_5d":..., "stage":..., "lead_days":...}, ...]
    missed_cases: missed_opportunity 产出的 cases
    启动成功默认未来5日收益 >= 8%。
    """
    rows=[x for x in latent_outcomes if x.get("future_5d") is not None]
    successes=[x for x in rows if float(x["future_5d"])>=8.0]
    precision=len(successes)/len(rows) if rows else None

    movers=[x for x in missed_cases if x]
    covered=[x for x in movers if x.get("had_latent")]
    recall=len(covered)/len(movers) if movers else None

    lead=[float(x.get("lead_days")) for x in successes if x.get("lead_days") is not None]
    return {
        "latent_n":len(rows),
        "precision":round(precision,4) if precision is not None else None,
        "big_mover_n":len(movers),
        "recall":round(recall,4) if recall is not None else None,
        "avg_lead_days":round(sum(lead)/len(lead),2) if lead else None,
    }
