"""Forward event observation; immutable origins, evidence gates, no order side effects."""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from fusion_engine import historical_structure, number, quote_issues, timestamp
from fusion_event_calendar import calendar_events, THEMES

VERSION = 'EVENT_RESEARCH_1'
LABELS = {'THEME_ONLY':'主题关联待核验', 'WAIT_DATA':'等待行情/历史数据',
          'EVENT_WATCH':'事件观察', 'LOW_POSITION_WATCH':'相对低位结构观察',
          'PRICE_RESPONSE':'价格响应待多方确认', 'EVIDENCE_ALIGNED':'多项证据同向（研究）',
          'CROWDED':'已有明显上涨，停止提前低位假设', 'FADING':'结构或证据走弱',
          'BLOCKED':'风险否决', 'EXPIRED':'事件结束', 'CANCELLED':'事件取消'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                   default=str).encode()).hexdigest()[:24]


def evidence_status(records, event_id, code, now):
    result = {}
    for kind in ('demand', 'flow', 'sector', 'business', 'risk'):
        valid = []
        for r in records:
            if not isinstance(r, dict) or r.get('kind') != kind or r.get('event_id') != event_id or r.get('ts_code') != code:
                continue
            published, received, until = (timestamp(r.get(k)) for k in ('published_at','observed_at','valid_until'))
            if (not published or not received or not until or not published <= received <= now <= until
                    or not str(r.get('source','')).startswith('https://') or not r.get('fact')
                    or r.get('status') not in ('SUPPORTS','CONTRADICTS')):
                continue
            # Do not accept a year-long validity marker for time-sensitive evidence.
            max_age = 86400 if kind in ('flow','sector') else 7*86400
            if (now-received).total_seconds() > max_age or (now-published).total_seconds() > max_age:
                continue
            valid.append(r)
        # Conflicting active evidence is not silently collapsed into a positive verdict.
        states = {r['status'] for r in valid}
        state = ('CONFLICT' if len(states)>1 else next(iter(states))) if states else 'UNKNOWN'
        result[kind] = dict(status=state, records=valid)
    return result


def evaluate(event, stock, quote, bars, now, previous_day, evidence=()):
    code = stock['ts_code']
    r = dict(event_id=event['id'], event_name=event['name'], end=event['end'],
             ts_code=code, name=stock.get('name',code), industry=stock.get('industry',''),
             themes=stock['themes'], mapping='行业分类关联；主营受益程度待核验',
             evaluated_at=now.isoformat(), state='WAIT_DATA', price=None, source_trade_time=None,
             execution_eligible=False, probability=None, structure={}, evidence={},
             next_condition='核对主营关联、需求、资金、板块及价格响应',
             invalidation='事件取消、风险确认、结构破坏或证据反转时停止沿用假设')
    ev = evidence_status(evidence,event['id'],code,now)
    r['evidence'] = ev
    if event.get('cancelled'):
        r['state']='CANCELLED'; return r
    if event['end'] < now.date().isoformat():
        r['state']='EXPIRED'; return r
    if ev['risk']['status'] in ('SUPPORTS','CONFLICT') or any(x in str(stock.get('name','')).upper() for x in ('ST','退')):
        r['state']='BLOCKED'; return r
    quote = quote or {}
    fresh = not quote_issues(quote,now,True)
    ordered = sorted(bars,key=lambda b:str(b.get('trade_date','')))
    current_history = bool(ordered and previous_day and str(ordered[-1].get('trade_date')) == previous_day)
    if fresh:
        r.update(price=number(quote.get('close')), source_trade_time=quote.get('source_trade_time') or quote.get('trade_time'))
    r['quote_fresh'] = fresh
    if not fresh or not current_history:
        return r
    s = historical_structure(ordered,quote,now)
    r['structure'] = s
    if s.get('status') != 'OK': return r
    pct=(float(quote['close'])/float(quote['pre_close'])-1)*100
    position=number(s.get('position60'))
    r['pct_chg'] = round(pct,3)
    if position is None: return r
    if any(ev[k]['status'] in ('CONTRADICTS','CONFLICT') for k in ('demand','flow','sector','business')):
        r['state']='FADING'
    elif s['return20_pct'] > 15 or position > .85 or pct > 4:
        r['state']='CROWDED'
    elif s['ma20_slope_pct'] < 0 or not s['higher_lows'] or float(quote['close']) < s['support']:
        r['state']='FADING'
    elif 0 <= position <= .60 and -8 <= s['return20_pct'] <= 15 and s['range20_pct'] <= 18 and s['volume_contraction'] <= 1.05:
        r['state']='LOW_POSITION_WATCH'
    else:
        r['state']='EVENT_WATCH'
    if r['state'] in ('LOW_POSITION_WATCH','EVENT_WATCH') and 0 < pct <= 4 and float(quote['close']) >= s['ma20']:
        if all(ev[k]['status']=='SUPPORTS' for k in ('demand','flow','sector','business')):
            r['state']='EVIDENCE_ALIGNED'
        elif sum(ev[k]['status']=='SUPPORTS' for k in ('flow','sector')) > 0:
            r['state']='PRICE_RESPONSE'
    return r


