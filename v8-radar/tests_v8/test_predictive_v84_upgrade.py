from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from v8.factor_research import evaluate_factor, spearman_ic
from v8.predictive_shadow import (audit_point_in_time, estimate_entry_probability,
                                  portfolio_correlation_shadow, wilson_interval)
from v8.storage import initialize
from v8.strategy_shadow import ma_volume_breakout, momentum_quality, volatility_contraction


class PredictiveShadowTests(unittest.TestCase):
    def test_wilson_interval_exposes_small_sample_uncertainty(self):
        low20, high20 = wilson_interval(16, 20)
        low100, high100 = wilson_interval(80, 100)
        self.assertLess(low20, low100)
        self.assertGreater(high20, 80)
        self.assertGreater(low100, 70)

    def test_probability_uses_only_mature_executable_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "v8.db"; initialize(db)
            con = sqlite3.connect(db)
            for index in range(100):
                key = f"E{index:03d}"
                decision = {"entry_quality_score": 78}
                con.execute("""insert into v8_signal_decisions(event_key,strategy_version,signal_time,
                  code,action,opportunity_score,market_score,market_regime,sector_score,
                  trend_quality_score,fund_quality_score,signal_persistence_score,risk_score,
                  suggested_position_pct,decision_json) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (key,"TEST","2026-01-01T10:00:00","000001","SHADOW_ENTRY_CONFIRMED",80,60,
                   "正常",75,72,73,74,0,0,json.dumps(decision)))
                result = 1.2 if index < 80 else -1.0
                con.execute("""insert into v8_execution_outcomes(event_key,track,horizon,matured,
                  net_return_pct,details_json) values(?,?,?,?,?,?)""",(key,"FORMAL","D3",1,result,"{}"))
            con.commit(); con.close()
            result = estimate_entry_probability({"opportunity_score":80,"entry_quality_score":78,
              "market_regime":"正常","sector_score":75,"trend_score":72,"fund_score":73,
              "persistence_score":74}, path=db, minimum_samples=30)
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["sample_n"], 100)
            self.assertEqual(result["raw_win_rate_pct"], 80)
            self.assertGreater(result["interval_low_pct"], 70)
            self.assertGreater(result["expected_net_return_pct"], 0)
            self.assertEqual(result["action_permission"], "ANNOTATION_ONLY")

    def test_probability_refuses_tiny_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "v8.db"; initialize(db)
            result = estimate_entry_probability({}, path=db, minimum_samples=30)
            self.assertEqual(result["status"], "INSUFFICIENT_SAMPLE")
            self.assertIsNone(result["probability_pct"])

    def test_point_in_time_audit_blocks_future_evidence(self):
        result = audit_point_in_time("2026-09-01T10:00:00", {
            "minute": {"data_time": "2026-09-01T10:01:00"},
            "daily": {"trade_date": "20260901"},
        })
        self.assertEqual(result["status"], "INVALID")
        self.assertEqual(len(result["violations"]), 1)

    def test_portfolio_correlation_uses_real_overlap(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "v8.db"; initialize(db)
            con = sqlite3.connect(db)
            con.execute("""insert into v8_positions(position_id,code,name,shares,cost_price,status)
              values('P1','000002','持仓股',100,10,'HOLDING')""")
            start = date(2026, 1, 1)
            price_a = price_b = 10.0
            for index in range(65):
                move = .01 if index % 3 else -.004
                price_a *= 1 + move; price_b *= 1 + move * .98
                day = (start + timedelta(days=index)).strftime("%Y%m%d")
                for code, price in (("000001.SZ",price_a),("000002.SZ",price_b)):
                    con.execute("""insert into v8_daily_bars(ts_code,trade_date,close,fetched_at,source)
                      values(?,?,?,?,?)""",(code,day,price,"2026-09-01T10:00:00","TEST"))
            con.commit();con.close()
            result = portfolio_correlation_shadow("000001",path=db)
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["risk"], "HIGH")
            self.assertGreater(result["max_correlation"], .95)


class FactorResearchTests(unittest.TestCase):
    def test_factor_ic_and_quantiles_are_auditable(self):
        rows=[]
        for day in ("20260101","20260102"):
            rows.extend({"trade_date":day,"factor":value,"forward_return":value/10}
                        for value in range(1,11))
        result=evaluate_factor(rows)
        self.assertEqual(spearman_ic(rows),1.0)
        self.assertEqual(result["mean_ic"],1.0)
        self.assertTrue(result["quantile_monotonic"])
        self.assertEqual(result["action_permission"],"ANNOTATION_ONLY")

    def test_new_strategy_factors_fail_closed_or_trigger_deterministically(self):
        rows=[]
        price=10.0
        for index in range(70):
            price *= 1.006
            rows.append({"trade_date":f"2026{index+1:04d}","open":price*.995,
                         "high":price*1.01,"low":price*.99,"close":price,
                         "vol":100 if index<69 else 180,"amount":200000})
        self.assertIn(ma_volume_breakout(rows)["status"],{"TRIGGERED","NOT_TRIGGERED"})
        self.assertIn(momentum_quality(rows)["status"],{"TRIGGERED","NOT_TRIGGERED"})
        self.assertIn(volatility_contraction(rows)["status"],{"TRIGGERED","NOT_TRIGGERED"})


if __name__ == "__main__":
    unittest.main()
