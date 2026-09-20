"""Offline self-checks: synthetic fixtures, no real providers, pushes or orders."""
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fusion_engine import TZ, ordered_pullback, quote_issues
from fusion_journal import Journal


class FusionChecks(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,9,4,10,0,tzinfo=TZ)

    def points(self, prices):
        return [{'source_trade_time':(self.now+dt.timedelta(minutes=i)).isoformat(),'price':p} for i,p in enumerate(prices)]

    def test_monotone_rise_is_not_pullback(self):
        self.assertFalse(ordered_pullback(self.points([10,10.1,10.2,10.3]),10)['confirmed'])

    def test_ordered_pullback(self):
        self.assertTrue(ordered_pullback(self.points([10,10.2,10.1,10.21]),10)['confirmed'])

    def test_time_only_is_not_fresh(self):
        q={'source_trade_time':'10:00:00','observed_at':self.now.isoformat(),'source':'tushare.rt_k',
           'data_quality':1,'close':10,'pre_close':10}
        self.assertTrue(quote_issues(q,self.now,True))

    def test_stale_quote(self):
        q={'source_trade_time':(self.now-dt.timedelta(minutes=4)).isoformat(),'observed_at':self.now.isoformat(),
           'source':'tushare.rt_k','data_quality':1,'close':10,'pre_close':10}
        self.assertTrue(quote_issues(q,self.now,True))

    def result(self,state='WATCH',price=10):
        return {'ts_code':'000001.SZ','family':'LATENT_BASE','state':state,'price':price,
                'source_trade_time':self.now.isoformat(),'execution_eligible':False}

    def test_origin_immutable_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            j=Journal(Path(folder)/'test.db')
            try:
                j.record(self.result(), 's1', self.now)
                j.record(self.result(price=11), 's2', self.now+dt.timedelta(minutes=1))
                j.record(self.result(price=11), 's2', self.now+dt.timedelta(minutes=1))
                self.assertEqual(j.db.execute('SELECT first_seen_price FROM origin').fetchone()[0],10)
                self.assertEqual(j.db.execute('SELECT COUNT(*) FROM observation').fetchone()[0],2)
                self.assertEqual(j.counts(),{'HELD':1})
                with self.assertRaises(sqlite3.IntegrityError):
                    j.db.execute('UPDATE origin SET first_seen_price=99')
            finally:j.close()

    def test_failed_delivery_retries_without_state_change(self):
        with tempfile.TemporaryDirectory() as folder:
            j=Journal(Path(folder)/'test.db')
            try:
                j.record(self.result(),'s1',self.now,push_enabled=True)
                self.assertEqual(j.deliver_one(self.now,lambda _: {'status':'FAILED'})['status'],'FAILED')
                self.assertEqual(j.deliver_one(self.now+dt.timedelta(seconds=31),lambda _: {'status':'SENT'})['status'],'SENT')
            finally:j.close()

    def test_expired_watch_is_not_sent(self):
        with tempfile.TemporaryDirectory() as folder:
            j=Journal(Path(folder)/'test.db')
            try:
                j.record(self.result(),'s1',self.now,push_enabled=True)
                called=[]
                self.assertEqual(j.deliver_one(self.now+dt.timedelta(minutes=4),lambda x: called.append(x))['status'],'EMPTY')
                self.assertFalse(called)
            finally:j.close()

    def test_uncertain_delivery_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            j=Journal(Path(folder)/'test.db')
            try:
                j.record(self.result(),'s1',self.now,push_enabled=True)
                j.deliver_one(self.now,lambda _: {'status':'UNCERTAIN'})
                self.assertEqual(j.deliver_one(self.now+dt.timedelta(minutes=1),lambda _: {'status':'SENT'})['status'],'EMPTY')
            finally:j.close()


if __name__=='__main__':
    unittest.main()
