import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from fusion_engine import TZ
from fusion_v8_baseline import V8Baseline

NOW=dt.datetime(2026,9,4,10,tzinfo=TZ)

class BaselineTests(unittest.TestCase):
    def setup_case(self,d,first):
        p=Path(d)/'old.sqlite3'
        with closing(sqlite3.connect(p)) as c,c:
            c.execute('CREATE TABLE v8_discovery_candidates(event_key TEXT,trade_date TEXT,code TEXT,name TEXT,first_seen_at TEXT,first_seen_price REAL,first_seen_pct_chg REAL,last_seen_at TEXT,action TEXT)')
            c.execute('INSERT INTO v8_discovery_candidates VALUES (?,?,?,?,?,?,?,?,?)',('e',first.date().isoformat(),'600000','fixture',first.isoformat(),10,1,NOW.isoformat(),'WATCH'))
        (Path(d)/'fusion_v8_source.json').write_text(json.dumps({'database':str(p)}))
        return p

    def test_preserves_original_and_sends_early(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.setup_case(d,NOW-dt.timedelta(seconds=30));before=p.read_bytes();sent=[]
            b=V8Baseline(d,SimpleNamespace(emit=lambda *a:sent.append(a) or 'SENT'))
            try:
                s=b.sync(NOW,True);b.sync(NOW+dt.timedelta(seconds=1),True)
                self.assertEqual(s['retained_origins'],1);self.assertEqual(len(sent),1)
                self.assertEqual(p.read_bytes(),before)
            finally:b.close()

    def test_historical_not_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            self.setup_case(d,NOW-dt.timedelta(days=1));sent=[]
            b=V8Baseline(d,SimpleNamespace(emit=lambda *a:sent.append(a) or 'SENT'))
            try:b.sync(NOW,True);self.assertEqual(sent,[])
            finally:b.close()

    def test_new_evidence_not_required(self):
        with tempfile.TemporaryDirectory() as d:
            self.setup_case(d,NOW);sent=[]
            b=V8Baseline(d,SimpleNamespace(emit=lambda *a:sent.append(a) or 'SENT'))
            try:b.sync(NOW,True,current_quotes={});self.assertEqual(len(sent),1)
            finally:b.close()

    def test_unconfigured(self):
        with tempfile.TemporaryDirectory() as d:
            b=V8Baseline(d,None)
            try:self.assertEqual(b.sync(NOW)['status'],'NOT_CONFIGURED')
            finally:b.close()
