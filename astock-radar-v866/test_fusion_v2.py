"""Offline fixtures only. Network access must be patched out by the runner."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import requests

from fusion_engine import TZ
from fusion_journal_v2 import ResearchJournal
from fusion_notifications import send_research
from fusion_outcomes import Settlement, calculate
from fusion_runtime_v2 import Runtime, WorkerLock
from fusion_workspace_v2 import render_payload

NOW = dt.datetime(2026,9,4,10,0,tzinfo=TZ)


def signal(now=NOW, price=10):
    return dict(ts_code='000001.SZ', name='Offline fixture', family='LATENT_BASE',
                state='WATCH', price=price, source_trade_time=now.isoformat(),
                observed_at=now.isoformat(), evaluated_at=now.isoformat(),
                issues=['RESEARCH_ONLY'], execution_eligible=False)


class Hub:
    def __init__(self):
        self.now=NOW
        self.calendar_fail=False
        self.quote_fail=False
        self.calls=[]

    def fetch_interface(self, name, params, **kwargs):
        self.calls.append(name)
        now=self.now
        if name == 'trade_cal':
            rows=[] if self.calendar_fail else [dict(cal_date=(now-dt.timedelta(days=i)).strftime('%Y%m%d'),is_open=int((now-dt.timedelta(days=i)).weekday()<5)) for i in range(20)]
        elif name == 'rt_k':
            rows=[] if self.quote_fail else [dict(ts_code='000001.SZ',name='Offline fixture',
                trade_time=now.isoformat(),observed_at=now.isoformat(),source='tushare.rt_k@dailyfetch',
                data_quality=1,close=10.09,pre_close=10,vol=1000000,amount=10000000)]
        elif name in ('daily','adj_factor'):
            dates=[now-dt.timedelta(days=i) for i in range(1,150) if (now-dt.timedelta(days=i)).weekday()<5]
            rows=[dict(trade_date=d.strftime('%Y%m%d'),close=10,high=10.5,low=9.5,vol=1000,adj_factor=1) for d in dates]
        else:
            raise AssertionError('unexpected endpoint '+name)
        frame=pd.DataFrame(rows)
        frame.attrs['status']='OK' if rows else 'EMPTY'
        return frame


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.j=ResearchJournal(Path(self.tmp.name)/'test.sqlite3')

    def tearDown(self):
        self.j.close()
        self.tmp.cleanup()

    def test_cross_day_original_price_and_timestamp_are_preserved(self):
        self.j.record(signal(),'a',NOW)
        tomorrow=NOW+dt.timedelta(days=3)
        self.j.unavailable(NOW+dt.timedelta(hours=6),'closed',paused=True)
        self.j.record(signal(tomorrow,11),'b',tomorrow)
        rows=self.j.db.execute('SELECT * FROM origin').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['first_seen_price'],10)
        self.assertEqual(rows[0]['first_seen_at'],NOW.isoformat())

    def test_disconnect_withdraws_pending_watch(self):
        self.j.record(signal(),'a',NOW,True)
        self.j.unavailable(NOW+dt.timedelta(seconds=60),'offline',True)
        rows=self.j.db.execute('SELECT state,status FROM outbox ORDER BY rowid').fetchall()
        self.assertEqual(rows[0]['status'],'EXPIRED')
        self.assertEqual(self.j.active()[0]['state'],'WAIT_DATA')
        self.assertNotEqual(rows[1]['status'],'PENDING')

    def test_sent_watch_gets_withdrawal(self):
        self.j.record(signal(),'a',NOW,True)
        with self.j.db:
            self.j.db.execute("UPDATE outbox SET status='SENT'")
        self.j.unavailable(NOW+dt.timedelta(seconds=60),'offline',True)
        self.assertEqual(self.j.db.execute('SELECT state,status FROM outbox ORDER BY rowid DESC LIMIT 1').fetchone()['status'],'PENDING')

    def test_observation_and_outcome_are_immutable(self):
        self.j.record(signal(),'a',NOW)
        with self.assertRaises(sqlite3.IntegrityError):
            self.j.db.execute('UPDATE observation SET price=123')
        Settlement(self.j,lambda *a:([],'EMPTY'))
        with self.j.db:
            self.j.db.execute("INSERT INTO research_outcome VALUES ('x',1,'20260907','now',1,.8,'{}')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.j.db.execute('UPDATE research_outcome SET gross_pct=99')

    def test_explicit_enable_uses_fresh_observation_only(self):
        self.j.record(signal(),'a',NOW,False)
        self.j.record(signal(NOW+dt.timedelta(seconds=30)),'b',NOW+dt.timedelta(seconds=30),True)
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM outbox WHERE status='PENDING'").fetchone()[0],1)
        self.j.record(signal(NOW+dt.timedelta(seconds=60)),'c',NOW+dt.timedelta(seconds=60),True)
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM outbox WHERE status='PENDING'").fetchone()[0],1)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.hub=Hub()
        self.runtime=Runtime(self.hub,self.tmp.name,sender=lambda _:self.fail('notification in disabled mode'))

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def test_calendar_recovers_same_day(self):
        self.hub.calendar_fail=True
        self.assertEqual(self.runtime.tick(NOW)['status'],'CALENDAR_UNKNOWN')
        self.hub.calendar_fail=False
        later=NOW+dt.timedelta(seconds=61)
        self.hub.now=later
        self.assertEqual(self.runtime.tick(later)['status'],'RESEARCH_ONLY')

    def test_paid_gateway_to_origin_and_offline_withdrawal(self):
        payload=self.runtime.tick(NOW)
        self.assertFalse(payload['push_enabled'])
        self.assertEqual(payload['candidates'][0]['family'],'LATENT_BASE')
        self.assertFalse(payload['candidates'][0]['execution_eligible'])
        self.assertEqual(len(self.runtime.journal.active()),1)
        self.hub.quote_fail=True
        self.assertEqual(self.runtime.tick(NOW+dt.timedelta(minutes=1))['status'],'QUOTE_UNAVAILABLE')
        self.assertEqual(self.runtime.journal.active()[0]['state'],'WAIT_DATA')

    def test_stale_history_is_not_accepted(self):
        original=self.hub.fetch_interface
        def old(name,params,**kw):
            frame=original(name,params,**kw)
            if name in ('daily','adj_factor'):
                frame=frame[frame.trade_date!='20260903']
            return frame
        self.hub.fetch_interface=old
        payload=self.runtime.tick(NOW)
        self.assertEqual(payload['candidates'][0]['state'],'WAIT_DATA')
        self.assertEqual(len(self.runtime.journal.active()),0)

    def test_after_hours_pauses_existing_signal(self):
        self.runtime.tick(NOW)
        self.runtime.tick(NOW.replace(hour=12))
        self.assertEqual(self.runtime.journal.active()[0]['state'],'PAUSED')

    def test_single_writer_lock(self):
        with WorkerLock(self.tmp.name):
            with self.assertRaises(RuntimeError):
                with WorkerLock(self.tmp.name):
                    pass

    def test_stale_screen_never_shows_actionable_cards(self):
        payload=self.runtime.tick(NOW)
        html=render_payload(payload,NOW+dt.timedelta(minutes=5))
        self.assertIn('研究进程未启动或状态过期',html)
        self.assertNotIn('Offline fixture',html)


class SettlementTests(unittest.TestCase):
    def test_adjusted_markout_and_cost(self):
        origin=dict(signal_id='x',trade_date='20260904',first_seen_price=10)
        outcome=calculate(origin,1,'20260907',[dict(trade_date='20260907',close=5.5)],
                          [dict(trade_date='20260904',adj_factor=1),dict(trade_date='20260907',adj_factor=2)],NOW)
        self.assertAlmostEqual(outcome['gross_pct'],10)
        self.assertAlmostEqual(outcome['assumed_net_pct'],9.8)

    def test_missing_factor_is_unknown_not_loss(self):
        self.assertIsNone(calculate(dict(signal_id='x',trade_date='20260904',first_seen_price=10),1,'20260907',[],[],NOW))

    def test_trading_calendar_horizons_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            j=ResearchJournal(Path(directory)/'j.sqlite3')
            try:
                j.record(signal(),'a',NOW)
                days=['20260904','20260907','20260908','20260909','20260910','20260911']
                def fetch(name,params):
                    if name=='trade_cal':
                        return [dict(cal_date=(NOW+dt.timedelta(days=i)).strftime('%Y%m%d'),
                                     is_open=int((NOW+dt.timedelta(days=i)).weekday()<5))
                                for i in range(8)],'OK'
                    return [dict(trade_date=d,close=11,adj_factor=1) for d in days],'OK'
                settlement=Settlement(j,fetch)
                later=dt.datetime(2026,9,11,16,0,tzinfo=TZ)
                self.assertEqual(settlement.run(later)['settled'],3)
                self.assertEqual(settlement.run(later+dt.timedelta(hours=1))['settled'],0)
                self.assertEqual([r[0] for r in j.db.execute('SELECT target_date FROM research_outcome ORDER BY horizon')],['20260907','20260909','20260911'])
            finally:
                j.close()


class NotificationTests(unittest.TestCase):
    @patch('fusion_wecom.channel',return_value=('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=OFFLINE_FIXTURE',{'configured':True}))
    @patch('requests.post')
    def test_acknowledged_send(self,post,resolve):
        post.return_value.status_code=200
        post.return_value.json.return_value={'errcode':0}
        self.assertEqual(send_research(signal())['status'],'SENT')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    @patch('fusion_wecom.channel',return_value=('https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=OFFLINE_FIXTURE',{'configured':True}))
    @patch('requests.post',side_effect=requests.Timeout)
    def test_timeout_is_uncertain_not_retry(self,post,resolve):
        self.assertEqual(send_research(signal())['status'],'UNCERTAIN')

    @patch('fusion_wecom.channel',return_value=('',{'configured':False}))
    @patch('requests.post')
    def test_missing_webhook_no_network(self,post,resolve):
        self.assertEqual(send_research(signal())['status'],'FAILED')
        post.assert_not_called()

    def test_launcher_import_installs_v2(self):
        import cloud_product_server
        import decision_workspace
        self.assertEqual(decision_workspace.render_cards.__module__,'fusion_workspace_v2')


if __name__=='__main__':
    unittest.main()
