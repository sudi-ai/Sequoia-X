from __future__ import annotations

from collections import defaultdict
from typing import Any

from .evaluation import summarize
from .storage import connect,initialize


def grouped_event_performance()->dict[str,Any]:
    """Only matured rows enter performance metrics; all others remain explicitly immature."""
    initialize();c=connect();rows=c.execute("""select f.*,r.source_api,r.policy_stage,
      coalesce(r.topic_primary,r.topic,'UNKNOWN') topic from v8_event_forward f
      join v8_event_radar r on r.event_id=f.event_id""").fetchall();c.close()
    # The same story may be syndicated dozens of times.  Performance is based
    # on one code/trading-day/horizon sample, never on raw article count.
    deduped={}
    for raw in rows:
        row=dict(raw);day=str(row.get('signal_time') or '')[:10]
        key=(str(row.get('code') or row.get('event_id')),day,str(row.get('horizon')))
        current=deduped.get(key)
        if current is None or str(row.get('signal_time') or '')<str(current.get('signal_time') or ''):
            deduped[key]=row
    groups=defaultdict(list)
    for row in deduped.values():
        row['matured']=bool(row.get('matured'))
        for key in (f"HORIZON:{row['horizon']}",f"SOURCE:{row['source_api']}|{row['horizon']}",
                    f"STAGE:{row.get('policy_stage') or 'NONE'}|{row['horizon']}",f"TOPIC:{row['topic']}|{row['horizon']}"):
            groups[key].append(row)
    return {key:summarize(items,return_key='net_return_pct') for key,items in groups.items()}
