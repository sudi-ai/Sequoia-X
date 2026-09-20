from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .config import V72_CONFIG
from .data_layer import DATA_LAYER
from .paid_enrichment import _announcement_factor, _report_factor, _row_time
from .portfolio import list_positions
from .portfolio_runtime_v72 import fetch_daily_features
from .signal_lab import DEFAULT_DB, _LOCK, _connect

CN = ZoneInfo('Asia/Shanghai')

POSITIVE_TERMS = (
    '中标','签订合同','重大合同','订单','回购','增持','业绩预增','扭亏','超预期','涨价',
    '获批','认证','投产','扩产','政策支持','补贴','国产替代','设备更新','算力','液冷','数据中心',
)
RISK_TERMS = ('立案','处罚','退市','减持','亏损','诉讼','终止','暂停','风险警示','下调')
MATERIAL_TERMS = ('重大','大额','国家','首次','独家','核心','超预期','订单','中标','政策支持')
SECTOR_KEYWORDS = {
    '液冷': ('液冷','温控','数据中心冷却'),
    '算力': ('算力','数据中心','服务器','AI基础设施'),
    '电力': ('电力','火电','水电','电网','电力改革'),
    '机器人': ('机器人','减速器','伺服','人形机器人'),
    '半导体': ('半导体','芯片','先进封装','国产替代'),
    '医药': ('创新药','医疗器械','医药','临床'),
    '新能源': ('新能源','储能','光伏','风电','锂电'),
    '低空经济': ('低空经济','无人机','通航'),
}


def ensure_schema(db_path: Path = DEFAULT_DB) -> None:
    with _LOCK:
        c = _connect(db_path)
        c.executescript('''
        CREATE TABLE IF NOT EXISTS v72_catalyst_events(
          event_key TEXT NOT NULL,code TEXT NOT NULL,name TEXT,sector TEXT,event_time TEXT NOT NULL,
          source_type TEXT NOT NULL,title TEXT NOT NULL,directness TEXT NOT NULL,score REAL NOT NULL,
          authority_score REAL,novelty_score REAL,relevance_score REAL,materiality_score REAL,unpriced_score REAL,
          ret5 REAL,ma20_gap_pct REAL,status TEXT NOT NULL DEFAULT 'SHADOW',details_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,PRIMARY KEY(event_key,code));
        CREATE INDEX IF NOT EXISTS idx_v72_catalyst_time ON v72_catalyst_events(event_time,score);
        CREATE TABLE IF NOT EXISTS v72_catalyst_push_log(
          trade_date TEXT NOT NULL,event_key TEXT NOT NULL,code TEXT NOT NULL,attempted_at TEXT NOT NULL,
          success INTEGER NOT NULL,error TEXT,PRIMARY KEY(trade_date,event_key,code));
        ''')
        c.commit(); c.close()


def _records(value: Any) -> list[dict[str, Any]]:
    if value is None: return []
    if hasattr(value,'to_dict'):
        try: return [dict(x) for x in value.to_dict(orient='records')]
        except Exception: return []
    if isinstance(value,list): return [dict(x) for x in value if isinstance(x,Mapping)]
    if isinstance(value,Mapping): return [dict(value)]
    return []


def _num(value: Any) -> float | None:
    try: return float(value)
    except Exception: return None


def _event_key(source: str, title: str, stamp: str) -> str:
    raw = f'{source}|{title.strip()}|{stamp[:10]}'.encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:24]


