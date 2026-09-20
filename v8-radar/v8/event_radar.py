from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime,timedelta,time
from typing import Any,Iterable,Mapping

from .config import CONFIG
from .message import build_event_message,build_software_issue_message
from .causal_intelligence import analyze_event
from .event_inference import extract_cues,infer_hypothesis,should_push_hypothesis
from .event_taxonomy import TOPIC_ALIASES,classify_event_metadata
from .event_governance import govern_event
from .event_market_shadow import evaluate_event_market_shadow
from .paid_data import PAID_DATA
from .portfolio import list_positions
from .push import send_once
from .runtime_log import log_exception
from .storage import connect,initialize,save_event_radar,save_module_health


POSITIVE=("回购","增持","中标","重大合同","业绩预增","扭亏","订单","获批","突破","首次覆盖","上调评级")
NEGATIVE=("立案调查","退市风险","终止上市","重大违法","处罚","业绩预亏","重大诉讼","债务违约","拟减持","解禁")
HARD=("立案调查","退市风险","终止上市","重大违法","债务违约")
COMPLETION=("减持完毕","减持计划实施完毕","减持期限届满","未实施减持","未减持")
TOPICS={alias:topic for topic,aliases in TOPIC_ALIASES.items() for alias in aliases}

MATERIAL_REPORT_POSITIVE=("首次覆盖","上调评级","调高评级","上调盈利预测","盈利预测上调","目标价上调","超预期")
MATERIAL_REPORT_NEGATIVE=("下调评级","调低评级","下调盈利预测","盈利预测下调","目标价下调","不及预期")


def _source_identity(row:Mapping[str,Any],source_api:str)->str:
    for key in ("org_name","source","media_name","publisher","website","channels","author_name"):
        value=str(row.get(key) or "").strip()
        if value:return value[:80]
    return source_api


def _report_change(row:Mapping[str,Any],text:str)->tuple[str,str,str]:
    if any(word in text for word in MATERIAL_REPORT_NEGATIVE):
        return "NEGATIVE","HIGH","MATERIAL_DOWNGRADE"
    if "首次覆盖" in text:
        return "POSITIVE","LOW","FIRST_COVERAGE"
    if any(word in text for word in MATERIAL_REPORT_POSITIVE):
        return "POSITIVE","LOW","MATERIAL_UPGRADE"
    current=str(row.get("rating") or row.get("recommend") or "").strip()
    previous=str(row.get("last_rating") or row.get("pre_rating") or "").strip()
    if current and previous and current!=previous:
        return ("POSITIVE","LOW","RATING_CHANGED") if any(x in current for x in ("买入","增持","推荐")) else ("NEUTRAL","UNKNOWN","RATING_CHANGED")
    return "NEUTRAL","UNKNOWN","ROUTINE_RESEARCH"


def _number(value:Any)->float|None:
    try:return float(value)
    except (TypeError,ValueError):return None


def _historical_report_change(code:str,source_identity:str,current:Mapping[str,Any])->tuple[str,str,str]|None:
    """Compare like-for-like forecasts after the first archived observation."""
    initialize();c=connect()
    rows=c.execute("""select details_json from v8_event_radar where source_api='report_rc' and code=?
      order by created_at desc limit 30""",(code,)).fetchall();c.close()
    for raw in rows:
        try:old=json.loads(raw[0] or "{}")
        except (TypeError,json.JSONDecodeError):continue
        if str(old.get("source_identity") or "")!=source_identity:continue
        previous=old.get("source_fields") if isinstance(old.get("source_fields"),dict) else {}
        current_rating=str(current.get("rating") or current.get("recommend") or "")
        previous_rating=str(previous.get("rating") or previous.get("recommend") or "")
        if current_rating and previous_rating and current_rating!=previous_rating:
            return ("POSITIVE","LOW","RATING_CHANGED") if any(x in current_rating for x in ("买入","增持","推荐")) else ("NEGATIVE","HIGH","RATING_CHANGED")
        changes=[]
        for key in ("target_price","tp","eps","np","op_pr"):
            new,old_value=_number(current.get(key)),_number(previous.get(key))
            if new is not None and old_value not in (None,0):changes.append((new/old_value-1)*100)
        if changes and max(changes)>=8:return "POSITIVE","LOW","MATERIAL_UPGRADE"
        if changes and min(changes)<=-8:return "NEGATIVE","HIGH","MATERIAL_DOWNGRADE"
        break
    return None


