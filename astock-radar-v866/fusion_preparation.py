"""Resumable full-cross-section history preparation, new-version storage only."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

from fusion_engine import historical_structure, number


class Preparation:
    def __init__(self, directory):
        self.db = sqlite3.connect(str(Path(directory)/'fusion_preparation.sqlite3'))
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS bars(code TEXT,day TEXT,payload TEXT,PRIMARY KEY(code,day));
        CREATE TABLE IF NOT EXISTS batches(day TEXT PRIMARY KEY,rows INTEGER,observed_at TEXT);
        ''')
        self.days = []
        self.target = None
        self.status = {'status':'NOT_PREPARED','policy':'BACKFILLED_CONTEXT_NOT_PIT'}
        self.last_attempt = None

    def step(self, now, previous_day, fetch):
        if not previous_day:
            return dict(self.status,status='CALENDAR_UNKNOWN')
        if self.last_attempt and (now-self.last_attempt).total_seconds() < 60:
            return self.status
        self.last_attempt = now
        if self.target != previous_day or not self.days:
            rows,status = fetch('trade_cal',dict(exchange='SSE',start_date=(now-dt.timedelta(days=150)).strftime('%Y%m%d'),end_date=previous_day))
            if status != 'OK':
                self.status = dict(self.status,status='CALENDAR_UNAVAILABLE')
                return self.status
            self.days = sorted({str(x.get('cal_date')) for x in rows if str(x.get('is_open')) in ('1','1.0')})[-65:]
            if len(self.days) < 60 or self.days[-1] != previous_day:
                self.days = []
                self.status = dict(self.status,status='CALENDAR_INCOMPLETE')
                return self.status
            self.target = previous_day
        done = {x[0] for x in self.db.execute('SELECT day FROM batches')}
        missing = [day for day in reversed(self.days) if day not in done]
        if missing:
            day = missing[0]
            bars,bs = fetch('daily',dict(trade_date=day))
            factors,fs = fetch('adj_factor',dict(trade_date=day))
            fm = {str(x.get('ts_code')):number(x.get('adj_factor')) for x in factors if str(x.get('trade_date')) == day}
            merged = [dict(x,adj_factor=fm.get(str(x.get('ts_code')))) for x in bars if str(x.get('trade_date')) == day and fm.get(str(x.get('ts_code')))]
            # A partial slice cannot count as a prepared market cross-section.
            if bs != 'OK' or fs != 'OK' or len(merged) < 3000 or len(merged) < len(bars)*.98:
                self.status = dict(self.status,status='BATCH_INCOMPLETE',batch_day=day,rows=len(merged))
                return self.status
            with self.db:
                for row in merged:
                    self.db.execute('INSERT OR IGNORE INTO bars VALUES (?,?,?)',(row['ts_code'],day,json.dumps(row,default=str)))
                self.db.execute('INSERT INTO batches VALUES (?,?,?)',(day,len(merged),now.isoformat()))
            done.add(day)
        ready = sum(day in done for day in self.days)
        self.status = dict(status='READY' if ready == len(self.days) else 'PREPARING',
                           target=self.target,prepared_days=ready,required_days=len(self.days),
                           policy='BACKFILLED_CONTEXT_NOT_PIT',coverage='PROVIDER_RETURNED_UNIVERSE_NOT_CERTIFIED_FULL_MARKET')
        return self.status

    def bars(self, code, previous_day):
        return [json.loads(row[0]) for row in reversed(list(self.db.execute(
            'SELECT payload FROM bars WHERE code=? AND day<=? ORDER BY day DESC LIMIT 65',(code,previous_day))))]

    def shortlist(self, now, previous_day, limit=200):
        if not previous_day:
            return []
        # Structural candidates only. Fresh quotes and full rules are still required intraday.
        found = []
        codes = [x[0] for x in self.db.execute('SELECT code FROM bars WHERE day=?',(previous_day,))]
        for code in codes:
            bars = self.bars(code,previous_day)
            if len(bars) < 60:
                continue
            last = number(bars[-1].get('close'))
            if not last:
                continue
            s = historical_structure(bars,dict(close=last,pre_close=last),now)
            if (s.get('status') == 'OK' and s.get('position60') is not None
                    and 0 <= s['position60'] <= .60 and s['range20_pct'] <= 18
                    and s['ma20_slope_pct'] >= 0 and s['higher_lows']):
                found.append((s['volume_contraction'],code))
        return [code for _,code in sorted(found)[:limit]]

    def close(self):
        self.db.close()
