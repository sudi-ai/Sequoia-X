import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fusion_ai_worker import TZ,candidate,validate_result,Worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,9,7,10,0,tzinfo=TZ)
        self.row={'id':'a','state':'WATCH','first_seen_at':self.now.isoformat(),'first_seen_price':10,'source_updated_at':self.now.isoformat(),'payload':json.dumps({'code':'600000','secret':'must-not-leak'})}

    def test_fresh_candidate(self):
        item=candidate(self.row,self.now)
        self.assertEqual(item['code'],'600000')
        self.assertNotIn('secret',json.dumps(item))

    def test_old_candidate_rejected(self):
        self.row['first_seen_at']=(self.now-dt.timedelta(minutes=6)).isoformat()
        self.assertIsNone(candidate(self.row,self.now))

    def test_stale_update_rejected(self):
        self.row['source_updated_at']=(self.now-dt.timedelta(minutes=4)).isoformat()
        self.assertIsNone(candidate(self.row,self.now))

    def test_blocked_rejected(self):
        self.row['state']='BLOCKED'
        self.assertIsNone(candidate(self.row,self.now))

    def test_no_naive_timestamp(self):
        self.row['first_seen_at']='2026-09-07T10:00:00'
        self.assertIsNone(candidate(self.row,self.now))

    def test_bad_reference_rejected(self):
        value={'summary':'test','supported_ids':['invented'],'missing':[],'stance':'OBSERVE'}
        with self.assertRaises(ValueError): validate_result(value,{'E1':{}})

    def test_trade_stance_rejected(self):
        value={'summary':'test','supported_ids':[],'missing':[],'stance':'BUY'}
        with self.assertRaises(ValueError): validate_result(value,{'E1':{}})

    def test_valid_structure(self):
        value={'summary':'test','supported_ids':['E1'],'missing':[],'stance':'INSUFFICIENT'}
        self.assertEqual(validate_result(value,{'E1':{}}),value)

    def test_disabled_makes_no_call(self):
        with tempfile.TemporaryDirectory() as folder:
            w=Worker(Path(folder))
            try:
                with patch('urllib.request.build_opener',side_effect=AssertionError('network')):
                    self.assertEqual(w.tick({'enabled':False})['status'],'DISABLED')
            finally: w.db.close()


if __name__=='__main__': unittest.main()