def _records(value:Any)->list[dict[str,Any]]:
    if hasattr(value,"to_dict"):
        try:return [dict(x) for x in value.to_dict(orient="records")]
        except Exception:return []
    return [dict(x) for x in value] if isinstance(value,list) else []


def classify_text(text:str,source_api:str)->tuple[str,str,str]:
    if any(word in text for word in COMPLETION) and not any(word in text for word in ("新减持计划","拟继续减持")):
        return "NEUTRAL","LOW","减持完成不视为新增风险"
    negative=[x for x in NEGATIVE if x in text];positive=[x for x in POSITIVE if x in text]
    if negative:return "NEGATIVE",("HARD_BLOCK" if any(x in text for x in HARD) else "HIGH"),"、".join(negative)
    if source_api=="report_rc" and positive:return "POSITIVE","LOW","、".join(positive)
    if positive:return "POSITIVE","LOW","、".join(positive)
    return "NEUTRAL","UNKNOWN","未命中高置信事件类型"


def _published(row:Mapping[str,Any],fallback:datetime)->str:
    for key in ("datetime","pub_time","ann_date","trade_date","report_date","date","time"):
        raw=row.get(key)
        if raw not in (None,""):
            text=str(raw)
            for fmt in ("%Y%m%d %H:%M:%S","%Y-%m-%d %H:%M:%S","%Y%m%d","%Y-%m-%d"):
                try:return datetime.strptime(text,fmt).astimezone().isoformat(timespec="seconds")
                except ValueError:pass
    return fallback.isoformat(timespec="seconds")


def _universe(now:datetime)->dict[str,str]:
    names={}
    try:
        raw=PAID_DATA.call("stock_basic",cache_key=f"v8:event:stock_basic:{now.date()}",ttl_seconds=86400,
          priority="BACKGROUND",
          exchange="",list_status="L",fields="ts_code,name")
        for row in _records(raw):
            code=str(row.get("ts_code") or "").split(".")[0];name=str(row.get("name") or "").strip()
            if code and len(name)>=2:names[code]=name
    except Exception as exc:log_exception("event_stock_universe",exc)
    for row in list_positions():names[str(row.get("code") or "").zfill(6)]=str(row.get("name") or "")
    initialize();c=connect()
    for row in c.execute("select code,max(name) name from v8_signal_decisions group by code").fetchall():
        names[str(row["code"]).zfill(6)]=str(row["name"] or "")
    c.close();return names


def _map_security(row:Mapping[str,Any],text:str,universe:Mapping[str,str])->tuple[str|None,str|None]:
    raw=str(row.get("ts_code") or row.get("code") or "")
    match=re.search(r"(?<!\d)(\d{6})(?!\d)",raw+" "+text)
    if match:
        code=match.group(1);return code,universe.get(code) or str(row.get("name") or row.get("sec_name") or "") or None
    explicit=str(row.get("name") or row.get("sec_name") or "").strip()
    if explicit:
        for code,name in universe.items():
            if explicit==name:return code,name
    matches=[(code,name) for code,name in universe.items() if len(name)>=3 and name in text]
    return matches[0] if len(matches)==1 else (None,None)


