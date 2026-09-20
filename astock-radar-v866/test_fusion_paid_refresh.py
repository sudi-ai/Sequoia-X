import datetime as dt
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from fusion_paid_evidence import collect,TZ
from fusion_paid_refresh import refresh,reserve_call,choose_codes


class FakeHub:
    def __init__(self,store):self.store=store
    def fetch_interface(self,name,params,**kwargs):
        assert kwargs['pit_safe'] is False
        if name=='trade_cal':return pd.DataFrame([{'cal_date':'20260904','is_open':1}])
        code=params['ts_code'];rows=[]
        for i in range(20 if name=='daily' else 1):
            day=(dt.date(2026,9,4)-dt.timedelta(days=i)).strftime('%Y%m%d')
            rows.append({'ts_code':code,'trade_date':day,'close':10,'low':9,'high':11,'vol':100,
                         'pe_ttm':12,'pb':1,'net_amount':20,'net_mf_amount':20,
                         'source':'tushare.'+name+'@dailyfetch','observed_at':'2026-09-05T09:00:00+08:00',
                         'effective_at':'2026-09-04T15:00:00+08:00','data_quality':1,'is_pit_safe':False})
        frame=pd.DataFrame(rows);frame.attrs['status']='OK'
        self.store.append_frame({'daily':'stock_daily','daily_basic':'daily_basic','moneyflow_ths':'moneyflow','moneyflow':'moneyflow'}[name],frame)
        return frame


class RefreshTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=dt.datetime(2026,9,5,10,tzinfo=TZ)
    def tearDown(self):self.tmp.cleanup()
    def test_indexed_context_has_three_parts(self):
        r=refresh(self.root,['600000'],self.now,FakeHub)
        self.assertEqual(r['target_trade_date'],'20260904')
        p=self.root/'data/fusion_paid_context.sqlite3'
        self.assertEqual(set(collect(p,'600000',self.now)['parts']),{'stock_daily','daily_basic','moneyflow'})
        self.assertFalse((self.root/'data/live_pit.db').exists())
        with closing(sqlite3.connect(p)) as db:
            self.assertTrue(any('ix_context_stock_daily' in str(row) for row in db.execute('EXPLAIN QUERY PLAN SELECT * FROM stock_daily WHERE ts_code=? ORDER BY id DESC LIMIT 600',('600000.SH',))))
    def test_call_limit_persists(self):
        refresh(self.root,[],self.now,FakeHub);p=self.root/'data/fusion_paid_context.sqlite3'
        with patch('fusion_paid_refresh.DAILY_CALL_LIMIT',2):
            self.assertTrue(reserve_call(p,'2026-09-05','daily','x'))
            self.assertTrue(reserve_call(p,'2026-09-05','daily','x'))
            self.assertFalse(reserve_call(p,'2026-09-05','daily','x'))
    def test_no_calendar_no_stock_calls(self):
        class Empty(FakeHub):
            def fetch_interface(self,name,params,**kw):
                assert name=='trade_cal'
                return pd.DataFrame()
        self.assertEqual(refresh(self.root,['600000'],self.now,Empty)['status'],'WAITING_VERIFIED_TRADE_CALENDAR')
    def test_no_candidates_no_api(self):
        def fail(store):raise AssertionError('No API initialization expected')
        self.assertEqual(refresh(self.root,[],self.now,fail)['status'],'IDLE')
    def test_standard_moneyflow_fallback(self):
        class Fallback(FakeHub):
            def fetch_interface(self,name,params,**kw):
                return pd.DataFrame() if name=='moneyflow_ths' else super().fetch_interface(name,params,**kw)
        refresh(self.root,['600000'],self.now,Fallback)
        self.assertEqual(collect(self.root/'data/fusion_paid_context.sqlite3','600000',self.now)['parts']['moneyflow']['source'],'tushare.moneyflow@dailyfetch')
    def test_missing_baseline_no_creation(self):
        self.assertEqual(choose_codes(self.root,self.now),[])


if __name__=='__main__':unittest.main()
