from __future__ import annotations
from typing import Any,Iterable,Mapping


def classify_event(texts:Iterable[str])->dict[str,Any]:
    text=' '.join(str(x) for x in texts if x).strip()
    if not text:return {'status':'UNKNOWN','direction':'NEUTRAL','risk_level':'UNKNOWN','reasons':[]}
    completion=('减持完毕','减持计划实施完毕','减持期限届满','未实施减持','未减持')
    hard=('立案调查','退市风险警示','终止上市','债务违约','重大违法')
    negative=('减持计划','拟减持','大比例解禁','业绩预亏','重大诉讼','监管处罚')
    positive=('回购股份','增持计划','重大合同','中标通知','业绩预增')
    if any(x in text for x in hard):return {'status':'CLASSIFIED','direction':'NEGATIVE','risk_level':'HARD_BLOCK','reasons':[x for x in hard if x in text]}
    if any(x in text for x in completion) and not any(x in text for x in ('新减持计划','拟继续减持')):
        return {'status':'CLASSIFIED','direction':'NEUTRAL','risk_level':'LOW','reasons':['减持完成/届满语义，不视为新增减持']}
    if any(x in text for x in negative):return {'status':'CLASSIFIED','direction':'NEGATIVE','risk_level':'HIGH','reasons':[x for x in negative if x in text]}
    if any(x in text for x in positive):return {'status':'CLASSIFIED','direction':'POSITIVE','risk_level':'LOW','reasons':[x for x in positive if x in text]}
    return {'status':'CLASSIFIED','direction':'NEUTRAL','risk_level':'UNKNOWN','reasons':['未命中高置信事件类型']}


def announcement_texts(announcement:Mapping[str,Any])->list[str]:
    values=[]
    for key in ('title','ann_title','summary','content','risk_reasons','announcement_risk_reasons'):
        value=announcement.get(key)
        if isinstance(value,list):values.extend(str(x) for x in value)
        elif value:values.append(str(value))
    rows=announcement.get('items')
    if isinstance(rows,list):
        for row in rows[:20]:
            if isinstance(row,Mapping):values.append(str(row.get('title') or row.get('summary') or ''))
    return values
