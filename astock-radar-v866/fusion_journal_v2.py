"""Append-only research evidence and explicit notification lifecycle."""
import hashlib
import json
from datetime import timedelta

from fusion_journal import Journal

VERSION = "FUSION_SHADOW_2"
ACTIVE = ("WATCH", "WAIT_DATA", "PAUSED")


class ResearchJournal(Journal):
    def active(self):
        return [dict(r) for r in self.db.execute(
            "SELECT o.*, c.state FROM origin o JOIN current_state c USING(signal_id) "
            "WHERE o.version=? AND c.state IN ('WATCH','WAIT_DATA','PAUSED')",
            (VERSION,))]

    def expire_pending(self, now):
        with self.db:
            self.db.execute("UPDATE outbox SET status='EXPIRED' WHERE "
                            "status IN ('HELD','PENDING','FAILED') AND expires_at<=?",
                            (now.isoformat(),))

    def _transition(self, origin, result, snapshot_id, now, push_enabled):
        sid = origin['signal_id']
        state = result['state']
        payload = dict(result, signal_id=sid, family=origin['family'],
                       first_seen_at=origin['first_seen_at'],
                       first_seen_price=origin['first_seen_price'],
                       notification_state=state)
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO observation VALUES (?,?,?,?,?,?)",
            (sid, snapshot_id, state, now.isoformat(), result.get('price'), raw))
        if not inserted.rowcount:
            return
        prev = self.db.execute("SELECT state,generation FROM current_state WHERE signal_id=?", (sid,)).fetchone()
        latest = self.db.execute("SELECT status FROM outbox WHERE signal_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (sid,)).fetchone()
        activation = push_enabled and state == 'WATCH' and latest and latest['status'] in ('HELD', 'EXPIRED')
        if prev and prev['state'] == state and not activation:
            return
        generation = prev['generation'] + 1 if prev else 1
        self.db.execute("INSERT INTO current_state VALUES (?,?,?) ON CONFLICT(signal_id) "
                        "DO UPDATE SET state=excluded.state,generation=excluded.generation", (sid, state, generation))
        self.db.execute("UPDATE outbox SET status='EXPIRED' WHERE signal_id=? AND status IN ('HELD','PENDING','FAILED')", (sid,))
        visible = self.db.execute("SELECT 1 FROM outbox WHERE signal_id=? AND status IN ('SENT','UNCERTAIN') LIMIT 1", (sid,)).fetchone()
        enabled = push_enabled and (state == 'WATCH' or (visible and state not in ('PAUSED',)))
        expires = now + timedelta(minutes=3 if state == 'WATCH' else 30)
        self.db.execute(
            "INSERT INTO outbox (message_id,signal_id,state,payload_json,status,attempts,created_at,expires_at,next_attempt_at,last_error,sent_at) "
            "VALUES (?,?,?,?,?,0,?,?,?,'',NULL)",
            (f'{sid}:{generation}', sid, state, raw, 'PENDING' if enabled else 'HELD',
             now.isoformat(), expires.isoformat(), now.isoformat()))

    def record(self, result, snapshot_id, now, push_enabled=False):
        result = dict(result, version=VERSION)
        code, family = result.get('ts_code', ''), result.get('family', '')
        if not code:
            return
        with self.db:
            origins = [r for r in self.active() if r['code'] == code]
            if result['state'] == 'WATCH' and family and not any(r['family'] == family for r in origins):
                sid = hashlib.sha256(f'{now.date()}|{code}|{family}|{VERSION}'.encode()).hexdigest()[:24]
                raw = json.dumps(result, ensure_ascii=False, allow_nan=False, default=str)
                self.db.execute("INSERT OR IGNORE INTO origin VALUES (?,?,?,?,?,?,?,?,?)", (
                    sid, now.strftime('%Y%m%d'), code, family, VERSION,
                    now.isoformat(), result.get('source_trade_time'), result.get('price'), raw))
                origins.append(dict(self.db.execute("SELECT * FROM origin WHERE signal_id=?", (sid,)).fetchone()))
            for origin in origins:
                state = result['state']
                if state == 'NO_SETUP' or (state == 'WATCH' and family != origin['family']):
                    state = 'EXPIRED'
                self._transition(origin, dict(result, state=state), snapshot_id, now, push_enabled)

    def unavailable(self, now, reason, push_enabled=False, codes=None, paused=False):
        with self.db:
            for origin in self.active():
                if codes is not None and origin['code'] not in codes:
                    continue
                result = dict(ts_code=origin['code'], family=origin['family'],
                              version=VERSION, state='PAUSED' if paused else 'WAIT_DATA',
                              price=None, evaluated_at=now.isoformat(), issues=[reason],
                              execution_eligible=False, data_mode='LIVE_RESEARCH')
                self._transition(origin, result, f'unavailable:{now.isoformat()}', now, push_enabled)
        self.expire_pending(now)

    def summary(self):
        rows = self.db.execute("SELECT c.state,count(*) n FROM current_state c JOIN origin o USING(signal_id) WHERE o.version=? GROUP BY c.state", (VERSION,))
        return {r['state']: r['n'] for r in rows}