class EventRadar:
    def __init__(self, directory, operations):
        self.root=Path(directory); self.operations=operations
        self.db=sqlite3.connect(str(self.root/'fusion_event_radar.sqlite3'))
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS event_origin(id TEXT PRIMARY KEY, first_seen_at TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS candidate_origin(id TEXT PRIMARY KEY,event_id TEXT,code TEXT,first_seen_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS price_origin(id TEXT PRIMARY KEY,observed_at TEXT,source_time TEXT,price REAL);
        CREATE TABLE IF NOT EXISTS observation(id TEXT,observed_at TEXT,payload TEXT,PRIMARY KEY(id,observed_at));
        CREATE TABLE IF NOT EXISTS current(id TEXT PRIMARY KEY,state TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS metadata(id TEXT PRIMARY KEY,payload TEXT);
        CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON event_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_no_update BEFORE UPDATE ON candidate_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS price_no_update BEFORE UPDATE ON price_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS observation_no_update BEFORE UPDATE ON observation BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON event_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS candidate_no_delete BEFORE DELETE ON candidate_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS price_no_delete BEFORE DELETE ON price_origin BEGIN SELECT RAISE(ABORT,'immutable'); END;
        CREATE TRIGGER IF NOT EXISTS observation_no_delete BEFORE DELETE ON observation BEGIN SELECT RAISE(ABORT,'immutable'); END;
        ''')
        self.last_run=None; self.last_universe_try=None
        self.status={'status':'NOT_STARTED','execution_eligible':False}

    def _list(self,name):
        path=self.root/name
        if not path.exists():return [],None
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(data,list) or len(data)>5000: raise ValueError('list expected')
            return data,None
        except (OSError,ValueError):return [],name+' 无效，未使用'

    def universe(self, now, fetch, allow_refresh):
        saved=self.db.execute("SELECT payload FROM metadata WHERE id='universe'").fetchone()
        cache=json.loads(saved[0]) if saved else {}
        # Reuse the existing collector's dated cache without another paid API request.
        shared=self.root/'p0_stock_basic_cache.json'
        if shared.exists():
            try:
                incoming=json.loads(shared.read_text(encoding='utf-8'))
                at=timestamp(incoming.get('observed_at'))
                old_at=timestamp(cache.get('observed_at'))
                source=str(incoming.get('source',''))
                records=incoming.get('records',[])
                if (at and at<=now and (old_at is None or at>old_at) and source=='Tushare.stock_basic'
                        and isinstance(records,list) and len(records)>=3000):
                    cache=dict(rows=[r for r in records if isinstance(r,dict) and r.get('ts_code') and r.get('industry')],
                               observed_at=at.isoformat(),source=source,coverage='PROVIDER_RETURNED_LISTED_UNIVERSE')
                    with self.db:self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('universe',?)",(json.dumps(cache,ensure_ascii=False),))
            except (OSError,ValueError,TypeError):
                pass
        observed=timestamp(cache.get('observed_at'))
        fresh=observed is not None and 0 <= (now-observed).total_seconds() <= 7*86400
        if allow_refresh and (not observed or observed.date()!=now.date()) and (self.last_universe_try is None or (now-self.last_universe_try).total_seconds()>=3600):
            self.last_universe_try=now
            rows,status=fetch('stock_basic',dict(exchange='',list_status='L',fields='ts_code,name,industry,list_date'))
            rows=[r for r in rows if r.get('ts_code') and r.get('industry')]
            if status=='OK' and len(rows)>=3000:
                cache=dict(rows=rows,observed_at=now.isoformat(),source='stock_basic',coverage='PROVIDER_RETURNED_LISTED_UNIVERSE')
                with self.db:self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('universe',?)",(json.dumps(cache,ensure_ascii=False),))
                fresh=True
        return cache.get('rows',[]) if fresh else [], cache.get('observed_at')

    def step(self, now, fetch, preparation, quotes, previous_day, allow_refresh=False, enabled=False):
        if self.last_run and 0 <= (now-self.last_run).total_seconds()<60:return self.status
        self.last_run=now
        extra,error=self._list('fusion_events.json')
        evidence,evidence_error=self._list('fusion_event_evidence.json')
        evidence_index={}
        for record in evidence:
            if isinstance(record,dict):
                evidence_index.setdefault((record.get('event_id'),record.get('ts_code')),[]).append(record)
        calendar=calendar_events(now,extra=extra)
        calendar['errors'] += [e for e in (error,evidence_error) if e]
        universe,universe_at=self.universe(now,fetch,allow_refresh)
        live_events=calendar['events']
        current_ids={e['id'] for e in live_events}
        # Retain ended/removed events for explicit retirement, never overwrite their origins.
        for row in self.db.execute('SELECT payload FROM event_origin'):
            old=json.loads(row[0])
            if old['id'] not in current_ids:
                old['cancelled']=old['end']>=now.date().isoformat() and not calendar['errors']
                old['input_unavailable']=bool(calendar['errors'])
                live_events.append(old)
        candidates=[]; transitions=[]; mapped_total=0
        for event in live_events:
            with self.db:self.db.execute('INSERT OR IGNORE INTO event_origin VALUES (?,?,?)',
                (event['id'],now.isoformat(),json.dumps(event,ensure_ascii=False)))
            active=not event.get('cancelled') and event['end']>=now.date().isoformat()
            mapped=[]
            if active:
                for stock in universe:
                    themes=[t for t in event['themes'] if str(stock.get('industry','')) in THEMES[t]['industries']]
                    business=evidence_status(evidence_index.get((event['id'],stock['ts_code']),[]),event['id'],stock['ts_code'],now)['business']
                    if business['status']=='SUPPORTS':
                        themes += [r['theme'] for r in business['records'] if r.get('theme') in event['themes']]
                        themes=list(dict.fromkeys(themes))
                    if themes and not any(x in str(stock.get('name','')).upper() for x in ('ST','退')):
                        mapped.append(dict(stock,themes=themes))
            mapped_total+=len(mapped)
            # Cover different themes fairly before the cap; this is not a return ranking.
            buckets=[[s for s in sorted(mapped,key=lambda s:s['ts_code']) if t in s['themes']]
                     for t in event['themes']]
            selected=[]; picked=set()
            for index in range(max((len(b) for b in buckets),default=0)):
                for bucket in buckets:
                    if index<len(bucket) and bucket[index]['ts_code'] not in picked and len(selected)<80:
                        selected.append(bucket[index]);picked.add(bucket[index]['ts_code'])
            ids={s['ts_code'] for s in selected}
            previous=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM candidate_origin WHERE event_id=?',(event['id'],))]
            selected += [r for r in previous if r['ts_code'] not in ids]
            for stock in selected:
                code=stock['ts_code']; identity=digest([event['id'],code])
                bars=preparation.bars(code,previous_day) if previous_day and active and not quote_issues(quotes.get(code,{}),now,True) else []
                result=evaluate(event,stock,quotes.get(code),bars,now,previous_day,
                                evidence_index.get((event['id'],code),[]))
                if event.get('input_unavailable') and active:
                    result.update(state='WAIT_DATA',next_condition='修复事件输入后再核验，不能因文件损坏判定事件取消')
                if active and code not in ids:
                    result.update(state='WAIT_DATA',next_condition='行业映射不再可核验或候选覆盖不足，暂停旧观察')
                with self.db:
                    self.db.execute('INSERT OR IGNORE INTO candidate_origin VALUES (?,?,?,?,?)',
                        (identity,event['id'],code,now.isoformat(),json.dumps(stock,ensure_ascii=False)))
                    if result.get('price') and result.get('quote_fresh'):
                        self.db.execute('INSERT OR IGNORE INTO price_origin VALUES (?,?,?,?)',
                            (identity,now.isoformat(),result['source_trade_time'],result['price']))
                origin=self.db.execute('SELECT first_seen_at FROM candidate_origin WHERE id=?',(identity,)).fetchone()
                price=self.db.execute('SELECT * FROM price_origin WHERE id=?',(identity,)).fetchone()
                result.update(first_seen_at=origin[0],first_seen_price=price['price'] if price else None,
                              first_price_at=price['source_time'] if price else None)
                old=self.db.execute('SELECT state FROM current WHERE id=?',(identity,)).fetchone()
                raw=json.dumps(result,ensure_ascii=False,allow_nan=False)
                with self.db:
                    last=self.db.execute('SELECT observed_at FROM observation WHERE id=? ORDER BY observed_at DESC LIMIT 1',(identity,)).fetchone()
                    if old is None or old[0]!=result['state'] or not last or (now-timestamp(last[0])).total_seconds()>=900:
                        self.db.execute('INSERT OR IGNORE INTO observation VALUES (?,?,?)',(identity,now.isoformat(),raw))
                    self.db.execute('INSERT OR REPLACE INTO current VALUES (?,?,?)',(identity,result['state'],raw))
                if (old is None and result['state'] not in ('WAIT_DATA','EXPIRED','CANCELLED')) or (old and old[0]!=result['state']):
                    transitions.append(result)
                if active:candidates.append(result)
        active_events=[e for e in live_events if not e.get('cancelled') and e['end']>=now.date().isoformat()]
        # New event / weekly milestone / state change digest, at most one notice per evaluation.
        signature=digest([(e['id'],e['start'],e['end'],max(0,(dt.date.fromisoformat(e['start'])-now.date()).days)//7) for e in active_events])
        saved=self.db.execute("SELECT payload FROM metadata WHERE id='calendar_signature'").fetchone()
        changed=not saved or saved[0]!=signature
        notice=None
        if (changed and active_events) or transitions:
            lines=['📅 新版｜事件提前观察雷达',now.date().isoformat()+'｜未来60天｜研究观察']
            for e in active_events[:5]:
                days=(dt.date.fromisoformat(e['start'])-now.date()).days
                lines.append(e['name']+'｜'+('还有'+str(days)+'天' if days>0 else '进行中'))
            if transitions:
                lines.append('状态变化（完整列表见工作台）：')
                for r in transitions[:4]:lines.append(r['name']+' '+r['ts_code']+'｜'+r['event_name']+'｜'+LABELS[r['state']])
            lines += ['行业关联候选 '+str(len(candidates))+'；行情/需求等缺失时不视为已确认。',
                      '节日只是研究线索；相对低位不代表低估，不给上涨概率或建仓指令。']
            notice=self.operations.emit('EVENT:'+now.isoformat()+':'+digest(lines),'EVENT_RESEARCH','\n'.join(lines),now,enabled)
        with self.db:self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('calendar_signature',?)",(signature,))
        rank={'EVIDENCE_ALIGNED':0,'LOW_POSITION_WATCH':1,'PRICE_RESPONSE':2,'EVENT_WATCH':3,'WAIT_DATA':4,'CROWDED':5,'FADING':6,'BLOCKED':7}
        candidates.sort(key=lambda r:(rank.get(r['state'],8),r['ts_code'],r['event_id']))
        self.status=dict(version=VERSION,status='OBSERVING' if universe else 'WAIT_INDUSTRY_DATA',
            generated_at=now.isoformat(),events=active_events,candidates=candidates,calendar=calendar,
            themes=THEMES,universe_observed_at=universe_at,mapped_total=mapped_total,
            candidate_limit_per_event=80,coverage_limited=mapped_total>len(candidates),last_notice=notice,
            execution_eligible=False,probability=None,
            evidence_coverage='行业分类由现有数据源提供；主营/需求/资金/板块证据需带来源录入，尚未自动采集',
            validation='无样本外收益验证；历史K线仅当前可见背景，不能回填过去预测')
        return self.status

    def shortlist(self):
        return list(dict.fromkeys(r['ts_code'] for r in self.status.get('candidates',[])
                    if r['state'] in ('LOW_POSITION_WATCH','PRICE_RESPONSE','EVIDENCE_ALIGNED')))[:24]

    def close(self):self.db.close()