def process_rows(source_api:str,rows:Iterable[Mapping[str,Any]],now:datetime,universe:Mapping[str,str],
                 holding_codes:set[str],candidate_codes:set[str])->list[dict[str,Any]]:
    results=[]
    for row in rows:
        title=str(row.get("title") or row.get("headline") or row.get("report_title") or row.get("content") or "").strip()
        summary=str(row.get("summary") or row.get("content") or "").strip()
        metadata=' '.join(str(row.get(key) or '') for key in ('report_type','classify','rating','recommend','industry'))
        text=f"{title} {summary[:500]} {metadata}"
        if not title:continue
        code,name=_map_security(row,text,universe)
        source_identity=_source_identity(row,source_api)
        source_fields={key:row.get(key) for key in ("org_name","author_name","report_type","classify","rating","last_rating",
          "recommend","target_price","tp","quarter","op_rt","op_pr","np","eps","pe") if row.get(key) not in (None,"")}
        report_change_kind=None
        if source_api=="report_rc":
            direction,risk,report_change_kind=_report_change(row,text);reason=report_change_kind
            if report_change_kind=="ROUTINE_RESEARCH" and code:
                historical=_historical_report_change(code,source_identity,source_fields)
                if historical:direction,risk,report_change_kind=historical;reason=report_change_kind
        else:
            # Composite news bodies often contain unrelated snippets.  Hard
            # risk classification is therefore anchored to the title unless
            # an exact security has already been supplied by the source.
            risk_text=f"{title} {summary[:180]}" if (row.get("ts_code") or row.get("code")) else title
            direction,risk,reason=classify_text(risk_text,source_api)
        # Neutral information with a meaningful precursor is retained for
        # hypothesis building, but it is not pushed as a directional signal.
        cues=extract_cues(text)
        topic=next((value for key,value in TOPICS.items() if key.lower() in text.lower()),None)
        classification_text=f"{title} {summary[:120]} {metadata}"
        metadata_event=classify_event_metadata(classification_text,source_api,row,code,topic)
        if risk=="HARD_BLOCK" and not code:
            risk="HIGH";reason=f"行业/宏观事件未明确映射个股：{reason}"
        if direction=="NEUTRAL" and not cues and metadata_event["primary_category"] in {
            "LOW_VALUE_OR_DUPLICATE","NO_A_SHARE_LINK","UNRESOLVED"}:continue
        if code in holding_codes and direction=="NEGATIVE" and risk in {"HIGH","HARD_BLOCK"}:alert="HOLDING_HARD_RISK"
        elif direction=="NEGATIVE":alert="EVENT_RISK"
        elif source_api=="report_rc":alert="REPORT_CHANGE" if report_change_kind!="ROUTINE_RESEARCH" else "REPORT_OBSERVATION"
        elif code in candidate_codes:alert="CANDIDATE_CATALYST"
        else:alert="MARKET_CATALYST"
        published=_published(row,now);identity=f"{source_api}|{published}|{title[:160]}|{code or topic or ''}"
        results.append({"event_id":hashlib.sha256(identity.encode("utf-8")).hexdigest(),"source_api":source_api,
          "source_identity":source_identity,"source_fields":source_fields,"report_change_kind":report_change_kind,
          "published_at":published,"fetched_at":now.isoformat(timespec="seconds"),"title":title[:180],
          "summary":summary[:500],"raw_body":summary,"code":code,"name":name,
          "topic":metadata_event.get("topic_primary") or topic,
          "direction":direction,"risk_level":risk,"alert_type":alert,"reason":reason,
          "cues":cues,"action_permission":"ANNOTATION_ONLY",**metadata_event})
    return results


def _push_eligible(event:Mapping[str,Any],hypothesis:Mapping[str,Any])->bool:
    """Only send information that has a usable A-share decision context."""
    kind=str(event.get("alert_type") or "")
    category=str(event.get("primary_category") or "")
    code=str(event.get("code") or "")
    topic=str(event.get("topic") or "")
    beneficiaries=[x for x in (event.get("beneficiaries") or []) if str(x.get("tier") or "") in {"A","B"}]
    affected=[x for x in (event.get("affected_companies") or []) if str(x.get("tier") or "") in {"A","B"}]
    if kind=="HOLDING_HARD_RISK":return True
    if category in {"NO_A_SHARE_LINK","LOW_VALUE_OR_DUPLICATE"}:return False
    if category=="UNRESOLVED":
        # A previously unmapped geopolitical/disaster clue may become useful
        # only after the causal layer derives sectors *and* A/B companies.
        high_impact=bool(set(str(x) for x in (event.get("cue_names") or [])) & {
          "SECURITY_ESCALATION","SUPPLY_DISRUPTION","EXTREME_WEATHER","PUBLIC_HEALTH","DEESCALATION"})
        return bool(high_impact and topic and (beneficiaries or affected) and
          str(hypothesis.get("state") or "") in {"HIGH_PROBABILITY","CONFIRMED","FADING","REVERSED"})
    if kind=="REPORT_OBSERVATION":return False
    if kind=="REPORT_CHANGE":return bool(code and event.get("report_change_kind") not in {None,"ROUTINE_RESEARCH"})
    if kind=="EVENT_RISK":return bool(code or (topic and affected))
    if kind=="CANDIDATE_CATALYST":return bool(code)
    if category=="NATIONAL_POLICY":
        return bool(topic and (beneficiaries or affected) and str(event.get("issuer_level") or "") in {
          "NATIONAL_CORE","MINISTRY_OFFICIAL","REGULATOR_OFFICIAL","LOCAL_OFFICIAL"})
    if kind=="MARKET_CATALYST":return bool(topic and (beneficiaries or affected))
    return should_push_hypothesis(hypothesis,False)


