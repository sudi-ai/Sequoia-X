# -*- coding: utf-8 -*-
"""Aggregate today's P0 repair audits without claiming intraday completion."""
import json
from pathlib import Path

from p0_realtime import ROOT, atomic_json, iso

JSON_PATH, TXT_PATH = ROOT / "P0_FIX_AUDIT.json", ROOT / "P0_FIX_AUDIT.txt"


def load(name):
    path = ROOT / name
    if not path.exists():
        return {"status": "MISSING", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def generate():
    vwap = load("VWAP_UNIT_AUDIT.json")
    discovery = load("DISCOVERY_WRITE_AUDIT.json")
    outcome = load("SIGNAL_OUTCOME_AUDIT.json")
    auction = load("AUCTION_0925_AUDIT.json")
    recall = load("WINNER_RECALL_AUDIT.json")
    code_checks = {
        "vwap_valid_rate_ge_95": float(vwap.get("valid_rate_pct", 0)) >= 95.0,
        "vwap_abnormal_rate_le_1": float(vwap.get("abnormal_rate_pct", 100)) <= 1.0,
        "invalid_vwap_quarantined": "cannot enter" in str(vwap.get("rule", "")),
        "discovery_write_matches": discovery.get("status") == "PASS",
        "realtime_nonzero_write_rule": bool(discovery.get("rule_realtime_nonzero_written_nonzero")),
        "future_null_preserved": int(outcome.get("future_null_preserved", 0)) == int((outcome.get("counts") or {}).get("waiting", 0)),
        "no_fabricated_outcome": int(outcome.get("fabricated_outcomes", -1)) == 0,
        "no_real_win_rate": outcome.get("real_win_rate_generated") is False,
        "no_meta_training": outcome.get("meta_training_started") is False,
        "no_a_plus": outcome.get("a_plus_enabled") is False,
        "no_auto_trading": outcome.get("auto_trading") is False,
    }
    passed = all(code_checks.values())
    report = {"version": "V8.6.6 P0 FIX", "generated_at": iso(),
              "conclusion": "P0_FIX_CODE_COMPLETE" if passed else "P0_FIX_CODE_INCOMPLETE",
              "next_phase_gate": "BLOCKED_UNTIL_COMPLETE_REAL_INTRADAY_DAY",
              "code_checks": code_checks, "vwap": vwap, "discovery": discovery,
              "signal_outcome": outcome, "auction_0925": auction, "winner_recall": recall,
              "forbidden_claims": ["P0 completed", "V8.7 completed", "real-time winner discovery completed", "win rate validated", "A+ can be enabled"]}
    atomic_json(JSON_PATH, report)
    lines = ["P0 FIX AUDIT", "=" * 52, f"Conclusion: {report['conclusion']}",
             f"Next phase gate: {report['next_phase_gate']}", "", "Today's code checks:"]
    lines.extend(f"- {key}: {'PASS' if value else 'FAIL'}" for key, value in code_checks.items())
    lines += ["", f"09:25 audit: {auction.get('status')}", f"Winner recall audit: {recall.get('gate')}",
              "", "No complete real intraday day exists yet. Tomorrow's validation is still required."]
    TXT_PATH.write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(generate(), ensure_ascii=False, indent=2))
