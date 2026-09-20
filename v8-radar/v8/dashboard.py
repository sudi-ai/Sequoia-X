from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import ROOT
from .evaluation import summarize
from .research_validation import evaluate_research_sample, promotion_gate
from .storage import connect


def _band(value:Any)->str:
    try:x=float(value)
    except (TypeError,ValueError):return 'UNKNOWN'
    return '0-50' if x<50 else ('50-70' if x<70 else ('70-80' if x<80 else ('80-90' if x<90 else '90-100')))


def dashboard_data()->dict[str,Any]:
    c=connect();rows=[dict(x) for x in c.execute("""select o.*,d.action,d.decision_json from v8_execution_outcomes o
      left join v8_signal_decisions d on d.event_key=o.event_key""").fetchall()];c.close()
    dimensions=defaultdict(list)
    for row in rows:
        try:d=json.loads(row.get('decision_json') or '{}')
        except Exception:d={}
        keys=(f"track={row.get('track')}|horizon={row.get('horizon')}",f"action={row.get('action') or 'POSITION'}|horizon={row.get('horizon')}",
          f"market={d.get('market',{}).get('label','UNKNOWN')}|horizon={row.get('horizon')}",
          f"sector={d.get('sector',{}).get('label','UNKNOWN')}|horizon={row.get('horizon')}",
          f"trend={_band(d.get('trend',{}).get('score'))}|horizon={row.get('horizon')}",
          f"fund={_band(d.get('fund',{}).get('score'))}|horizon={row.get('horizon')}",
          f"persistence={_band(d.get('persistence',{}).get('score'))}|horizon={row.get('horizon')}")
        for key in keys:dimensions[key].append(row)
    groups={}
    for key,value in sorted(dimensions.items()):
        metrics=evaluate_research_sample(value)
        metrics['promotion_gate']=promotion_gate(metrics)
        groups[key]=metrics
    return {'rules':['只统计matured=1','未成熟样本不进入胜率','盘中INTRADAY_FIXED不是T+1可实现往返收益',
      '研究晋级仅生成REVIEW_ONLY证据，绝不自动修改正式评分'], 'groups':groups}


def write_markdown(path:Path|None=None)->Path:
    target=Path(path or ROOT/'reports_v8'/'V8统计面板.md');target.parent.mkdir(parents=True,exist_ok=True);data=dashboard_data()
    lines=['# V8 Research Shadow 统计面板','',*[f'- {x}' for x in data['rules']],'',
      '| 分组 | 成熟 | 未成熟 | 胜率 | 平均 | 中位 | 利润因子 |','|---|---:|---:|---:|---:|---:|---:|']
    for key,value in data['groups'].items():
        lines.append(f"| {key} | {value['mature_n']} | {value['immature_n']} | {value['win_rate']} | {value['mean']} | {value['median']} | {value['profit_factor']} |")
    target.write_text('\n'.join(lines)+'\n',encoding='utf-8');return target