def backfill_event_taxonomy(path=None)->dict[str,int]:
    """Classify legacy V8 rows in place without changing their signal direction or alert type."""
    initialize(path);c=connect(path)
    rows=c.execute("select * from v8_event_radar where primary_category is null or primary_category='' ").fetchall()
    counts:dict[str,int]={}
    for raw in rows:
        row=dict(raw);details={}
        try:details=json.loads(row.get("details_json") or "{}")
        except (TypeError,json.JSONDecodeError):pass
        text=" ".join(str(x or "") for x in (row.get("title"),details.get("summary"),details.get("reason")))
        meta=classify_event_metadata(text,str(row.get("source_api") or ""),details,row.get("code"),row.get("topic"))
        category=str(meta["primary_category"]);counts[category]=counts.get(category,0)+1
        c.execute("""update v8_event_radar set primary_category=?,event_tags_json=?,related_entities_json=?,
          issuer=?,issuer_level=?,policy_stage=?,topic_primary=?,topic_secondary=?,fetched_at=coalesce(fetched_at,created_at),
          raw_body=coalesce(raw_body,?) where event_id=?""",(category,
          json.dumps(meta.get("event_tags") or [],ensure_ascii=False),
          json.dumps(meta.get("related_entities") or {},ensure_ascii=False,default=str),meta.get("issuer"),
          meta.get("issuer_level"),meta.get("policy_stage"),meta.get("topic_primary"),meta.get("topic_secondary"),
          details.get("summary") or "",row["event_id"]))
    c.commit();c.close();return counts


def scan_events(now:datetime|None=None,lookback_minutes:int=30,max_pushes:int|None=None)->dict[str,Any]:
    now=now or datetime.now().astimezone();start=now-timedelta(minutes=max(10,lookback_minutes));universe=_universe(now)
    holding_codes={str(x.get("code") or "").zfill(6) for x in list_positions()}
    initialize();c=connect();candidate_codes={str(x[0]).zfill(6) for x in c.execute(
      "select distinct code from v8_signal_decisions where signal_time>=?",((now-timedelta(days=3)).isoformat(timespec="seconds"),)).fetchall()};c.close()
    calls={
      "news":{"src":"sina","start_date":start.strftime("%Y-%m-%d %H:%M:%S"),"end_date":now.strftime("%Y-%m-%d %H:%M:%S")},
      "major_news":{"src":"sina","start_date":start.strftime("%Y-%m-%d %H:%M:%S"),"end_date":now.strftime("%Y-%m-%d %H:%M:%S")},
      "anns_d":{"start_date":start.strftime("%Y%m%d"),"end_date":now.strftime("%Y%m%d")},
      "report_rc":{"start_date":start.strftime("%Y%m%d"),"end_date":now.strftime("%Y%m%d")},
    }
    created=pushed=0;failures={};source_rows={};new_events=[]
    for api,kwargs in calls.items():
        try:
            all_rows=_records(PAID_DATA.call(api,cache_key=f"v8:event:{api}:{now:%Y%m%d%H%M}",ttl_seconds=300,
                                             priority="BACKGROUND",**kwargs))
            try:
                from .paid_month_archive import archive_rows
                archive_rows(api,all_rows,fetched_at=now)
            except Exception as archive_exc:
                log_exception("event_raw_archive",archive_exc,api=api)
            all_rows.sort(key=lambda item:_published(item,now),reverse=True)
            rows=all_rows[:CONFIG.event_max_rows_per_source]
            source_rows[api]=len(rows)
            for event in process_rows(api,rows,now,universe,holding_codes,candidate_codes):
                if not save_event_radar(event):continue
                created+=1
                hypothesis=infer_hypothesis(event,now)
                enriched=analyze_event({**event,**hypothesis},now)
                market_shadow=evaluate_event_market_shadow(enriched,now)
                enriched={**enriched,"market_shadow":market_shadow}
                governance=govern_event(enriched,now)
                hypothesis_ready=should_push_hypothesis(hypothesis,event.get("alert_type")=="HOLDING_HARD_RISK")
                explicit_ready=(event.get("alert_type") in {"REPORT_CHANGE","CANDIDATE_CATALYST"} or
                  (event.get("primary_category")=="NATIONAL_POLICY" and event.get("policy_stage") in {
                    "FORMAL_RELEASE","IMPLEMENTATION_RULES","FUNDING","ACTUAL_EXECUTION"}))
                if governance["allowed"] and (hypothesis_ready or explicit_ready) and _push_eligible(enriched,hypothesis):
                    new_events.append({**enriched,"governance":governance})
        except Exception as exc:
            failures[api]=type(exc).__name__;log_exception("event_scan",exc,api=api)
    priority={"HOLDING_HARD_RISK":0,"EVENT_RISK":1,"CANDIDATE_CATALYST":2,"REPORT_CHANGE":3,"MARKET_CATALYST":4}
    unique=[];seen=set()
    for event in sorted(new_events,key=lambda x:(priority.get(str(x.get('alert_type')),9),str(x.get('published_at') or '')),reverse=False):
        key=(str(event.get('title') or '')[:100],event.get('code'),event.get('direction'))
        if key in seen:continue
        seen.add(key);unique.append(event)
    push_limit=CONFIG.event_max_pushes_per_scan if max_pushes is None else max(1,max_pushes)
    for event in unique[:push_limit]:
        ok,_=send_once(f"EVENT|{event['event_id']}",build_event_message(event));pushed+=int(ok)
    status="OK" if not failures else ("DEGRADED" if len(failures)<len(calls) else "FAILED")
    detail={"status":status,"created":created,"unique":len(unique),"pushed":pushed,
            "suppressed":max(0,len(unique)-push_limit),"failures":failures,"source_rows":source_rows}
    save_module_health("EVENT_RADAR",status,now.isoformat(timespec="seconds"),detail);return detail


