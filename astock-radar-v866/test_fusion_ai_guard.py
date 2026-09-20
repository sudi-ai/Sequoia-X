import datetime as dt
from pathlib import Path
import tempfile
import unittest
from fusion_ai_guard import Budget, TZ, readiness


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.budget = Budget(Path(self.temp.name) / 'budget.db')
        self.now = dt.datetime(2026, 9, 5, 12, tzinfo=TZ)

    def test_daily_cap(self):
        self.budget.reserve(5, 5, 50, self.now)
        with self.assertRaises(RuntimeError):
            self.budget.reserve(.01, 5, 50, self.now)

    def test_monthly_cap(self):
        self.budget.reserve(3, 5, 3, self.now)
        with self.assertRaises(RuntimeError):
            self.budget.reserve(1, 5, 3, self.now)

    def test_pending_survives_new_day(self):
        self.budget.reserve(5, 5, 50, self.now)
        with self.assertRaises(RuntimeError):
            self.budget.reserve(1, 5, 50, self.now + dt.timedelta(days=1))

    def test_unknown_retains_reservation(self):
        request = self.budget.reserve(5, 5, 50, self.now)
        self.budget.settle(request)
        with self.assertRaises(RuntimeError):
            self.budget.reserve(1, 5, 50, self.now)

    def test_settle_releases_difference(self):
        request = self.budget.reserve(5, 5, 50, self.now)
        self.budget.settle(request, 1)
        self.budget.reserve(4, 5, 50, self.now)

    def test_excess_cost_blocks_future_calls(self):
        request = self.budget.reserve(1, 5, 50, self.now)
        self.budget.settle(request, 2)
        with self.assertRaises(RuntimeError):
            self.budget.reserve(1, 5, 50, self.now)

    def test_settlement_cannot_be_overwritten(self):
        request = self.budget.reserve(1, 5, 50, self.now)
        self.budget.settle(request, 1)
        self.budget.settle(request, 1)
        with self.assertRaises(ValueError):
            self.budget.settle(request, 0)

    def test_missing_prices_block_paid_calls(self):
        report = readiness({'daily_budget_cny': 5, 'monthly_budget_cny': 50})
        self.assertEqual(report['status'], 'PRICING_REQUIRED')
        self.assertFalse(report['paid_calls_allowed'])


if __name__ == '__main__':
    unittest.main()
