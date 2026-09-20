"""Append-only origins and observations, with a separate durable notification outbox."""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from fusion_engine import TZ, VERSION

SCHEMA = '''
CREATE TABLE IF NOT EXISTS origin(signal_id TEXT PRIMARY KEY,trade_date TEXT NOT NULL,code TEXT NOT NULL,
 family TEXT NOT NULL,version TEXT NOT NULL,first_seen_at TEXT NOT NULL,source_trade_time TEXT NOT NULL,
 first_seen_price REAL NOT NULL,evidence_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observation(signal_id TEXT NOT NULL,snapshot_id TEXT NOT NULL,state TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,price REAL,payload_json TEXT NOT NULL,PRIMARY KEY(signal_id,snapshot_id));
CREATE TABLE IF NOT EXISTS current_state(signal_id TEXT PRIMARY KEY,state TEXT NOT NULL,generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(message_id TEXT PRIMARY KEY,signal_id TEXT NOT NULL,state TEXT NOT NULL,
 payload_json TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,next_attempt_at TEXT NOT NULL,last_error TEXT,sent_at TEXT);
CREATE TRIGGER IF NOT EXISTS origin_no_update BEFORE UPDATE ON origin BEGIN SELECT RAISE(ABORT,'immutable origin'); END;
CREATE TRIGGER IF NOT EXISTS origin_no_delete BEFORE DELETE ON origin BEGIN SELECT RAISE(ABORT,'immutable origin'); END;
CREATE TRIGGER IF NOT EXISTS observation_no_update BEFORE UPDATE ON observation BEGIN SELECT RAISE(ABORT,'immutable observation'); END;
CREATE TRIGGER IF NOT EXISTS observation_no_delete BEFORE DELETE ON observation BEGIN SELECT RAISE(ABORT,'immutable observation'); END;
'''


class Journal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(SCHEMA)

    def record(self, result, snapshot_id, now, push_enabled=False):
        code, family = result['ts_code'], result.get('family')
        today = now.date().isoformat()
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self.db:
            existing = list(self.db.execute('SELECT signal_id,family FROM origin WHERE code=? AND trade_date=? AND version=?', (code, today, VERSION)))
            if result['state'] == 'WATCH' and family:
                signal_id = hashlib.sha256(f'{today}|{code}|{family}|{VERSION}'.encode()).hexdigest()[:24]
                self.db.execute('INSERT OR IGNORE INTO origin VALUES(?,?,?,?,?,?,?,?,?)',
                                (signal_id,today,code,family,VERSION,now.isoformat(),result['source_trade_time'],result['price'],raw))
                if signal_id not in {r['signal_id'] for r in existing}:
                    existing.append({'signal_id': signal_id, 'family': family})
            for item in existing:
                signal_id = item['signal_id']
                state = result['state']
                if state == 'WATCH' and item['family'] != family:
                    state = 'EXPIRED'
                elif state == 'NO_SETUP':
                    state = 'EXPIRED'
                cursor = self.db.execute('INSERT OR IGNORE INTO observation VALUES(?,?,?,?,?,?)',
                                         (signal_id,snapshot_id,state,now.isoformat(),result.get('price'),raw))
                if cursor.rowcount == 0:
                    continue
                previous = self.db.execute('SELECT state,generation FROM current_state WHERE signal_id=?',(signal_id,)).fetchone()
                if previous and previous['state'] == state:
                    continue
                generation = previous['generation']+1 if previous else 1
                self.db.execute('INSERT INTO current_state VALUES(?,?,?) ON CONFLICT(signal_id) DO UPDATE SET state=excluded.state,generation=excluded.generation',
                                (signal_id,state,generation))
                # A superseded opportunity must not be sent after its withdrawal.
                self.db.execute("UPDATE outbox SET status='EXPIRED' WHERE signal_id=? AND status IN ('HELD','PENDING','FAILED')", (signal_id,))
                message_id = f'{signal_id}:{generation}'
                expires = now+dt.timedelta(minutes=3 if state=='WATCH' else 30)
                visible = self.db.execute("SELECT 1 FROM outbox WHERE signal_id=? AND status='SENT' LIMIT 1",(signal_id,)).fetchone()
                enabled = push_enabled and (state=='WATCH' or bool(visible))
                payload = {**result,'signal_id':signal_id,'notification_state':state,'family':item['family']}
                self.db.execute('INSERT OR IGNORE INTO outbox(message_id,signal_id,state,payload_json,status,created_at,expires_at,next_attempt_at) VALUES(?,?,?,?,?,?,?,?)',
                                (message_id,signal_id,state,json.dumps(payload,ensure_ascii=False,allow_nan=False),
                                 'PENDING' if enabled else 'HELD',now.isoformat(),expires.isoformat(),now.isoformat()))

    def recover(self):
        # An interrupted HTTP request may have reached WeCom. Do not blindly resend.
        with self.db:
            self.db.execute("UPDATE outbox SET status='UNCERTAIN',last_error='interrupted delivery requires review' WHERE status='SENDING'")

    def deliver_one(self, now, sender):
        stamp = now.isoformat()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE outbox SET status='EXPIRED' WHERE status IN ('PENDING','FAILED','HELD') AND expires_at<=?",(stamp,))
            row = self.db.execute("SELECT * FROM outbox WHERE status IN ('PENDING','FAILED') AND next_attempt_at<=? AND expires_at>? AND attempts<3 ORDER BY CASE WHEN state='WATCH' THEN 1 ELSE 0 END,created_at LIMIT 1",(stamp,stamp)).fetchone()
            if not row:
                self.db.commit(); return {'status':'EMPTY'}
            self.db.execute("UPDATE outbox SET status='SENDING',attempts=attempts+1 WHERE message_id=?",(row['message_id'],))
            self.db.commit()
        except Exception:
            self.db.rollback(); raise
        try:
            outcome = sender(json.loads(row['payload_json']))
        except Exception:
            outcome = {'status':'UNCERTAIN','error':'transport outcome unknown'}
        status = outcome.get('status')
        if status not in ('SENT','FAILED','UNCERTAIN'):
            status = 'UNCERTAIN'
        with self.db:
            self.db.execute('UPDATE outbox SET status=?,last_error=?,sent_at=?,next_attempt_at=? WHERE message_id=?',
                            (status,str(outcome.get('error',''))[:200],stamp if status=='SENT' else None,
                             (now+dt.timedelta(seconds=30*2**row['attempts'])).isoformat(),row['message_id']))
        return {'message_id':row['message_id'],'status':status}

    def counts(self):
        return {r[0]:r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM outbox GROUP BY status')}

    def close(self):
        self.db.close()
