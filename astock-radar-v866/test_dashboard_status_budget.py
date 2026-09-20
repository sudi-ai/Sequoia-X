import sqlite3
from contextlib import closing
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import sample_domains as domains


class DashboardBudgetTests(unittest.TestCase):
    def test_sql_budget_does_not_certify_partial_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.db'
            with closing(sqlite3.connect(path)) as db:
                db.executescript('CREATE TABLE snapshot_manifest(is_pit_safe,status,trade_date,mandatory_label,row_count); CREATE TABLE intraday_quote(is_pit_safe); CREATE TABLE discovery_observation(is_pit_safe);')
                db.executemany('INSERT INTO intraday_quote VALUES (?)',[(1,)]*5000)
                db.commit()
            result=domains.discovery_sample_status(path,budget_seconds=0)
            self.assertFalse(result['status_complete'])
            self.assertFalse(result['ready_for_alpha_rank'])

    def test_dashboard_cache_avoids_repeated_scans(self):
        with patch.object(domains,'_status_cache',None),patch.object(domains,'_status_expires',0),patch.object(domains,'discovery_sample_status',return_value={'status_complete':True}) as discovery,patch.object(domains,'trade_signal_sample_status',return_value={'status_complete':True}) as signal:
            first=domains.sample_domain_status()
            second=domains.sample_domain_status()
            self.assertEqual(discovery.call_count,1)
            self.assertEqual(signal.call_count,1)
            first['discovery']['mutation']=True
            self.assertNotIn('mutation',second['discovery'])


if __name__=='__main__':
    unittest.main()
