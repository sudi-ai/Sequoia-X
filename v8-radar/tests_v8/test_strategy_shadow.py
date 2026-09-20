from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from v8 import storage
from v8.strategy_shadow import high_tight_flag,limit_up_shakeout,persist,relative_strength


def _bar(day:int,open_price:float,high:float,low:float,close:float,vol:float)->dict:
    return {"trade_date":f"202601{day:02d}","open":open_price,"high":high,"low":low,"close":close,"vol":vol}


class StrategyShadowTests(unittest.TestCase):
    def test_high_tight_flag_requires_rise_before_consolidation(self):
        rows=[]
        for index in range(40):
            close=10+index*.25
            rows.append(_bar(index+1,close-.1,close+.5,close-.5,close,200))
        for index in range(10):
            close=19.0+index*.04
            rows.append(_bar(41+index,close-.03,close+.12,close-.12,close,90))
        result=high_tight_flag(rows)
        self.assertEqual(result["status"],"TRIGGERED")
        self.assertTrue(result["checks"]["先涨后整"])

    def test_high_tight_flag_rejects_high_before_late_low(self):
        rows=[]
        for index in range(40):
            close=20-index*.25
            rows.append(_bar(index+1,close+.1,close+.5,close-.5,close,200))
        for index in range(10):
            rows.append(_bar(41+index,10,10.1,9.9,10,90))
        self.assertNotEqual(high_tight_flag(rows)["status"],"TRIGGERED")

    def test_limit_up_rule_distinguishes_main_and_chinext(self):
        history=[_bar(index+1,10,10.2,9.8,10,100) for index in range(20)]
        sample=history+[_bar(21,10,11,10,11,100),_bar(22,11.3,11.4,10.9,11.1,150)]
        self.assertEqual(limit_up_shakeout("600001","测试股",sample)["status"],"TRIGGERED")
        self.assertEqual(limit_up_shakeout("300001","测试股",sample)["status"],"NOT_TRIGGERED")

    def test_relative_strength_needs_real_cross_section(self):
        market=[float(index) for index in range(30)]
        sector=[10.0,15.0,20.0,25.0,30.0]
        result=relative_strength(29.0,market,sector)
        self.assertEqual(result["status"],"TRIGGERED")
        self.assertGreaterEqual(result["score"],90)
        unknown=relative_strength(20.0,[1.0,2.0],[1.0,2.0])
        self.assertEqual(unknown["status"],"UNKNOWN")

    def test_triggered_factor_is_settled_separately_without_entry_permission(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/"v8.sqlite3";storage.initialize(db)
            stamp=datetime(2026,9,1,10,0).isoformat()
            event={"event_key":"base1","signal_time":stamp,"evaluation_time":stamp,
                   "code":"600001","name":"测试股","industry":"测试行业"}
            result=persist(event,{"HIGH_TIGHT_FLAG_REFINED":{"status":"TRIGGERED","score":95,
              "reason":"测试","factor_version":"TEST","action_permission":"ANNOTATION_ONLY"}},
              checkpoint_minute=0,entry_price=10,path=db)
            self.assertEqual(result,{"saved":1,"registered":3})
            c=storage.connect(db)
            factor=c.execute("select * from v8_strategy_factor_shadow").fetchone()
            decision=c.execute("select action,suggested_position_pct from v8_signal_decisions").fetchone()
            outcomes=c.execute("select count(*) from v8_execution_outcomes").fetchone()[0]
            c.close()
            self.assertEqual(factor["action_permission"],"ANNOTATION_ONLY")
            self.assertEqual(decision["action"],"FACTOR_SHADOW")
            self.assertEqual(decision["suggested_position_pct"],0)
            self.assertEqual(outcomes,3)


if __name__=="__main__":
    unittest.main()