def run_event_schedule(now:datetime|None=None)->dict[str,Any]|None:
    now=now or datetime.now().astimezone();current=now.time().replace(tzinfo=None)
    if now.weekday()>=5 and not (time(8,20)<=current<=time(22,0)):return None
    slots=[]
    if time(8,25)<=current<=time(8,40):slots.append(("PREOPEN",720))
    if (time(9,30)<=current<=time(11,30) or time(13,0)<=current<=time(15,0)) and now.minute%10==0:
        slots.append((f"INTRADAY_{now:%H%M}",20))
    for label,target in (("POST1530",time(15,30)),("POST1800",time(18,0)),("POST2130",time(21,30))):
        if target<=current<=(datetime.combine(now.date(),target)+timedelta(minutes=10)).time():slots.append((label,180))
    if not slots:return None
    initialize();c=connect();results=[]
    for label,lookback in slots:
        key=f"{now.date()}|EVENT_SCAN|{label}"
        exists=c.execute("select 1 from v8_push_state where dedupe_key=? and status='SCAN_DONE'",(key,)).fetchone()
        if exists:continue
        result=scan_events(now,lookback);results.append(result)
        if result.get("status")=="OK":
            c.execute("""insert into v8_push_state(dedupe_key,status,attempts,last_attempt_at)
              values(?,'SCAN_DONE',1,?) on conflict(dedupe_key) do update set status='SCAN_DONE',
              attempts=v8_push_state.attempts+1,last_attempt_at=excluded.last_attempt_at""",
              (key,now.isoformat(timespec="seconds")));c.commit()
    c.close();return {"slots":len(results),"results":results}


def proactive_issue_audit(now:datetime|None=None)->tuple[bool,str]:
    now=now or datetime.now().astimezone();issues=[];initialize();backfill_event_taxonomy();c=connect()
    row=c.execute("select * from v8_module_health where module='EVENT_RADAR'").fetchone()
    if not row or str(row["checked_at"])[:10]!=now.date().isoformat():issues.append("事件雷达今天没有成功运行记录")
    elif row["status"]!="OK":issues.append(f"事件雷达处于{row['status']}，部分新闻/公告/研报可能延迟")
    state=c.execute("select count(*) from v8_miss_audit where trade_date=?",(now.date().isoformat(),)).fetchone()[0]
    if now.time().replace(tzinfo=None)>=time(15,40) and not state:issues.append("收盘强势股漏选审计尚无结果")
    profile_count=c.execute("select count(*) from v8_company_profiles").fetchone()[0]
    if profile_count<1000:issues.append(f"公司业务知识库不完整，目前仅{profile_count}家公司")
    total_hyp=c.execute("select count(*) from v8_event_radar where substr(created_at,1,10)=?",(now.date().isoformat(),)).fetchone()[0]
    unresolved=c.execute("select count(*) from v8_event_radar where substr(created_at,1,10)=? and primary_category='UNRESOLVED'",(now.date().isoformat(),)).fetchone()[0]
    if total_hyp>=5 and unresolved/total_hyp>.3:
        issues.append(f"事件识别覆盖不足：今日真正无法识别{unresolved}/{total_hyp}条，需要扩充规则")
    c.close()
    if not issues:return False,"no_issue"
    return send_once(f"{now.date()}|SOFTWARE_ISSUE_AUDIT",build_software_issue_message(issues))
