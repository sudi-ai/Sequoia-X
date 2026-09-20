"""New-only planning, read-only legacy evidence and durable operator notices."""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from fusion_engine import number, timestamp, TZ
from fusion_wecom import channel, send_text


class Operations:
    def __init__(self, directory):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.root / 'fusion_operations.sqlite3'))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS notice(id TEXT PRIMARY KEY,kind TEXT,created_at TEXT,
          content TEXT,status TEXT,ack TEXT);
        CREATE TABLE IF NOT EXISTS state(id TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS legacy_evidence(id TEXT PRIMARY KEY,source TEXT,
          imported_at TEXT,payload TEXT);
        ''')
        with self.db:
            self.db.execute("UPDATE notice SET status='UNCERTAIN' WHERE status='SENDING'")

    def get(self, key):
        row = self.db.execute('SELECT value FROM state WHERE id=?',(key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value',(key,value))

    def emit(self, identity, kind, text, now, enabled, sender=None):
        sender = sender or send_text
        # Each row corresponds to exactly one visible message. Unknown delivery is never retried.
        text = text.encode('utf-8')[:1800].decode('utf-8',errors='ignore')
        with self.db:
            inserted = self.db.execute('INSERT OR IGNORE INTO notice VALUES (?,?,?,?,?,?)',
                (identity,kind,now.isoformat(),text,'SENDING' if enabled else 'HELD','')).rowcount
        if not inserted:
            return 'ALREADY_RECORDED'
        if not enabled:
            return 'HELD'
        try:
            result = sender(text)
            status = result.get('status')
            status = status if status in ('SENT','FAILED','UNCERTAIN') else 'UNCERTAIN'
        except Exception:
            status = 'UNCERTAIN'
        with self.db:
            self.db.execute('UPDATE notice SET status=?,ack=? WHERE id=?',(status,status,identity))
        return status

    def import_legacy(self, now):
        # Explicit source manifest only. No recursive scans or writes to old databases.
        manifest = self.root / 'fusion_legacy_sources.json'
        if not manifest.exists():
            return {'status':'NOT_CONFIGURED','imported':0,'policy':'READ_ONLY_NOT_PIT'}
        try:
            sources = json.loads(manifest.read_text(encoding='utf-8'))
            if not isinstance(sources,list):
                raise ValueError('manifest')
            count = 0
            for spec in sources[:10]:
                p = Path(spec['path'])
                if not p.is_absolute() or not p.is_file():
                    continue
                # CSV exports are the deliberate interchange boundary; no schema guessing.
                if p.suffix.lower() != '.csv':
                    continue
                import csv
                with p.open(encoding='utf-8-sig',newline='') as f:
                    for index,row in enumerate(csv.DictReader(f)):
                        if index >= 20000:
                            break
                        allowed = ('ts_code','code','name','trade_date','first_seen_at','observed_at','price','score','reason','state')
                        record = {k:row[k] for k in allowed if row.get(k)}
                        if not record.get('ts_code',record.get('code')):
                            continue
                        raw = json.dumps(record,ensure_ascii=False,sort_keys=True)
                        key = hashlib.sha256((str(p)+'|'+raw).encode()).hexdigest()
                        with self.db:
                            count += self.db.execute('INSERT OR IGNORE INTO legacy_evidence VALUES (?,?,?,?)',
                                (key,str(p),now.isoformat(),raw)).rowcount
            return {'status':'IMPORTED_REFERENCE_ONLY','imported':count,'policy':'READ_ONLY_NOT_PIT'}
        except (OSError,ValueError,KeyError,TypeError):
            return {'status':'SOURCE_ERROR','imported':0,'policy':'READ_ONLY_NOT_PIT'}

    def portfolio(self, now, quotes, enabled):
        p = self.root / 'fusion_positions.json'
        if not p.exists():
            return {'status':'NO_NEW_VERSION_POSITIONS','positions':0,'alerts':0,'items':[]}
        try:
            rows = json.loads(p.read_text(encoding='utf-8'))
            if not isinstance(rows,list):
                raise ValueError('positions')
        except (OSError,ValueError):
            return {'status':'POSITION_FILE_ERROR','positions':0,'alerts':0,'items':[]}
        count, unknown, items = 0, 0, []
        for row in rows:
            if not isinstance(row,dict):
                unknown += 1
                continue
            code = str(row.get('ts_code',''))
            q = quotes.get(code,{})
            from fusion_engine import quote_issues
            if quote_issues(q,now,True):
                unknown += 1
                items.append({'ts_code':code,'stage':'WAIT_DATA','price':None,
                              'reason':'实时行情缺失或过期'})
                continue
            price = number(q.get('close'))
            cost = number(row.get('cost_price'))
            stop,target = number(row.get('stop_price')),number(row.get('target_price'))
            pnl_pct = (price/cost-1)*100 if price and cost and cost > 0 else None
            stage = 'STOP_REVIEW' if stop and price <= stop else 'TARGET_REVIEW' if target and price >= target else 'NORMAL'
            items.append({'ts_code':code,'name':row.get('name'),'stage':stage,'price':price,
                          'cost_price':cost,'pnl_pct':pnl_pct,'quantity':row.get('quantity'),
                          'available_qty':row.get('available_qty'),'stop_price':stop,
                          'target_price':target,'position_asof':row.get('position_asof'),
                          'risk_note':'未设置止损/目标，仅跟踪盈亏' if not stop and not target else ''})
            key = 'position:'+code
            if self.get(key) == stage:
                continue
            self.set(key,stage)
            if stage == 'NORMAL':
                continue
            level = stop if stage == 'STOP_REVIEW' else target
            available = row.get('available_qty') if str(row.get('position_asof',''))[:10] == now.date().isoformat() else None
            content = ('新版持仓人工复核\n'+code+' '+stage+'\n现价：'+str(price)+
                '\n本次触发价格线：'+str(level)+'\n源行情：'+str(q.get('trade_time'))+
                '\n今日可卖数量：'+(str(available) if available is not None else '未核验，请查券商')+
                '\n核对实际持仓、交易限制与原计划；不是自动卖出指令，不保证价格线可成交。')
            self.emit(key+':'+now.isoformat(),stage,content,now,enabled)
            count += 1
        return {'status':'READY' if not unknown else 'PARTIAL_DATA','positions':len(rows),
                'alerts':count,'unknown':unknown,'items':items}

    def run(self, now, worker_status, candidates, calendar, enabled=False, quotes=None):
        day = now.date().isoformat()
        _,info = channel()
        if self.get('legacy_day') != day:
            self.set('legacy_status',json.dumps(self.import_legacy(now)))
            self.set('legacy_day',day)
        previous = self.get('health')
        health = 'OK' if worker_status == 'RESEARCH_ONLY' else worker_status
        if previous != health:
            self.set('health',health)
            if health not in ('MARKET_CLOSED',) and previous is not None:
                self.emit('health:'+now.isoformat(),'HEALTH',
                    '新版数据状态变化\n'+str(previous)+' → '+health+'\n行情异常期间停止依据软件新增决策；恢复不等于取得买入资格。',now,enabled)
        minute = now.hour*60+now.minute
        if calendar is True and (540 <= minute < 565 or 930 <= minute < 990):
            kind = 'PREOPEN' if minute < 565 else 'CLOSE_REVIEW'
            text = ('新版'+('盘前计划' if kind == 'PREOPEN' else '收盘复盘')+'\n日期：'+day+
                    '\n当前数据状态：'+worker_status+'\n执行资格：未开放'+
                    '\n先核对持仓与数据，再复核潜伏、回踩、启动观察。没有有效条件不补票。'+
                    '\n全部消息与结算见工作台；发现收益不是成交收益或样本外胜率。')
            if kind == 'CLOSE_REVIEW':
                from fusion_market_review import build_brief
                text,summary=build_brief(kind,now,self.root,worker_status)
                self.set('close_review_status',json.dumps(summary))
            self.emit(day+':'+kind,kind,text,now,enabled)
        portfolio = self.portfolio(now,quotes or {},enabled if worker_status == 'RESEARCH_ONLY' else False)
        if worker_status != 'RESEARCH_ONLY':
            portfolio['monitoring']='PAUSED_NON_TRADING'
            portfolio['status']='PAUSED'
        if calendar is True:
            from fusion_market_review import build_brief
            for label,target in [('09:35',575),('10:00',600),('13:30',810),('14:30',870)]:
                if target <= minute < target+3:
                    identity=day+':MARKET:'+label
                    if not self.db.execute('SELECT 1 FROM notice WHERE id=?',(identity,)).fetchone():
                        content,summary=build_brief('MARKET',now,self.root,worker_status)
                        self.emit(identity,'MARKET_DIGEST',content,now,enabled)
                        self.set('market_digest_status',json.dumps(summary))
        recent = [dict(r) for r in self.db.execute('SELECT kind,created_at,status FROM notice ORDER BY created_at DESC LIMIT 10')]
        return {'channel':info,'portfolio':portfolio,'legacy':json.loads(self.get('legacy_status') or '{}'),
                'market_digest':json.loads(self.get('market_digest_status') or '{}'),
                'close_review':json.loads(self.get('close_review_status') or '{}'),
                'notices':recent,'schedule':'09:00-09:25 / 15:30-16:30, trading days only',
                'execution_eligible':False}

    def close(self):
        self.db.close()
