"""Fixed-horizon, adjusted research markouts, not executable trade P&L."""
import json
from datetime import datetime, timedelta

from fusion_engine import number
from fusion_journal_v2 import VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_outcome (
 signal_id TEXT NOT NULL, horizon INTEGER NOT NULL, target_date TEXT NOT NULL,
 settled_at TEXT NOT NULL, gross_pct REAL NOT NULL, assumed_net_pct REAL NOT NULL,
 evidence_json TEXT NOT NULL, PRIMARY KEY(signal_id,horizon));
CREATE TRIGGER IF NOT EXISTS research_outcome_no_update BEFORE UPDATE ON research_outcome
 BEGIN SELECT RAISE(ABORT,'immutable research outcome'); END;
CREATE TRIGGER IF NOT EXISTS research_outcome_no_delete BEFORE DELETE ON research_outcome
 BEGIN SELECT RAISE(ABORT,'immutable research outcome'); END;
CREATE TABLE IF NOT EXISTS research_settlement_attempt (
 signal_id TEXT PRIMARY KEY, checked_at TEXT NOT NULL, status TEXT NOT NULL);
"""


def calculate(origin, horizon, target, bars, factors, now, cost_bps=20):
    prices = {str(r.get('trade_date', '')): r for r in bars}
    adjustments = {str(r.get('trade_date', '')): number(r.get('adj_factor')) for r in factors}
    first = number(origin['first_seen_price'])
    close = number(prices.get(target, {}).get('close'))
    base = adjustments.get(origin['trade_date'])
    end = adjustments.get(target)
    if any(v is None or v <= 0 for v in (first, close, base, end)):
        return None
    gross = (close * end / (first * base) - 1) * 100
    return dict(signal_id=origin['signal_id'], horizon=horizon, target_date=target,
                settled_at=now.isoformat(), gross_pct=gross,
                assumed_net_pct=gross - cost_bps / 100,
                evidence_json=json.dumps(dict(first_seen_price=first, close=close,
                    origin_factor=base, target_factor=end, assumed_round_trip_cost_bps=cost_bps,
                    measure='DISCOVERY_TO_FIXED_CLOSE_NOT_TRADE_PNL',
                    source='paid_data_hub:daily+adj_factor', observed_at=now.isoformat()), allow_nan=False))


class Settlement:
    def __init__(self, journal, fetch):
        self.journal, self.fetch = journal, fetch
        journal.db.executescript(SCHEMA)

    def run(self, now, max_origins=2):
        if (now.hour, now.minute) < (15, 10):
            return {'status': 'WAIT_CLOSE', 'settled': 0}
        db = self.journal.db
        origins = [dict(r) for r in db.execute(
            "SELECT o.* FROM origin o LEFT JOIN research_settlement_attempt a USING(signal_id) "
            "WHERE version=? AND (SELECT count(*) FROM research_outcome r WHERE r.signal_id=o.signal_id)<3 "
            "AND (a.checked_at IS NULL OR a.checked_at<?) ORDER BY coalesce(a.checked_at,''),o.first_seen_at LIMIT ?",
            (VERSION, (now-timedelta(minutes=30)).isoformat(), max_origins))]
        settled = 0
        for origin in origins:
            cal, status = self.fetch('trade_cal', dict(exchange='SSE', start_date=origin['trade_date'], end_date=now.strftime('%Y%m%d')))
            start = datetime.strptime(origin['trade_date'], '%Y%m%d').date()
            expected = {(start + timedelta(days=i)).strftime('%Y%m%d')
                        for i in range((now.date() - start).days + 1)}
            calendar = {}
            conflicts = False
            for row in cal:
                date = str(row.get('cal_date', ''))
                opened = str(row.get('is_open'))
                if date not in expected:
                    continue
                if opened not in ('0', '0.0', '1', '1.0'):
                    conflicts = True
                    continue
                flag = opened in ('1', '1.0')
                if date in calendar and calendar[date] != flag:
                    conflicts = True
                calendar[date] = flag
            days = sorted(date for date, opened in calendar.items() if opened)
            valid = (status == 'OK' and not conflicts and set(calendar) == expected
                     and origin['trade_date'] in days)
            targets = [(h, days[days.index(origin['trade_date'])+h]) for h in (1,3,5)
                       if valid and days.index(origin['trade_date'])+h < len(days)]
            existing = {r[0] for r in db.execute('SELECT horizon FROM research_outcome WHERE signal_id=?', (origin['signal_id'],))}
            targets = [(h,d) for h,d in targets if h not in existing]
            result_status = 'WAIT_HORIZON' if valid else 'CALENDAR_UNAVAILABLE'
            if targets:
                params = dict(ts_code=origin['code'], start_date=origin['trade_date'], end_date=max(d for _,d in targets))
                bars, bs = self.fetch('daily', params)
                factors, fs = self.fetch('adj_factor', params)
                result_status = 'DATA_MISSING'
                if bs == fs == 'OK':
                    with db:
                        for horizon, target in targets:
                            outcome = calculate(origin, horizon, target, bars, factors, now)
                            if outcome is not None:
                                inserted = db.execute('INSERT OR IGNORE INTO research_outcome VALUES (:signal_id,:horizon,:target_date,:settled_at,:gross_pct,:assumed_net_pct,:evidence_json)', outcome)
                                settled += inserted.rowcount
                        total = db.execute('SELECT count(*) FROM research_outcome WHERE signal_id=?', (origin['signal_id'],)).fetchone()[0]
                        result_status = 'COMPLETE' if total == 3 else 'PARTIAL_OR_WAITING'
            with db:
                db.execute('INSERT INTO research_settlement_attempt VALUES (?,?,?) ON CONFLICT(signal_id) DO UPDATE SET checked_at=excluded.checked_at,status=excluded.status',
                           (origin['signal_id'], now.isoformat(), result_status))
        return {'status': 'CHECKED', 'checked': len(origins), 'settled': settled}

    def summary(self):
        rows = self.journal.db.execute(
            "SELECT r.horizon,count(*) samples,count(distinct o.code) stocks,"
            "sum(CASE WHEN assumed_net_pct>0 THEN 1 ELSE 0 END) positive,avg(assumed_net_pct) mean_net_pct "
            "FROM research_outcome r JOIN origin o USING(signal_id) WHERE o.version=? GROUP BY r.horizon", (VERSION,))
        return [dict(r, positive_pct=100*r['positive']/r['samples']) for r in rows]
