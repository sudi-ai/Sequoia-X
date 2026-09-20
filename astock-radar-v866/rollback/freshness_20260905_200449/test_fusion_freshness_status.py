import unittest
from datetime import datetime
from fusion_freshness_status import describe, panel, TZ


class FreshnessDisclosureTests(unittest.TestCase):
    def test_historical_receipt_is_not_quote_delay(self):
        now = datetime(2026, 9, 5, 15, tzinfo=TZ)
        result = describe({'trade_date': '20260904', 'observed_at': '2026-09-04T15:00:00+08:00'}, 'historical', False, now)
        self.assertEqual(result['receipt_age_seconds'], 86400)
        self.assertFalse(result['receipt_age_is_quote_delay'])
        self.assertFalse(result['fresh_for_intraday_review'])
        self.assertIsNone(result['source_trade_time'])

    def test_missing_or_naive_timestamp_not_invented(self):
        for value in (None, 'invalid', '2026-09-05T12:00:00'):
            self.assertIsNone(describe({'observed_at': value}, 'unknown', False)['receipt_age_seconds'])

    def test_html_is_escaped(self):
        html = panel({'source_trade_time': '<script>alert(1)</script>'}, '<bad>', False)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;bad&gt;', html)
        self.assertIn('fusion-data-freshness', html)

    def test_explicit_fresh_flag_preserved(self):
        self.assertTrue(describe({}, 'verified', True)['fresh_for_intraday_review'])


if __name__ == '__main__':
    unittest.main()
