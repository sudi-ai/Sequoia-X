import datetime as dt
import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from fusion_engine import TZ
from fusion_market_review import market_snapshot,research_review,format_brief

NOW=dt.datetime(2026,9,4,10,tzinfo=TZ)

class ReviewTests(unittest.TestCase):
    def database(self,path,date='20260904',source='paid.rt_k',time='2026-09-04T09:59:00+08:00'):
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE snapshot_manifest(snapshot_id TEXT,trade_date TEXT,source TEXT,observed_at TEXT);CREATE TABLE intraday_quote(snapshot_id TEXT,ts_code TEXT,name TEXT,pct_chg REAL,industry TEXT);')
            db.execute('INSERT INTO snapshot_manifest VALUES (?,?,?,?)',('s',date,source,time))
            db.executemany('INSERT INTO intraday_quote VALUES (?,?,?,?,?)',[('s','600000.SH','fixture',1,'test'),('s','000001.SZ','fixture2',-1,'test'),('s','000002.SZ','fixture3',None,'test')])

    def test_yesterday_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.db';self.database(p,date='20260903')
            self.assertEqual(market_snapshot(NOW,database=p)['status'],'NO_TODAY_SNAPSHOT')

    def test_demo_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.db';self.database(p,source='DEMO')
            self.assertEqual(market_snapshot(NOW,database=p)['status'],'SOURCE_REJECTED')

    def test_stale_intraday_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.db';self.database(p,time='2026-09-04T09:30:00+08:00')
            self.assertEqual(market_snapshot(NOW,database=p)['status'],'STALE_OR_FUTURE')

    def test_valid_denominator(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.db';self.database(p)
            self.assertEqual(market_snapshot(NOW,database=p)['up_pct'],50)

    def test_close_not_certified(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m.db';self.database(p)
            self.assertFalse(market_snapshot(NOW.replace(hour=16),True,p)['close_verified'])

    def test_missing_journal_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(research_review(d,NOW)['status'],'RESEARCH_RECORDS_UNAVAILABLE')

    def test_message_preserves_limits(self):
        text=format_brief('CLOSE_REVIEW',NOW,{'status':'NO_TODAY_SNAPSHOT'},{'status':'RESEARCH_RECORDS_UNAVAILABLE'},'MARKET_CLOSED')
        self.assertIn('不使用昨日数据',text);self.assertIn('非成交收益',text)
        self.assertLessEqual(len(text.encode('utf-8')),1800)
