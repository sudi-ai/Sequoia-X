"""Regression coverage for explicitly approved release boundaries."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest

from fusion_journal_v2 import ResearchJournal
from fusion_outcomes import Settlement
from test_fusion_v2 import NOW, signal


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = ResearchJournal(Path(self.tmp.name)/'journal.sqlite3')

    def tearDown(self):
        self.j.close()
        self.tmp.cleanup()

    def test_expired_held_requires_new_observation_and_sends_once(self):
        self.j.record(signal(), 'original', NOW, False)
        later = NOW+dt.timedelta(minutes=5)
        self.j.expire_pending(later)
        self.j.record(signal(), 'original', later, True)
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM outbox WHERE status='PENDING'").fetchone()[0], 0)
        self.j.record(signal(later, 10.1), 'fresh', later, True)
        self.j.record(signal(later, 10.1), 'fresh', later, True)
        rows = self.j.db.execute("SELECT payload_json FROM outbox WHERE status='PENDING'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn('10.1', rows[0]['payload_json'])

    def settle_calendar(self, calendar):
        self.j.record(signal(), 'origin', NOW)
        calls = []
        def fetch(name, params):
            calls.append(name)
            if name == 'trade_cal':
                return calendar, 'OK'
            return [], 'EMPTY'
        service = Settlement(self.j, fetch)
        result = service.run(NOW+dt.timedelta(days=7, hours=6))
        self.assertEqual(result['settled'], 0)
        self.assertEqual(calls, ['trade_cal'])
        status = self.j.db.execute('SELECT status FROM research_settlement_attempt').fetchone()[0]
        self.assertEqual(status, 'CALENDAR_UNAVAILABLE')

    def calendar(self):
        return [dict(cal_date=(NOW+dt.timedelta(days=i)).strftime('%Y%m%d'),
                     is_open=int((NOW+dt.timedelta(days=i)).weekday()<5)) for i in range(8)]

    def test_missing_trading_day_blocks_settlement(self):
        self.settle_calendar([r for r in self.calendar() if r['cal_date'] != '20260908'])

    def test_missing_closed_day_blocks_settlement(self):
        self.settle_calendar([r for r in self.calendar() if r['cal_date'] != '20260905'])

    def test_conflicting_calendar_blocks_settlement(self):
        self.settle_calendar(self.calendar()+[dict(cal_date='20260908', is_open=0)])

    def test_invalid_open_flag_blocks_settlement(self):
        rows = self.calendar()
        rows[2]['is_open'] = None
        self.settle_calendar(rows)


if __name__ == '__main__':
    unittest.main()
