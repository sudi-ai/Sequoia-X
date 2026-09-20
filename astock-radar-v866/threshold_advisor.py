# -*- coding: utf-8 -*-
"""
阈值顾问：只给 Challenger 建议，不自动修改正式参数。
防止系统因为短期样本过拟合。
"""
from __future__ import annotations

def advise(metrics, current_watch=68, current_ready=78, current_start=86, min_samples=50):
    n=max(int(metrics.get("latent_n",0)),int(metrics.get("big_mover_n",0)))
    if n<min_samples:
        return {"action":"KEEP","reason":f"样本仅{n}，不足{min_samples}，禁止自动调参",
                "watch":current_watch,"ready":current_ready,"start":current_start}

    precision=metrics.get("precision")
    recall=metrics.get("recall")
    watch,ready,start=current_watch,current_ready,current_start
    notes=[]

    # Recall太低：说明漏报多，只小幅降低潜伏阈值，不碰真正买点阈值。
    if recall is not None and recall<0.45:
        watch=max(62,watch-2);ready=max(watch+6,ready-1);notes.append("大涨股覆盖率偏低，轻微放宽潜伏池")
    # Precision太低：潜伏池噪声太多，提高阈值。
    if precision is not None and precision<0.35:
        watch=min(76,watch+2);ready=min(86,ready+2);start=min(92,start+1);notes.append("潜伏池噪声偏高，提高门槛")
    # 两者都健康时不追求更多信号。
    if precision is not None and recall is not None and precision>=0.5 and recall>=0.6:
        notes.append("精度与覆盖率均健康，保持正式参数")

    return {"action":"CHALLENGER" if (watch,ready,start)!=(current_watch,current_ready,current_start) else "KEEP",
            "reason":"；".join(notes) or "暂不调整","watch":watch,"ready":ready,"start":start}
