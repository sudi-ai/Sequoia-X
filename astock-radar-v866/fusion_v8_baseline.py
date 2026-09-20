"""Read-only ingestion of the running V8 discovery baseline.

Never imports/runs legacy code, writes its DB, or upgrades legacy scores to probabilities.
The original discovery is immutable; current state and new evidence are separate.
"""
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from fusion_engine import timestamp, number


class V8Baseline:
    def __init__(self, directory, operations):
        self.root=Path(directory);self.operations=operations
        self.db=sqlite3.connect(str(self.root/'fusion_v8_baseline.sqlite3'))
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS original(id TEXT PRIMARY KEY,source_path TEXT,event_key TEXT,
          first_seen_at TEXT,first_seen_price REAL,first_seen_pct REAL,imported_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS evaluation(id TEXT,observed_at TEXT,source_updated_at TEXT,
          source_state TEXT,source_payload TEXT,new_evidence TEXT,PRIMARY KEY(id,observed_at));
        CREATE TABLE IF NOT EXISTS current(id TEXT PRIMARY KEY,state TEXT,notice_state TEXT);
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
        CREATE TRIGGER IF NOT EXISTS original_no_update BEFORE UPDATE ON original BEGIN SELECT RAISE(ABORT,'immutable original'); END;
        CREATE TRIGGER IF NOT EXISTS original_no_delete BEFORE DELETE ON original BEGIN SELECT RAISE(ABORT,'immutable original'); END;
        ''')
        self.status={'status':'NOT_CONFIGURED','execution_eligible':False}

    def sync(self, now, enabled=False, current_quotes=None):
        config=self.root/'fusion_v8_source.json'
        if not config.exists():return self.status
        try:
            cfg=json.loads(config.read_text(encoding='utf-8'));source=Path(cfg['database'])
            if not source.is_absolute() or not source.is_file():raise ValueError('source')
            minimum=(now-dt.timedelta(days=30)).date().isoformat()
            # Read-only and bounded. No legacy API/client/module execution.
            with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as old:
                old.row_factory=sqlite3.Row
                rows=[dict(x) for x in old.execute('SELECT * FROM v8_discovery_candidates WHERE trade_date>=? ORDER BY first_seen_at DESC LIMIT 10000',(minimum,))]
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
            self.status={'status':'SOURCE_UNAVAILABLE','execution_eligible':False};return self.status
        imported=0;fresh_count=0;changes=0
        for row in rows:
            event=str(row.get('event_key') or '')
            first=timestamp(row.get('first_seen_at'))
            price=number(row.get('first_seen_price'))
            if not event or first is None or price is None or price<=0:continue
            identity=hashlib.sha256((str(source.resolve())+'|'+event).encode()).hexdigest()[:32]
            raw=json.dumps(row,ensure_ascii=False,default=str)
            with self.db:
                imported+=self.db.execute('INSERT OR IGNORE INTO original VALUES (?,?,?,?,?,?,?,?)',
                    (identity,str(source.resolve()),event,row['first_seen_at'],price,number(row.get('first_seen_pct_chg')),now.isoformat(),raw)).rowcount
            original=self.db.execute('SELECT * FROM original WHERE id=?',(identity,)).fetchone()
            original_changed=(original['first_seen_at']!=row['first_seen_at'] or original['first_seen_price']!=price)
            if original_changed:changes+=1
            last=timestamp(row.get('last_seen_at'));state=str(row.get('action') or row.get('last_push_state') or 'UNKNOWN')
            fresh=bool(last and last.date()==now.date() and -30<=(now-last).total_seconds()<=180)
            q=(current_quotes or {}).get(str(row.get('code')), {})
            if not q and len(str(row.get('code','')))==6:
                q=next((v for k,v in (current_quotes or {}).items() if k.split('.')[0]==str(row['code'])),{})
            evidence={'original_changed':original_changed,'source_quote_time':row.get('source_time'),
                'source_time_verified':False,'new_quote_observed_at':q.get('observed_at'),
                'new_quote_price':number(q.get('close')),'execution_eligible':False,
                'enhancement_role':'PARALLEL_EVIDENCE_NOT_INITIAL_ALERT_GATE'}
            previous=self.db.execute('SELECT state,notice_state FROM current WHERE id=?',(identity,)).fetchone()
            old_state=previous['state'] if previous else None
            with self.db:
                if original_changed or old_state != state:
                    self.db.execute('INSERT OR IGNORE INTO evaluation VALUES (?,?,?,?,?,?)',
                        (identity,now.isoformat(),row.get('last_seen_at'),state,raw,json.dumps(evidence,ensure_ascii=False)))
                self.db.execute('INSERT INTO current VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state',
                    (identity,state,previous['notice_state'] if previous else ''))
            if not fresh or original_changed:continue
            fresh_count+=1
            age=(now-first).total_seconds()
            first_notice=not previous or not previous['notice_state']
            kind=None
            if first_notice and 0<=age<=180 and state in ('WATCH','CONFIRMED'):
                kind='EARLY_DISCOVERY'
            elif not first_notice and state!=old_state:
                kind='BASELINE_STATE_CHANGE'
            if not kind:continue
            lines=['🟡 新版｜V8原始早发现' if kind=='EARLY_DISCOVERY' else '🧭 新版｜V8观察状态变化',
                '📌 '+str(row.get('name') or row.get('code'))+' '+str(row.get('code')),
                '原发现：'+original['first_seen_at']+'｜'+format(original['first_seen_price'],'.2f')+'元',
                '发现时涨幅：'+(format(original['first_seen_pct'],'.2f')+'%' if original['first_seen_pct'] is not None else '未记录'),
                '原通道：'+str(row.get('channel') or '未记录')+'｜原状态：'+state,
                '原记录更新：'+str(row.get('last_seen_at')),
                '首次接入延迟：'+str(round(age,1))+'秒' if kind=='EARLY_DISCOVERY' else '状态变化：'+str(old_state)+' → '+state,
                '【新版处理】保留原早发现，不等待新版评分或回踩确认才提醒。',
                '公告、成交与样本外证据仍未齐备；旧评分不是胜率，源行情时间未独立核验。',
                '👉 仅早期观察。状态否决／失效时停止沿用旧观察；不构成建仓或自动卖出指令。',
                '原记录：'+event]
            result=self.operations.emit('BASELINE:'+identity+':'+kind+':'+str(row.get('last_seen_at')),
                kind,'\n'.join(lines),now,enabled)
            with self.db:self.db.execute('UPDATE current SET notice_state=? WHERE id=?',(result,identity))
        total=self.db.execute('SELECT COUNT(*) FROM original').fetchone()[0]
        self.status=dict(status='READ_ONLY_BASELINE_CONNECTED',source=str(source),
            source_rows=len(rows),retained_origins=total,newly_imported=imported,fresh_rows=fresh_count,
            original_changes_detected=changes,checked_at=now.isoformat(),execution_eligible=False,
            policy='Old discoveries retained; enhancements parallel; historical imports never replayed as fresh alerts',
            limitation='Polling adds latency; source historical research performance not yet independently audited')
        return self.status

    def close(self):self.db.close()
