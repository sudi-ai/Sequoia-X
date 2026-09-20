"""Offline acceptance for isolated new-version planning and notification routing."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from fusion_engine import TZ
from fusion_decision_plan import attach_plan
from fusion_wecom import channel, send_text
from fusion_operations import Operations
from fusion_preparation import Preparation


class DesignTests(unittest.TestCase):
    def test_no_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'secret.json'
            p.write_text(json.dumps({'wework_webhook_url':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=legacy'}))
            url,status=channel(p,{'WEWORK_WEBHOOK_URL':'old','V8_WEBHOOK_FILE':'old'})
            self.assertFalse(url)
            self.assertFalse(status['legacy_fallback'])

    def test_dedicated_ack(self):
        class Reply:
            status_code=200
            def json(self):return {'errcode':0}
        result=send_text('test',transport=lambda *a,**k:Reply(),environ={'FUSION_WECOM_WEBHOOK':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test'})
        self.assertEqual(result['status'],'SENT')

    def test_unknown_delivery(self):
        def fail(*a,**k):raise TimeoutError()
        result=send_text('test',transport=fail,environ={'FUSION_WECOM_WEBHOOK':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test'})
        self.assertEqual(result['status'],'UNCERTAIN')

    def test_execution_always_locked(self):
        now=dt.datetime(2026,9,4,10,tzinfo=TZ)
        r=attach_plan({'state':'WATCH','family':'LATENT_BASE','observed_at':now.isoformat(),'source_trade_time':now.isoformat()},now)
        self.assertFalse(r['execution_eligible'])
        self.assertTrue(r['decision_plan']['blockers'])
        stale=attach_plan(r,now+dt.timedelta(minutes=4))
        self.assertIsNone(stale['decision_plan']['valid_until'])

    def test_notice_is_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            o=Operations(d);calls=[];now=dt.datetime.now(TZ)
            def sender(text):calls.append(text);return {'status':'UNCERTAIN'}
            o.emit('one','TEST','test',now,True,sender)
            o.emit('one','TEST','test',now,True,sender)
            self.assertEqual(len(calls),1)
            o.close()

    def test_legacy_csv_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'old.csv';p.write_text('ts_code,price\n000001.SZ,10\n')
            original=p.read_bytes()
            (Path(d)/'fusion_legacy_sources.json').write_text(json.dumps([{'path':str(p)}]))
            o=Operations(d)
            self.assertEqual(o.import_legacy(dt.datetime.now(TZ))['imported'],1)
            self.assertEqual(p.read_bytes(),original)
            self.assertEqual(o.import_legacy(dt.datetime.now(TZ))['imported'],0)
            o.close()

    def test_unconfigured_portfolio(self):
        with tempfile.TemporaryDirectory() as d:
            o=Operations(d)
            self.assertEqual(o.portfolio(dt.datetime.now(TZ),{},False)['positions'],0)
            o.close()

    def test_preparation_requires_calendar(self):
        with tempfile.TemporaryDirectory() as d:
            p=Preparation(d)
            self.assertEqual(p.step(dt.datetime.now(TZ),None,None)['status'],'CALENDAR_UNKNOWN')
            self.assertEqual(p.shortlist(dt.datetime.now(TZ),None),[])
            p.close()


if __name__=='__main__':unittest.main()
