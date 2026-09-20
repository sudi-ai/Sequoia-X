from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from .storage import connect, save_miss_audit


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _audit_bucket(code:str,name:Any)->tuple[str,bool]:
    text=str(name or '').strip().upper()
    if text.startswith(('N','C')):return '上市初期新股',False
    if code.startswith(('4','8','92')):return '北交所',False
    if code.startswith('688'):return '科创板',True
    if code.startswith(('300','301')):return '创业板',True
    return '沪深主板',True


def early_watch_annotation(decision: Mapping[str, Any]) -> dict[str, Any]:
    """Research annotation only. It never upgrades WATCH into an entry action."""
    if str(decision.get("action")) != "WATCH":
        return {"watch_type": "NONE", "watch_score": 0.0, "reason": "非观察状态", "action_permission": "NONE"}
    opportunity = _num(decision.get("opportunity_score"))
    trend = _num((decision.get("trend") or {}).get("score"))
    fund = _num((decision.get("fund") or {}).get("score"))
    sector = _num((decision.get("sector") or {}).get("score"))
    persistence = _num((decision.get("persistence") or {}).get("score"))
    watch_score = round(.25 * opportunity + .25 * trend + .3 * fund + .1 * sector + .1 * persistence, 2)
    if fund >= 72 and trend >= 68 and sector < 45:
        kind, reason = "INDEPENDENT_STRENGTH", "个股资金与趋势较强，但板块尚未扩散"
    elif fund >= 58 and trend >= 55 and opportunity >= 58:
        kind, reason = "LATENT_WATCH", "资金和趋势开始改善，尚未达到持续确认"
    else:
        kind, reason = "NORMAL_WATCH", "普通观察，等待更多证据"
    return {"watch_type": kind, "watch_score": watch_score, "reason": reason,
            "action_permission": "ANNOTATION_ONLY"}


def audit_market_movers(rows: Iterable[Mapping[str, Any]], *, trade_date: str | None = None,
                        path: Path | None = None) -> dict[str, Any]:
    """Compare actual movers with V8 discoveries; results are research-only and never feed live decisions."""
    day = trade_date or date.today().isoformat()
    c = connect(path)
    decisions = {str(x["code"]).zfill(6): dict(x) for x in c.execute(
        "select code,action,opportunity_score from v8_signal_decisions where substr(signal_time,1,10)=?", (day,)
    ).fetchall()}
    c.close()
    total = discovered = 0
    groups: dict[str, int] = {};segments:dict[str,dict[str,int]]={}
    eligible_total=eligible_discovered=0
    for item in rows:
        code = str(item.get("code") or "").split(".")[0].zfill(6)
        if not code.strip("0"):
            continue
        total += 1
        segment,eligible=_audit_bucket(code,item.get('name'))
        decision = decisions.get(code)
        if decision:
            discovered += 1
            action = str(decision.get("action") or "WATCH")
            status = "CONFIRMED" if action == "SHADOW_ENTRY_CONFIRMED" else ("BLOCKED" if action == "BLOCKED" else "WATCH")
            reason = "已发现，需复核后续状态是否合理"
        else:
            action = None
            status = "NOT_DISCOVERED"
            reason = "未进入V6.6/V8候选链路"
        groups[status] = groups.get(status, 0) + 1
        bucket=segments.setdefault(segment,{'total':0,'discovered':0,'blocked':0,'confirmed':0})
        bucket['total']+=1
        if status!='NOT_DISCOVERED':bucket['discovered']+=1
        if status=='BLOCKED':bucket['blocked']+=1
        if status=='CONFIRMED':bucket['confirmed']+=1
        if eligible:
            eligible_total+=1
            if status!='NOT_DISCOVERED':eligible_discovered+=1
        save_miss_audit({"trade_date": day, "code": code, "name": item.get("name"),
          "actual_return_pct": item.get("actual_return_pct"), "max_return_pct": item.get("max_return_pct"),
          "discovery_status": status, "decision_action": action, "miss_reason": reason,
          "audit_segment":segment,"eligible_for_main_recall":eligible}, path)
    return {"trade_date": day, "total_movers": total, "discovered": discovered,
            "recall_pct": round(discovered / total * 100, 2) if total else None, "groups": groups,
            "segments":segments,"main_sample_total":eligible_total,
            "main_sample_discovered":eligible_discovered,
            "main_sample_recall_pct":round(eligible_discovered/eligible_total*100,2) if eligible_total else None}
