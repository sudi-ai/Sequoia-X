import datetime as dt
import tempfile
import unittest
from types import SimpleNamespace
from fusion_engine import TZ
from fusion_auction import AuctionBrief, valid_rows

NOW=dt.datetime(2026,9,4,9,26,tzinfo=TZ)

def row(day='20260904'):
    return dict(ts_code='600000.SH',trade_date=day,price=10,pre_close=9.9,vol=100,amount=1000)

class AuctionTests(unittest.TestCase):
    def test_previous_date_rejected(self):
        self.assertFalse(valid_rows([row('20260903')],'20260904'))

    def test_missing_raw_date_rejected(self):
        x=row();x.pop('trade_date');self.assertFalse(valid_rows([x],'20260904'))

    def test_wrong_units_rejected(self):
        x=row();x['amount']=1;self.assertFalse(valid_rows([x],'20260904'))

    def test_same_day_accepted(self):
        self.assertEqual(len(valid_rows([row()],'20260904')),1)

    def test_no_baseline_no_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            a=AuctionBrief(d,None,None)
            try:
                x=a.enrich(valid_rows([row()],'20260904'),'20260904',{'600000.SH':{'name':'fixture','structure':{'support':9,'resistance':11}}})[0]
                self.assertIsNone(x['amount_ratio']);self.assertIn('不足',x['confirmation'])
            finally:a.close()

    def test_send_once_and_no_backfill_pool(self):
        with tempfile.TemporaryDirectory() as d:
            sent=[]
            op=SimpleNamespace(emit=lambda *args: sent.append(args) or 'SENT')
            feed=SimpleNamespace(fetch=lambda day:([row(day)],'OK'))
            a=AuctionBrief(d,feed,op)
            try:
                status=a.step(NOW,True,None,None,[],True,clock=lambda:NOW)
                self.assertEqual(status['row_count'],1)
                self.assertIn('不追溯',status['pool_status'])
                a.step(NOW+dt.timedelta(minutes=1),True,None,None,[],True,clock=lambda:NOW)
                self.assertEqual(len(sent),1)
            finally:a.close()

    def test_closed_does_not_send(self):
        with tempfile.TemporaryDirectory() as d:
            a=AuctionBrief(d,None,None)
            try:self.assertEqual(a.step(NOW,False,None,None,[],True)['status'],'MARKET_CLOSED')
            finally:a.close()

    def test_window_missed_reports_missing(self):
        with tempfile.TemporaryDirectory() as d:
            op=SimpleNamespace(emit=lambda *args:'HELD')
            a=AuctionBrief(d,None,op)
            try:self.assertEqual(a.step(NOW.replace(minute=31),True,None,None,[],False)['status'],'MISSED_OR_UNAVAILABLE')
            finally:a.close()
