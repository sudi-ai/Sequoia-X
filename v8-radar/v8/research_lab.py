"""Post-close research validation cycle for V8 Research Shadow.

The cycle only writes statistical evidence.  It cannot alter discovery scores,
thresholds, position sizes, notifications or order behaviour.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .research_validation import evaluate_research_sample, promotion_gate
from .storage import (connect, initialize, save_factor_research,
                      save_module_health, save_research_validation)

RULE_VERSION = "V8_RESEARCH_GATE_1"


def run_factor_research_cycle(now: datetime | None = None, *, path: Path | None = None) -> dict[str, Any]:
    """Build IC/quantile evidence from fully settled factor cross-sections."""
    from .factor_research import evaluate_factor

    stamp = (now or datetime.now().astimezone()).astimezone()
    initialize(path); c = connect(path)
    rows = [dict(row) for row in c.execute("""SELECT s.factor_name,substr(s.observed_at,1,10) trade_date,
      s.code,s.score factor,o.horizon,o.net_return_pct forward_return
      FROM v8_strategy_factor_shadow s
      JOIN (SELECT event_key,factor_name,max(checkpoint_minute) checkpoint_minute
            FROM v8_strategy_factor_shadow GROUP BY event_key,factor_name) latest
        ON latest.event_key=s.event_key AND latest.factor_name=s.factor_name
       AND latest.checkpoint_minute=s.checkpoint_minute
      JOIN v8_execution_outcomes o
        ON o.event_key=s.event_key||':'||s.factor_name
      WHERE o.track='FIXED' AND o.matured=1 AND o.net_return_pct IS NOT NULL
        AND o.horizon IN ('D1','D3','D5')""").fetchall()]
    c.close()
    groups: dict[tuple[str,str], list[dict[str,Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row['factor_name']),str(row['horizon']))].append(row)
    saved = usable = 0
    for (factor_name,horizon),sample in groups.items():
        result = evaluate_factor(sample)
        save_factor_research(stamp.date().isoformat(),factor_name,horizon,result,path)
        saved += 1
        usable += int(result.get('mean_ic') is not None and result.get('sample_n',0) >= 60)
    status = {"source_rows":len(rows),"groups_saved":saved,"usable_groups":usable,
              "automatic_promotion":False,"action_permission":"ANNOTATION_ONLY"}
    save_module_health("FACTOR_RESEARCH", "OK" if rows else "NO_MATURE_SAMPLES",
                       stamp.isoformat(), status, path)
    return status


def _rows(path: Path | None = None) -> list[dict[str, Any]]:
    initialize(path)
    c = connect(path)
    rows = [dict(row) for row in c.execute("""select o.*,d.strategy_version,d.action,d.market_regime
      from v8_execution_outcomes o left join v8_signal_decisions d on d.event_key=o.event_key
      order by coalesce(o.exit_time,''),o.id""").fetchall()]
    c.close()
    for row in rows:
        try:
            details = json.loads(row.get("details_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            details = {}
        if row.get("benchmark_return_pct") is None and details.get("benchmark_return_pct") is not None:
            row["benchmark_return_pct"] = details.get("benchmark_return_pct")
    return rows


def run_validation_cycle(now: datetime | None = None, *, path: Path | None = None) -> dict[str, Any]:
    stamp = (now or datetime.now().astimezone()).astimezone()
    rows = _rows(path)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        strategy = str(row.get("strategy_version") or "UNKNOWN")
        track = str(row.get("track") or "UNKNOWN")
        horizon = str(row.get("horizon") or "UNKNOWN")
        action = str(row.get("action") or "POSITION")
        for key in (
            f"strategy={strategy}|track={track}|horizon={horizon}",
            f"action={action}|track={track}|horizon={horizon}",
        ):
            groups[key].append(row)

    review_ready = 0
    saved = 0
    for key, sample in groups.items():
        metrics = evaluate_research_sample(sample)
        gate = promotion_gate(metrics)
        save_research_validation(stamp.date().isoformat(), key, RULE_VERSION,
                                 stamp.isoformat(), metrics, gate, path)
        saved += 1
        review_ready += int(bool(gate.get("eligible_for_manual_review")))

    result = {
        "rule_version": RULE_VERSION,
        "source_rows": len(rows),
        "groups_saved": saved,
        "manual_review_ready": review_ready,
        "automatic_promotion": False,
        "action_permission": "ANNOTATION_ONLY",
    }
    result["factor_research"] = run_factor_research_cycle(stamp,path=path)
    save_module_health("RESEARCH_VALIDATION", "OK" if rows else "NO_MATURE_SAMPLES",
                       stamp.isoformat(), result, path)
    return result