def _target_universe(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    targets: dict[str,dict[str,Any]] = {}
    for p in list_positions(db_path=db_path):
        if p.get('status') not in {'HOLDING','SIMULATED'}: continue
        code=str(p.get('ts_code') or '').split('.')[0].zfill(6)
        targets[code]={'code':code,'name':str(p.get('name') or ''),'sector':'','priority':'HOLDING'}
    with _LOCK:
        c=_connect(db_path)
        rows=c.execute('''select code,name,sector,snapshot_json,max(score) score,max(signal_time) signal_time
                          from signal_events where trade_date>=date('now','-3 day')
                          and engine in ('V7_SHADOW','V6_LEGACY','V6_V7_FILTERED')
                          group by code,name,sector order by score desc limit ?''',(int(V72_CONFIG.catalyst_target_top_n),)).fetchall()
        c.close()
    for row in rows:
        code=str(row['code'] or '').split('.')[0].zfill(6)
        if not code.strip('0'): continue
        sector=str(row['sector'] or '')
        if not sector:
            try:
                snap=json.loads(row['snapshot_json'] or '{}'); sector=str(snap.get('sector') or '')
            except Exception: pass
        if code in targets:
            targets[code]['sector']=targets[code].get('sector') or sector
        else:
            targets[code]={'code':code,'name':str(row['name'] or ''),'sector':sector,'priority':'CANDIDATE'}
    return list(targets.values())


def _global_news(now: datetime) -> list[dict[str,Any]]:
    start=(now-timedelta(hours=12)).strftime('%Y-%m-%d %H:%M:%S'); end=now.strftime('%Y-%m-%d %H:%M:%S')
    rows=[]
    for api in ('news','major_news'):
        try:
            raw=DATA_LAYER.relay_call(api,cache_key=f'catalyst:{api}:{now:%Y%m%d%H%M}'[:-1],ttl_seconds=300,
                                      priority='holding',src='sina',start_date=start,end_date=end)
            for r in _records(raw): rows.append({'api':api,**r})
        except Exception: continue
    return rows


def _match_news(target: Mapping[str,Any], row: Mapping[str,Any]) -> tuple[str | None,str]:
    title=str(row.get('title') or row.get('headline') or row.get('content') or '').strip()
    content=str(row.get('content') or row.get('summary') or '')
    text=f'{title} {content}'; code=str(target.get('code') or ''); name=str(target.get('name') or '')
    row_code=str(row.get('ts_code') or row.get('code') or '')
    if row_code:
        return ('DIRECT',title) if code in row_code else (None,title)
    if (name and name in text) or code in text:
        return 'DIRECT',title
    sector=str(target.get('sector') or '')
    for label,words in SECTOR_KEYWORDS.items():
        if any(w in text for w in words) and (label in sector or any(w in sector for w in words)):
            return 'SECTOR',title
    return None,title


def _unpriced(features: Mapping[str,Any]) -> tuple[float,list[str]]:
    ret5=_num(features.get('ret5')); gap=_num(features.get('ma20_gap_pct')); vol=_num(features.get('volume_ratio'))
    score=10.0; notes=[]
    if ret5 is not None and ret5>=15: score-=10; notes.append('近5日涨幅较大')
    elif ret5 is not None and ret5>=8: score-=5; notes.append('近5日已有一定涨幅')
    if gap is not None and gap>=10: score-=5; notes.append('明显偏离MA20')
    if vol is not None and vol>=2.5: score-=3; notes.append('量能已显著放大')
    if not notes: notes.append('价格尚未显示明显提前兑现')
    return max(0.0,score),notes


def _score_event(*,source_type:str,directness:str,title:str,features:Mapping[str,Any],report:Mapping[str,Any]|None=None) -> dict[str,Any]:
    authority={'ANNOUNCEMENT':20,'major_news':18,'news':12,'REPORT':14}.get(source_type,10)
    relevance=25 if directness=='DIRECT' else 15
    novelty=15
    materiality=20 if any(k in title for k in MATERIAL_TERMS) else (12 if any(k in title for k in POSITIVE_TERMS) else 6)
    unpriced,price_notes=_unpriced(features)
    score=authority+relevance+novelty+materiality+unpriced
    if any(k in title for k in RISK_TERMS): score-=35
    if report:
        if report.get('profit_forecast_trend')=='UP': score+=8
        if str(report.get('rating_change') or '').upper() not in {'','UNKNOWN'}: score+=4
    return {'score':round(max(0,min(100,score)),1),'authority_score':authority,'novelty_score':novelty,
            'relevance_score':relevance,'materiality_score':materiality,'unpriced_score':unpriced,'price_notes':price_notes}


def _save_event(event: Mapping[str,Any], db_path: Path) -> bool:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path)
        cur=c.execute('''insert or ignore into v72_catalyst_events
          (event_key,code,name,sector,event_time,source_type,title,directness,score,authority_score,novelty_score,relevance_score,materiality_score,unpriced_score,ret5,ma20_gap_pct,status,details_json,created_at)
          values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
          event['event_key'],event['code'],event.get('name'),event.get('sector'),event['event_time'],event['source_type'],event['title'],event['directness'],event['score'],
          event['authority_score'],event['novelty_score'],event['relevance_score'],event['materiality_score'],event['unpriced_score'],event.get('ret5'),event.get('ma20_gap_pct'),
          'SHADOW',json.dumps(dict(event),ensure_ascii=False,default=str),datetime.now(CN).isoformat(timespec='seconds')))
        inserted=cur.rowcount>0; c.commit(); c.close(); return inserted


def build_catalyst_message(event: Mapping[str,Any]) -> str:
    level='重点催化观察' if event['score']>=80 else '板块潜伏观察'
    benefits='直接受益个股' if event['directness']=='DIRECT' else '板块映射受益'
    try:
        event_hour=datetime.fromisoformat(str(event.get('event_time'))).astimezone(CN).hour
    except Exception:
        event_hour=datetime.now(CN).hour
    overnight=event_hour>=18 or event_hour<8
    heading='隔夜催化观察' if overnight else level
    action=('仅加入次日观察池；晚间不确认建仓。次日等待开盘后价格、板块扩散、成交额，'
            '并在09:35后结合分钟承接复核。' if overnight else
            '进入催化观察池；不因消息直接买入，等待09:35后板块扩散、分钟承接和成交额确认。')
    return '\n'.join([
      f'【V7.2催化潜伏雷达｜{heading}】',
      f"{event.get('name')} {event.get('code')}｜{event.get('sector') or '板块待确认'}",
      f"📰 事件：{event.get('title')}",
      f"来源：{event.get('source_type')}｜受益层级：{benefits}｜事件评分 {event.get('score')}",
      f"📊 近5日 {_num(event.get('ret5')) or 0:+.2f}%｜距MA20 {_num(event.get('ma20_gap_pct')) or 0:+.2f}%",
      f"💡 定价判断：{'；'.join(event.get('price_notes') or [])}", '',
      '👉 操作', action,
      '🛡 取消：消息相关性被证伪、高开过多、冲高回落、板块无扩散或价格已明显兑现。',
      '📌 Catalyst Shadow仅用于提前发现和Forward验证，不自动下单。'
    ])


def _push_once(event: Mapping[str,Any], db_path: Path) -> dict[str,Any]:
    if not V72_CONFIG.catalyst_push: return {'sent':False,'reason':'disabled'}
    day=str(event['event_time'])[:10]; ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path)
        existing=c.execute('select success from v72_catalyst_push_log where trade_date=? and event_key=? and code=?',(day,event['event_key'],event['code'])).fetchone()
        count=c.execute('select count(*) n from v72_catalyst_push_log where trade_date=? and success=1',(day,)).fetchone()['n']; c.close()
    if existing and int(existing['success'] or 0)==1: return {'sent':False,'reason':'deduped'}
    if int(count)>=V72_CONFIG.catalyst_max_push_per_day: return {'sent':False,'reason':'daily_limit'}
    from .wework_shadow_push import send_message
    ok,detail=send_message(build_catalyst_message(event))
    with _LOCK:
        c=_connect(db_path); c.execute('insert or replace into v72_catalyst_push_log values(?,?,?,?,?,?)',(day,event['event_key'],event['code'],datetime.now(CN).isoformat(timespec='seconds'),int(ok),'' if ok else str(detail)[:160])); c.commit(); c.close()
    return {'sent':bool(ok),'reason':'sent' if ok else 'failed'}


def run_catalyst_scan(*,now:datetime|None=None,db_path:Path=DEFAULT_DB,push:bool=True) -> dict[str,Any]:
    if not V72_CONFIG.catalyst_enable: return {'enabled':False}
    now=(now or datetime.now(CN)).astimezone(CN); targets=_target_universe(db_path); news_rows=_global_news(now)
    events=[]; inserted=sent=0
    for target in targets:
        code=target['code']; features=fetch_daily_features(code,now); ann=_announcement_factor(code,now); report=_report_factor(code,now)
        candidates=[]
        for row in news_rows:
            direct,title=_match_news(target,row)
            if not direct or not title or not any(k in title for k in POSITIVE_TERMS): continue
            candidates.append((str(row.get('api') or 'news'),direct,title,_row_time(row)))
        for reason in ann.get('announcement_positive_reasons') or []:
            candidates.append(('ANNOUNCEMENT','DIRECT',str(reason),None))
        if report.get('profit_forecast_trend')=='UP' or str(report.get('rating_change') or '').upper() not in {'','UNKNOWN'}:
            candidates.append(('REPORT','DIRECT',f"研报变化：盈利预测{report.get('profit_forecast_trend')}｜评级{report.get('rating_change')}",None))
        best=None
        for source,direct,title,when in candidates:
            stamp=(when.astimezone(CN) if when else now).isoformat(timespec='seconds')
            scored=_score_event(source_type=source,directness=direct,title=title,features=features,report=report if source=='REPORT' else None)
            event={**target,**scored,'source_type':source,'directness':direct,'title':title,'event_time':stamp,
                   'event_key':_event_key(source,title,stamp),'ret5':features.get('ret5'),'ma20_gap_pct':features.get('ma20_gap_pct')}
            if best is None or event['score']>best['score']: best=event
        if not best: continue
        was_inserted=_save_event(best,db_path); inserted+=int(was_inserted); events.append(best)
        if was_inserted and best['score']>=V72_CONFIG.catalyst_push_score and push:
            sent+=int(_push_once(best,db_path).get('sent',False))
        if was_inserted:
            try:
                from .forward_v72 import freeze_snapshot,get_or_create_episode
                price=_num(features.get('close'))
                if price:
                    ep=get_or_create_episode(code,best['event_time'][:10],direction='LONG',signal_type='CATALYST',db_path=db_path)
                    freeze_snapshot(code=code,signal_time=best['event_time'],entry_reference_price=price,features=features,research={'catalyst':best},
                                    current_score=None,research_score=best['score'],board_type=features.get('board_type'),market_regime=features.get('market_regime'),episode_id=ep.get('episode_id'),db_path=db_path)
            except Exception: pass
    return {'enabled':True,'targets':len(targets),'news_rows':len(news_rows),'events':len(events),'inserted':inserted,'sent':sent,
            'top':sorted(events,key=lambda x:x['score'],reverse=True)[:5]}


__all__=['run_catalyst_scan','build_catalyst_message','ensure_schema','_match_news','_score_event']
