from __future__ import annotations

from collections import defaultdict
from typing import Any

from .evaluation import summarize
from .storage import connect


def build_intraday_report()->dict[str,Any]:
    c=connect();rows=[dict(x) for x in c.execute("""select o.*,d.action,d.code,d.name from v8_execution_outcomes o
      join v8_signal_decisions d on d.event_key=o.event_key where o.track='INTRADAY_FIXED'""").fetchall()];c.close()
    horizons=defaultdict(list);groups=defaultdict(list)
    for row in rows:
        horizons[str(row.get('horizon'))].append(row)
        groups[f"{row.get('action')}|{row.get('horizon')}"] .append(row)
    order={'M3':3,'M5':5,'M10':10,'M15':15,'M30':30,'M60':60,'CLOSE':999}
    return {'definition':'盘中观察收益，不是A股T+1可实现的往返交易收益；不扣交易成本',
      'by_horizon':{k:summarize(v) for k,v in sorted(horizons.items(),key=lambda x:order.get(x[0],9999))},
      'by_final_action_and_horizon':{k:summarize(v) for k,v in sorted(groups.items())}}
