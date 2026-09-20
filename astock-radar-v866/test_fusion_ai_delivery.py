import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

import fusion_wecom
from fusion_ai_worker import review_message, send_review, Worker, TZ


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.item={'id':'delivery-test','code':'600000','first_seen_at':dt.datetime.now(TZ).isoformat(),
                   'facts':{'E1':{'fact':'test'},'E3':{'fact':'limitations'}}}
        self.review={'summary':'Evidence review only.', 'supported_ids':['E1','E3'],
                     'missing':['Independent evidence'], 'stance':'INSUFFICIENT'}
        self.info={'configured':True,'dedicated':True,'legacy_fallback':False,'fingerprint':'new-only'}
        self.cfg={'channel_fingerprint':'new-only'}

    def test_real_channel_tuple_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            secret=Path(folder)/'test.json'
            secret.write_text(json.dumps({'fusion_wecom_webhook':'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=offline-test'}))
            result=fusion_wecom.channel(secret_file=secret,environ={})
            self.assertIsInstance(result,tuple)
            with patch('fusion_wecom.channel',return_value=result), patch('fusion_wecom.send_text',return_value={'status':'SENT'}) as send:
                self.assertEqual(send_review(self.item,self.review,{'channel_fingerprint':result[1]['fingerprint']}),'SENT')
                self.assertIn(self.review['summary'],send.call_args.args[0])
                self.assertNotIn('offline-test',send.call_args.args[0])

    def test_wrong_group_blocked(self):
        with patch('fusion_wecom.channel',return_value=('unused',self.info)),patch('fusion_wecom.send_text') as send:
            self.assertEqual(send_review(self.item,self.review,{'channel_fingerprint':'old'}),'BLOCKED_CHANNEL')
            send.assert_not_called()

    def test_legacy_fallback_blocked(self):
        with patch('fusion_wecom.channel',return_value=('unused',dict(self.info,legacy_fallback=True))),patch('fusion_wecom.send_text') as send:
            self.assertEqual(send_review(self.item,self.review,self.cfg),'BLOCKED_CHANNEL')
            send.assert_not_called()

    def test_uncertain_not_retried(self):
        with patch('fusion_wecom.channel',return_value=('unused',self.info)),patch('fusion_wecom.send_text',side_effect=TimeoutError) as send:
            self.assertEqual(send_review(self.item,self.review,self.cfg),'UNCERTAIN')
            self.assertEqual(send.call_count,1)

    def test_message_utf8_bound(self):
        review=dict(self.review,summary='\u4e2d'*180,missing=['\u4e2d'*100]*8)
        text=review_message(self.item,review)
        self.assertLessEqual(len(text.encode('utf-8')),1800)
        self.assertIn('E3',text)
        self.assertIn('\u4e0d\u4fdd\u8bc1\u6536\u76ca',text)

    def test_preview_label(self):
        text=review_message(dict(self.item,preview=True),self.review)
        self.assertIn('\u975e\u80a1\u7968\u4fe1\u53f7',text)

    def test_analyze_records_delivery(self):
        cfg=dict(self.cfg,base_url='https://api.apiyi.com/v1',api_key='offline',notify=True,
                 input_usd_per_million=1.32,output_usd_per_million=3.96,cny_per_usd=7,
                 daily_budget_cny=5,monthly_budget_cny=50)
        response={'usage':{'prompt_tokens':100,'completion_tokens':100},
                  'choices':[{'finish_reason':'stop','message':{'content':json.dumps(self.review)}}]}
        opener=Mock()
        opener.open.return_value=io.BytesIO(json.dumps(response).encode())
        with tempfile.TemporaryDirectory() as folder:
            worker=Worker(Path(folder))
            try:
                with patch('urllib.request.build_opener',return_value=opener),patch('fusion_wecom.channel',return_value=('unused',self.info)),patch('fusion_wecom.send_text',return_value={'status':'SENT'}) as send:
                    worker.analyze(self.item,cfg)
                    row=worker.db.execute('SELECT status,notice_status FROM reviews').fetchone()
                    self.assertEqual(tuple(row),('REVIEWED','SENT'))
                    send.assert_called_once()
                    self.assertIn(self.review['summary'],send.call_args.args[0])
            finally:
                worker.db.close()


if __name__=='__main__':
    unittest.main()
