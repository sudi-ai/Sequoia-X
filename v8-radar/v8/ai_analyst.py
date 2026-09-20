"""V8 AI analyst Shadow layer.

This module synthesizes auditable market, seasonal-event and stock evidence.
It is deliberately isolated from entry scoring and order execution.  The
initial provider is a deterministic local evidence analyst so V8 keeps working
without an external model, network dependency or private-data transmission.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

from .config import CONFIG
from .push import send_once
from .storage import (connect, initialize, save_ai_analyst_report,
                      save_module_health)

ANALYST_MODE = "LOCAL_EVIDENCE_SHADOW"
ACTION_PERMISSION = "ANNOTATION_ONLY"

# Only stable Gregorian event anchors are built in. Lunar or officially shifted
# holidays must be supplied later through a dated calendar source; V8 never
# guesses them from the natural calendar.
ANNUAL_EVENTS = (
    {"name":"元旦消费","month":1,"day":1,"lead":35,
     "themes":("旅游","酒店","食品","零售","影视","航空")},
    {"name":"五一假期","month":5,"day":1,"lead":50,
     "themes":("旅游","酒店","免税","影视","航空","餐饮")},
    {"name":"暑期消费","month":7,"day":1,"lead":45,
     "themes":("旅游","酒店","影视","游戏","航空","教育")},
    {"name":"开学季","month":9,"day":1,"lead":30,
     "themes":("教育","文具","零售","消费电子")},
    {"name":"国庆假期","month":10,"day":1,"lead":60,
     "themes":("旅游","酒店","免税","影视","航空","食品","零售")},
    {"name":"双十一消费","month":11,"day":11,"lead":50,
     "themes":("电商","物流","零售","消费电子","家电","包装")},
    {"name":"年末消费","month":12,"day":20,"lead":40,
     "themes":("零售","食品","白酒","家电","旅游","影视")},
)

_LAST_SLOTS: set[str] = set()


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number=float(value)
        return number if math.isfinite(number) else default
    except (TypeError,ValueError):
        return default


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value,Mapping):return dict(value)
    try:
        parsed=json.loads(value or "{}")
        return parsed if isinstance(parsed,dict) else {}
    except (TypeError,ValueError,json.JSONDecodeError):
        return {}


def _report_id(report_type: str,subject: str,slot: str) -> str:
    raw=f"{report_type}|{subject}|{slot}"
    return "v8ai_"+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _candidate_rows(day: str,path: Path|None=None) -> list[dict[str,Any]]:
    initialize(path);c=connect(path)
    rows=[dict(row) for row in c.execute("""select d.*,s.fund_quality_score,s.trend_quality_score,
      s.sector_score decision_sector_score,s.risk_score,s.market_regime decision_market_regime,
      s.decision_json from v8_discovery_candidates d left join v8_signal_decisions s
      on s.event_key=d.event_key where d.trade_date=?""",(day,)).fetchall()]
    c.close()
    for row in rows:
        details=_json(row.get("details_json"));decision=_json(row.get("decision_json"))
        row["details"]=details;row["decision"]=decision
        row["fund_score"]=_finite(row.get("fund_quality_score"),
          _finite(details.get("fund_score"),50))
        row["sector_score_effective"]=_finite(row.get("decision_sector_score"),
          _finite(details.get("sector_score"),50))
        row["first_pct"]=_finite(row.get("first_seen_pct_chg"),_finite(row.get("pct_chg"),0))
    return rows


def _rank_candidate(row: Mapping[str,Any]) -> dict[str,Any]:
    discovery=_finite(row.get("discovery_score"),50) or 50
    opportunity=_finite(row.get("opportunity_score"),50) or 50
    fund=_finite(row.get("fund_score"),50) or 50
    sector=_finite(row.get("sector_score_effective"),50) or 50
    pct=_finite(row.get("pct_chg"),0) or 0
    first_pct=_finite(row.get("first_pct"),pct) or pct
    risk=_finite(row.get("risk_score"),50) or 50
    late_penalty=max(0,pct-3)*6+max(0,first_pct-2.5)*4
    score=max(0,min(100,discovery*.28+opportunity*.24+fund*.20+sector*.18+(100-risk)*.10-late_penalty))
    if pct>5 or first_pct>4:state="上涨较多·只观察"
    elif fund<48:state="资金待确认"
    elif sector<55:state="板块扩散待确认"
    elif first_pct<=2.5 and pct<=4:state="提前复核候选"
    else:state="常规观察"
    reasons=[f"初筛{discovery:.1f}",f"机会{opportunity:.1f}",f"资金{fund:.1f}",f"板块{sector:.1f}"]
    if late_penalty>0:reasons.append(f"上涨位置扣分{late_penalty:.1f}")
    return {"code":row.get("code"),"name":row.get("name"),"industry":row.get("industry"),
      "price":row.get("price"),"pct_chg":pct,"first_seen_pct_chg":first_pct,
      "analyst_score":round(score,2),"state":state,"reasons":reasons,
      "action_permission":ACTION_PERMISSION}


def screen_candidates(now: datetime|None=None,*,themes:Iterable[str]=(),path:Path|None=None,
                      limit:int|None=None) -> list[dict[str,Any]]:
    now=(now or datetime.now().astimezone()).astimezone();theme_words=tuple(str(x) for x in themes)
    rows=_candidate_rows(now.date().isoformat(),path)
    if theme_words:
        rows=[row for row in rows if any(word in " ".join(str(row.get(k) or "")
          for k in ("industry","name")) for word in theme_words)]
    ranked=[_rank_candidate(row) for row in rows]
    ranked.sort(key=lambda row:(row["state"]=="提前复核候选",row["analyst_score"]),reverse=True)
    return ranked[:(limit or CONFIG.ai_analyst_candidate_limit)]


def _market_evidence(now:datetime,path:Path|None=None)->dict[str,Any]:
    day=now.date().isoformat();initialize(path);c=connect(path)
    run=c.execute("""select * from v8_discovery_runs where substr(observed_at,1,10)=?
      order by observed_at desc limit 1""",(day,)).fetchone()
    sector_rows=[dict(row) for row in c.execute("""select * from v8_sector_intraday_snapshots
      where trade_date=? and observed_at=(select max(observed_at) from v8_sector_intraday_snapshots
      where trade_date=?)""",(day,day)).fetchall()]
    c.close();run_row=dict(run) if run else {};details=_json(run_row.get("details_json"))
    market=details.get("market") if isinstance(details.get("market"),Mapping) else {}
    return {"source_status":"FRESH" if run else "UNAVAILABLE","observed_at":run_row.get("observed_at"),
      "source_time":run_row.get("source_time"),"market_score":_finite(run_row.get("market_score")),
      "market_regime":run_row.get("market_regime"),"universe_count":run_row.get("universe_count"),
      "advance_ratio":market.get("advance_ratio"),"limit_up":market.get("limit_up"),
      "limit_down":market.get("limit_down"),"median_pct":market.get("median_pct"),"sectors":sector_rows}


def analyze_market(now:datetime|None=None,*,slot:str="MANUAL",path:Path|None=None)->dict[str,Any]:
    now=(now or datetime.now().astimezone()).astimezone();evidence=_market_evidence(now,path)
    score=_finite(evidence.get("market_score"),50) or 50
    regime=str(evidence.get("market_regime") or ("进攻" if score>=68 else ("防守" if score<42 else "正常")))
    sectors=evidence.get("sectors") or []
    sectors.sort(key=lambda row:_finite(row.get("median_pct"),-999) or -999,reverse=True)
    strong=sectors[:3];weak=list(reversed(sectors[-3:])) if sectors else []
    candidates=screen_candidates(now,path=path)
    fund_values=[_finite(row.get("fund_score")) for row in _candidate_rows(now.date().isoformat(),path)]
    fund_values=[value for value in fund_values if value is not None]
    confidence=20 if evidence["source_status"]!="FRESH" else min(88,48+len(sectors)*2+len(candidates)*2)
    if evidence["source_status"]!="FRESH":conclusion="市场快照缺失，保持中性，不生成方向判断。"
    elif regime=="防守":conclusion="市场处于防守环境，优先风险控制；候选只做提前观察。"
    elif regime=="进攻":conclusion="市场风险偏好较强，但仍需避免追逐已明显上涨个股。"
    else:conclusion="市场环境中性，重点观察板块扩散与资金持续性。"
    report={"report_id":_report_id("MARKET","A_SHARE",f"{now.date()}:{slot}"),
      "report_type":"MARKET","subject_key":"A_SHARE","observed_at":now.isoformat(timespec="seconds"),
      "evidence_cutoff":now.isoformat(timespec="seconds"),"analyst_mode":ANALYST_MODE,
      "confidence":confidence,"conclusion":conclusion,"market_regime":regime,"market_score":score,
      "market_evidence":evidence,"strong_sectors":strong,"weak_sectors":weak,
      "candidate_fund_median":round(median(fund_values),2) if fund_values else None,
      "screened_candidates":candidates,"data_limitations":[] if evidence["source_status"]=="FRESH" else ["市场快照不可用"],
      "action_permission":ACTION_PERMISSION,"automatic_trade":False}
    save_ai_analyst_report(report,path);return report


def _next_occurrence(rule:Mapping[str,Any],today:date)->date:
    candidate=date(today.year,int(rule["month"]),int(rule["day"]))
    return candidate if candidate>=today else date(today.year+1,int(rule["month"]),int(rule["day"]))


def analyze_event_calendar(now:datetime|None=None,*,slot:str="MANUAL",path:Path|None=None)->dict[str,Any]:
    now=(now or datetime.now().astimezone()).astimezone();events=[]
    for rule in ANNUAL_EVENTS:
        event_date=_next_occurrence(rule,now.date());days=(event_date-now.date()).days
        lead=min(int(rule["lead"]),CONFIG.ai_analyst_event_lead_days)
        if days>lead:continue
        stage="提前建池" if days>30 else ("数据跟踪" if days>10 else ("资金验证" if days>3 else "防利好兑现"))
        candidates=screen_candidates(now,themes=rule["themes"],path=path,limit=5)
        events.append({"name":rule["name"],"event_date":event_date.isoformat(),"days_to_event":days,
          "stage":stage,"themes":list(rule["themes"]),"candidates":candidates,
          "warning":"历史季节规律只是弱证据，必须等待实时数据验证"})
    events.sort(key=lambda item:item["days_to_event"])
    conclusion=(f"未来{CONFIG.ai_analyst_event_lead_days}天识别到{len(events)}个固定日历事件；"
      "仅建立提前候选池，不按节日规律直接建仓。")
    report={"report_id":_report_id("EVENT_CALENDAR","A_SHARE",f"{now.date()}:{slot}"),
      "report_type":"EVENT_CALENDAR","subject_key":"A_SHARE","observed_at":now.isoformat(timespec="seconds"),
      "evidence_cutoff":now.isoformat(timespec="seconds"),"analyst_mode":ANALYST_MODE,
      "confidence":65 if events else 30,"conclusion":conclusion,"events":events,
      "calendar_scope":"仅内置固定公历事件；春节、中秋及调休日期等待权威交易日历",
      "action_permission":ACTION_PERMISSION,"automatic_trade":False}
    save_ai_analyst_report(report,path);return report


def analyze_stock(code:str,now:datetime|None=None,*,path:Path|None=None)->dict[str,Any]:
    now=(now or datetime.now().astimezone()).astimezone();code=str(code).split(".")[0]
    rows=[row for row in _candidate_rows(now.date().isoformat(),path) if str(row.get("code"))==code]
    candidate=_rank_candidate(rows[0]) if rows else None
    conclusion=(f"{candidate['name']}进入{candidate['state']}，仍需价格、资金和板块共同复核。"
      if candidate else "当前没有该股票的当日独立候选证据，保持未知，不强行判断。")
    report={"report_id":_report_id("STOCK",code,f"{now:%Y%m%d%H%M}"),"report_type":"STOCK",
      "subject_key":code,"observed_at":now.isoformat(timespec="seconds"),
      "evidence_cutoff":now.isoformat(timespec="seconds"),"analyst_mode":ANALYST_MODE,
      "confidence":70 if candidate else 20,"conclusion":conclusion,"candidate":candidate,
      "data_limitations":[] if candidate else ["当日候选数据缺失"],
      "action_permission":ACTION_PERMISSION,"automatic_trade":False}
    save_ai_analyst_report(report,path);return report


def build_market_message(report:Mapping[str,Any])->str:
    market=report.get("market_evidence") or {};strong=report.get("strong_sectors") or []
    candidates=report.get("screened_candidates") or []
    lines=["🧠 A股机会雷达 V8｜AI市场分析师","",
      f"🌐 市场 {report.get('market_regime')}｜市场分 {float(report.get('market_score') or 0):.1f}",
      f"📊 上涨广度 {float(market.get('advance_ratio') or 0)*100:.1f}%｜涨停 {market.get('limit_up','待确认')}｜跌停 {market.get('limit_down','待确认')}"]
    if strong:lines.append("🔥 强势方向："+"｜".join(str(x.get("industry")) for x in strong[:3]))
    if candidates:
        lines.append("🔎 提前复核候选："+"｜".join(
          f"{x.get('name')}({float(x.get('pct_chg') or 0):+.2f}%)" for x in candidates[:5]))
    lines += ["",f"📌 结论：{report.get('conclusion')}","⚠ AI只做证据归纳，不能代替行情确认；数据缺失保持未知。",
      "V8 AI分析师 Shadow｜不自动下单｜不构成投资建议"]
    return "\n".join(lines)


def build_event_message(report:Mapping[str,Any])->str:
    lines=["🧠 A股机会雷达 V8｜节日与情绪事件雷达",""]
    for event in (report.get("events") or [])[:3]:
        lines.append(f"📅 {event['name']}｜还有{event['days_to_event']}天｜{event['stage']}")
        lines.append("🎯 观察方向："+"、".join(event["themes"][:6]))
        if event.get("candidates"):
            lines.append("🔎 关联候选："+"｜".join(str(x.get("name")) for x in event["candidates"][:4]))
    if not report.get("events"):lines.append("当前观察窗口内暂无固定日历事件。")
    lines += ["",f"📌 结论：{report.get('conclusion')}",
      "⚠ 节日规律只用于提前建池，仍需订单、资金、板块和价格共同确认。",
      "V8 AI分析师 Shadow｜不自动下单｜不构成投资建议"]
    return "\n".join(lines)


def _within(now:datetime,start:time,end:time)->bool:
    current=now.time().replace(tzinfo=None);return start<=current<=end


def run_ai_analyst_schedule(now:datetime|None=None,*,path:Path|None=None)->list[dict[str,Any]]:
    now=(now or datetime.now().astimezone()).astimezone();results=[]
    if not CONFIG.ai_analyst_enabled:return results
    slots=[
      ("EVENT_AM",time(8,35),time(8,45),"EVENT"),
      ("MARKET_AM",time(10,25),time(10,35),"MARKET"),
      ("MARKET_PM",time(14,25),time(14,35),"MARKET"),
      ("MARKET_CLOSE",time(15,25),time(15,35),"MARKET"),
      ("EVENT_PM",time(20,25),time(20,35),"EVENT"),
    ]
    for name,start,end,kind in slots:
        slot=f"{now.date()}|{name}"
        if slot in _LAST_SLOTS or not _within(now,start,end):continue
        if kind=="MARKET" and now.weekday()>=5:continue
        report=analyze_market(now,slot=name,path=path) if kind=="MARKET" else analyze_event_calendar(now,slot=name,path=path)
        pushed=False;detail="not_requested"
        if CONFIG.ai_analyst_push_enabled:
            message=build_market_message(report) if kind=="MARKET" else build_event_message(report)
            pushed,detail=send_once(f"{slot}|V8_AI_ANALYST",message,path=path)
        report["push_status"]="SENT" if pushed else str(detail).upper()
        save_ai_analyst_report(report,path);_LAST_SLOTS.add(slot);results.append(report)
    save_module_health("AI_ANALYST","OK",now.isoformat(timespec="seconds"),{
      "mode":ANALYST_MODE,"reports":len(results),"automatic_trade":False},path)
    return results
