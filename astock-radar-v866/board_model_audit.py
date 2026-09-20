# -*- coding: utf-8 -*-
"""
连板潜力模型审计：
不只看“高分票涨没涨”，而看不同等级的真实晋级率、召回率和样本量。
"""
from __future__ import annotations

def evaluate(samples):
    """
    samples:
      board_grade / board_potential / label(未来是否晋级>=2板)
    """
    rows=[x for x in samples if x.get("label") is not None]
    if not rows:return {"n":0}
    actual=sum(int(x["label"]) for x in rows)
    out={"n":len(rows),"actual_promoted":actual,"grades":{}}
    for g in ("A","B","C","REJECT"):
        sub=[x for x in rows if x.get("board_grade")==g]
        if not sub:continue
        wins=sum(int(x["label"]) for x in sub)
        out["grades"][g]={"n":len(sub),"promotion_rate":round(wins/len(sub),4)}
    predicted=[x for x in rows if x.get("board_grade") in ("A","B")]
    hit=sum(int(x["label"]) for x in predicted)
    out["ab_precision"]=round(hit/len(predicted),4) if predicted else None
    out["promotion_recall"]=round(hit/actual,4) if actual else None
    a=[x for x in rows if x.get("board_grade")=="A"]
    out["a_precision"]=round(sum(int(x["label"]) for x in a)/len(a),4) if a else None
    return out

def quality_gate(metrics,min_samples=60,min_a_samples=20):
    if metrics.get("n",0)<min_samples:
        return {"passed":False,"reason":"总样本不足"}
    ag=metrics.get("grades",{}).get("A",{})
    if ag.get("n",0)<min_a_samples:
        return {"passed":False,"reason":"A级样本不足"}
    # 连板潜力不是买点胜率，不要求80%；A级1进2命中达到65%已很强，仍需原V8买点/T+1。
    if (metrics.get("a_precision") or 0)<.65:
        return {"passed":False,"reason":"A级晋级精度不足"}
    if (metrics.get("promotion_recall") or 0)<.45:
        return {"passed":False,"reason":"大部分晋级股仍被漏掉"}
    return {"passed":True,"reason":"样本、精度、召回均达标"}
