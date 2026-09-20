from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from v8.env_loader import load_v8_env
load_v8_env()
from v8.config import CONFIG, ROOT
from v8.legacy_adapter import _STATE, handle_v8_legacy_event
from v8.storage import initialize

CN = ZoneInfo("Asia/Shanghai")


def sample_paid():
    return {"status":"OK","realtime_minute":{"freshness_status":"FRESH","quality":"OK","last":30.2,"vwap":30.0,
      "active_buy_ratio":.64,"order_imbalance":.25},"announcement":{"announcement_risk_level":"LOW"},
      "news":{"news_risk_level":"LOW"}}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--real-interface",action="store_true"); args=parser.parse_args()
    print(json.dumps({"root":str(ROOT),"db":str(CONFIG.db_path),"push":CONFIG.push_enabled,
      "auto_order":CONFIG.auto_order_enabled,"shadow_only":CONFIG.shadow_only},ensure_ascii=False,indent=2))
    initialize(); _STATE.clear(); now=datetime.now(CN)
    candidate={"code":"002837","name":"英维克","price":30.2,"amount_delta":36800000,"amount_acceleration":1.5,
      "ma20_slope10":2.2,"ma20_gap_pct":2.0,"ma60_gap_pct":5.0,"close_position":.7,"market_advance_ratio":.58,
      "market_score":68,"atr14":1.0,"pullback_confirmed":True}
    deep={"buy_score":82,"trend_score":80,"flow":{"score":78,"large_order_direction":.35},
      "trade":{"defense":28.9,"zone_low":29.8,"zone_high":30.3}}
    sector={"sector":"液冷服务器","score":78,"up_ratio":.68,"median_return":.018,"amount_acceleration":1.3,
      "leader_strength":82,"second_third_strength":74,"fund_persistence":76}
    if args.real_interface:
        result=handle_v8_legacy_event(candidate=candidate,deep=deep,tier="confirm",source="V8_ACCEPTANCE",sector_item=sector,now=now)
    else:
        with patch("v8.legacy_adapter.PAID_DATA.candidate_snapshot",return_value=sample_paid()):
            result=None
            for _ in range(3):
                result=handle_v8_legacy_event(candidate=candidate,deep=deep,tier="confirm",source="V8_ACCEPTANCE",sector_item=sector,now=now)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    c=sqlite3.connect(CONFIG.db_path)
    tables=("v8_signal_decisions","v8_counterfactuals","v8_persistence_observations","v8_push_state","v8_positions")
    counts={table:c.execute(f"select count(*) from {table}").fetchone()[0] for table in tables}; c.close()
    status="PASS" if result and result.get("status")=="RECORDED" else "FAIL"
    print(json.dumps({"tables":counts,"status":status},ensure_ascii=False,indent=2))
    return 0 if status=="PASS" else 1


if __name__=="__main__": raise SystemExit(main())
