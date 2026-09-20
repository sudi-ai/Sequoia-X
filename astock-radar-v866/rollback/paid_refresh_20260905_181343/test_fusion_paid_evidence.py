import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from fusion_paid_evidence import collect,TZ,SOURCES


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'data.db'
        self.db=sqlite3.connect(self.path)
        for table in SOURCES:
            self.db.execute('CREATE TABLE '+table+' (id INTEGER PRIMARY KEY,ts_code TEXT,source TEXT,trade_date TEXT,observed_at TEXT,effective_at TEXT,data_quality REAL,payload_json TEXT)')
        self.asof=dt.datetime(2026,9,5,10,0,tzinfo=TZ)

    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def put(self,table,day='20260904',observed='2026-09-04T16:00:00+08:00',source=None,**values):
        payload={'ts_code':'600000.SH','trade_date':day,**values}
        self.db.execute('INSERT INTO '+table+' VALUES(NULL,?,?,?,?,?,?,?)',('600000.SH',source or next(iter(SOURCES[table])),day,observed,day[:4]+'-'+day[4:6]+'-'+day[6:]+'T15:00:00+08:00',1,json.dumps(payload)))
        self.db.commit()

    def test_basic_derivatives_only(self):
        self.put('daily_basic',pe_ttm=12,pb=1.1,secret='DO_NOT_SEND')
        bundle=collect(self.path,'600000',self.asof)
        self.assertEqual(bundle['parts']['daily_basic']['metrics']['pe_ttm'],12)
        self.assertNotIn('DO_NOT_SEND',json.dumps(bundle))

    def test_future_observation_rejected(self):
        self.put('daily_basic',observed='2026-09-06T16:00:00+08:00',pe_ttm=12)
        self.assertFalse(collect(self.path,'600000',self.asof)['parts'])

    def test_current_day_daily_rejected(self):
        self.put('daily_basic',day='20260905',observed='2026-09-05T09:00:00+08:00',pe_ttm=12)
        self.assertFalse(collect(self.path,'600000',self.asof)['parts'])

    def test_stale_rejected(self):
        self.put('daily_basic',day='20260101',pe_ttm=12)
        self.assertFalse(collect(self.path,'600000',self.asof)['parts'])

    def test_demo_rejected(self):
        self.put('daily_basic',source='DEMO',pe_ttm=12)
        self.assertFalse(collect(self.path,'600000',self.asof)['parts'])

    def test_naive_observation_rejected(self):
        self.put('daily_basic',observed='2026-09-04T16:00:00',pe_ttm=12)
        self.assertFalse(collect(self.path,'600000',self.asof)['parts'])

    def test_no_funds_units_or_intent_invented(self):
        self.put('moneyflow',net_amount=-10)
        metrics=collect(self.path,'600000',self.asof)['parts']['moneyflow']['metrics']
        self.assertEqual(metrics['net_flow_sign'],-1)
        self.assertNotIn('net_amount',metrics)

    def test_twenty_observation_window(self):
        for i in range(20):
            day=(dt.date(2026,9,4)-dt.timedelta(days=i)).strftime('%Y%m%d')
            self.put('stock_daily',day=day,close=10,low=9,high=11,vol=100)
        m=collect(self.path,'600000',self.asof)['parts']['stock_daily']['metrics']
        self.assertEqual(m['close_position_in_observed_range'],0.5)
        self.assertEqual(m['last_volume_over_previous19_mean'],1)
        self.assertFalse(m['continuous_trading_calendar_verified'])

    def test_readonly_source(self):
        self.put('daily_basic',pe_ttm=12)
        before=self.path.read_bytes();collect(self.path,'600000',self.asof)
        self.assertEqual(before,self.path.read_bytes())

    def test_absent_database_not_created(self):
        missing=Path(self.tmp.name)/'missing.db'
        self.assertFalse(collect(missing,'600000',self.asof)['parts'])
        self.assertFalse(missing.exists())


if __name__=='__main__':unittest.main()
