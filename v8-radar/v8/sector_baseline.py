from __future__ import annotations

import json
from datetime import datetime
from typing import Any,Mapping

from .storage import connect,initialize


def save_sector_snapshots(now:datetime,sectors:Mapping[str,Mapping[str,Any]])->int:
    initialize();c=connect();minute=now.strftime('%H:%M');values=[]
    for industry,row in sectors.items():
        values.append((now.isoformat(timespec='seconds'),now.date().isoformat(),minute,industry,row.get('amount'),
          row.get('up_ratio'),row.get('median_pct'),row.get('top3_pct'),row.get('count'),row.get('above_vwap_ratio'),
          json.dumps(dict(row),ensure_ascii=False,default=str)))
    c.executemany("""insert into v8_sector_intraday_snapshots(observed_at,trade_date,minute_key,industry,amount,
      up_ratio,median_pct,top3_pct,stock_count,above_vwap_ratio,details_json) values(?,?,?,?,?,?,?,?,?,?,?)
      on conflict(trade_date,minute_key,industry) do update set observed_at=excluded.observed_at,amount=excluded.amount,
      up_ratio=excluded.up_ratio,median_pct=excluded.median_pct,top3_pct=excluded.top3_pct,
      stock_count=excluded.stock_count,above_vwap_ratio=excluded.above_vwap_ratio,details_json=excluded.details_json""",values)
    c.commit();c.close();return len(values)


def sector_response(industries:list[str],now:datetime|None=None)->dict[str,Any]:
    now=now or datetime.now().astimezone();initialize();c=connect();responses=[]
    for industry in dict.fromkeys(x for x in industries if x):
        current=c.execute("""select * from v8_sector_intraday_snapshots where industry=? and trade_date=?
          order by observed_at desc limit 1""",(industry,now.date().isoformat())).fetchone()
        if not current:continue
        minute=str(current['minute_key']);history=c.execute("""select trade_date,max(amount) amount from v8_sector_intraday_snapshots
          where industry=? and trade_date<? and minute_key between ? and ? group by trade_date order by trade_date desc limit 5""",
          (industry,now.date().isoformat(),minute,minute)).fetchall()
        historical=[float(x['amount']) for x in history if x['amount'] not in (None,0)]
        baseline=sum(historical)/len(historical) if historical else None
        ratio=float(current['amount'])/baseline if baseline and current['amount'] else None
        responses.append({'industry':industry,'current_amount':current['amount'],'baseline_5d_amount':baseline,
          'baseline_days':len(historical),'volume_ratio':ratio,'up_ratio':current['up_ratio'],
          'median_pct':current['median_pct'],'leader_pct':current['top3_pct'],
          'above_vwap_ratio':current['above_vwap_ratio'],'snapshot_time':current['observed_at']})
    c.close()
    if not responses:return {'status':'UNKNOWN','responses':[],'data_limitations':['相关行业尚无分钟快照']}
    usable=[x for x in responses if x['volume_ratio'] is not None]
    return {'status':'OK' if usable else 'WARMING_UP','responses':responses,
      'volume_ratio':max((x['volume_ratio'] for x in usable),default=None),
      'up_ratio':max((float(x['up_ratio']) for x in responses if x['up_ratio'] is not None),default=None),
      'leader_pct':max((float(x['leader_pct']) for x in responses if x['leader_pct'] is not None),default=None),
      'above_vwap_ratio':max((float(x['above_vwap_ratio']) for x in responses if x['above_vwap_ratio'] is not None),default=None),
      'baseline_days':max((x['baseline_days'] for x in responses),default=0),
      'data_limitations':[] if usable else ['需要积累最多5个交易日同刻成交快照']}
