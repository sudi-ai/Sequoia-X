from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from .storage import connect, initialize


STATE_ORDER = {
    "CLUE": 0,
    "RISING": 1,
    "HIGH_PROBABILITY": 2,
    "CONFIRMED": 3,
    "SPREADING": 4,
    "FADING": 5,
    "CLOSED": 6,
    "REVERSED": 7,
}


@dataclass(frozen=True)
class Cue:
    name: str
    words: tuple[str, ...]
    mechanism: str
    weight: float
    direction: str = "NEUTRAL"


# These are generic mechanisms, not a list of events to predict.  New events are
# assembled from co-occurring mechanisms and independent sources.
CUES = (
    Cue("POLICY_TIGHTEN", ("制裁", "禁运", "限制出口", "加征关税", "监管收紧", "限产"), "政策约束增强", 12, "NEGATIVE"),
    Cue("POLICY_SUPPORT", ("补贴", "专项资金", "扩大内需", "支持政策", "放宽准入", "降息", "降准"), "政策支持增强", 12, "POSITIVE"),
    Cue("SECURITY_ESCALATION", ("撤侨", "军事部署", "空袭", "导弹", "交火", "进入战备", "领空关闭"), "安全风险升级", 18, "NEGATIVE"),
    Cue("SUPPLY_DISRUPTION", ("停产", "断供", "中断", "封锁", "港口关闭", "航道受阻", "供应短缺"), "供给或运输受阻", 16, "NEGATIVE"),
    Cue("PRICE_SHOCK", ("大幅涨价", "价格飙升", "创历史新高", "紧急调价", "运价上涨"), "价格出现异常变化", 10),
    Cue("DEMAND_SURGE", ("订单激增", "供不应求", "需求爆发", "排产提升", "销量大增", "紧急采购"), "终端需求上升", 14, "POSITIVE"),
    Cue("CAPACITY_CHANGE", ("扩产", "投产", "停工", "复产", "产能利用率"), "产能状态变化", 8),
    Cue("TECH_BREAKTHROUGH", ("技术突破", "首次实现", "获批上市", "商业化", "量产"), "技术或商业化进程变化", 12, "POSITIVE"),
    Cue("PUBLIC_HEALTH", ("疫情", "传染病", "病例激增", "公共卫生事件"), "公共卫生需求变化", 15, "NEGATIVE"),
    Cue("EXTREME_WEATHER", ("台风", "洪水", "干旱", "寒潮", "极端高温", "地震"), "天气或灾害冲击", 14, "NEGATIVE"),
    Cue("CORPORATE_RISK", ("立案调查", "债务违约", "重大诉讼", "退市风险", "业绩暴雷"), "公司风险暴露", 20, "NEGATIVE"),
    Cue("DEESCALATION", ("停火", "达成协议", "解除制裁", "恢复通航", "恢复供应", "风险缓和"), "风险溢价可能回落", 16),
    Cue("AUTHORITATIVE_DENIAL", ("官方辟谣", "权威辟谣", "不实消息", "消息不实", "予以否认"), "权威来源否认原事件", 30),
)

TRANSMISSION = {
    "SUPPLY_DISRUPTION": ["供给收缩", "现货/运价变化", "上游利润与下游成本分化"],
    "DEMAND_SURGE": ["需求增长", "订单与排产变化", "设备/材料/服务需求变化"],
    "POLICY_SUPPORT": ["政策支持", "投资或消费预期改善", "相关产业需求变化"],
    "POLICY_TIGHTEN": ["政策约束", "贸易/合规成本变化", "国产替代或需求转移"],
    "SECURITY_ESCALATION": ["风险偏好下降", "能源/运输/避险需求变化", "产业成本与订单重新分配"],
    "PRICE_SHOCK": ["关键价格变化", "收入与成本重新分配", "产业链利润分化"],
    "TECH_BREAKTHROUGH": ["技术可用性提高", "商业化进程加快", "设备与配套需求变化"],
    "EXTREME_WEATHER": ["生产/运输受扰", "供给与应急需求变化", "价格和订单变化"],
    "PUBLIC_HEALTH": ["公共卫生风险上升", "医疗/防护需求变化", "出行消费与供应链受扰"],
    "DEESCALATION": ["风险下降", "供应或运输恢复", "此前风险溢价可能反转"],
}


def extract_cues(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    found = []
    for cue in CUES:
        hits = [word for word in cue.words if word.lower() in lowered]
        if hits:
            found.append({"name": cue.name, "hits": hits, "mechanism": cue.mechanism,
                          "weight": cue.weight, "direction": cue.direction})
    return found


def canonical_key(text: str, code: str | None = None) -> str:
    cleaned = re.sub(r"[\W_]+", "", text.lower())
    # Remove common news boilerplate and retain a stable semantic fingerprint.
    for token in ("最新", "消息", "公告", "报道", "公司", "关于", "今日", "目前"):
        cleaned = cleaned.replace(token, "")
    fingerprint = f"{code or ''}|{cleaned[:72]}"
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]


def _tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text.lower())
    return {cleaned[i:i+3] for i in range(max(0, len(cleaned)-2))}


def _similarity(left: str, right: str) -> float:
    a,b=_tokens(left),_tokens(right)
    jaccard=len(a & b)/len(a | b) if a and b else 0.0
    normalized=lambda x:re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+","",x.lower())
    sequence=SequenceMatcher(None,normalized(left),normalized(right)).ratio()
    return max(jaccard,sequence)


def _resolve_cluster(c, text: str, code: str | None, topic: str | None, cutoff: str) -> tuple[str,float]:
    default=canonical_key(text,code);best_key,best_score=default,0.0
    rows=c.execute("""select e.cluster_key,e.title,e.details_json,r.topic from v8_event_evidence e
      left join v8_event_radar r on r.event_id=e.event_id where e.observed_at>=? order by e.observed_at desc limit 500""",(cutoff,)).fetchall()
    for row in rows:
        details={}
        try:details=json.loads(row["details_json"] or "{}")
        except (TypeError,json.JSONDecodeError):pass
        old_code=str(details.get("code") or "") or None;old_topic=str(row["topic"] or "") or None
        if code and old_code and code!=old_code:continue
        if topic and old_topic and topic!=old_topic:continue
        score=_similarity(text,str(row["title"] or ""))
        if score>best_score:best_key,best_score=str(row["cluster_key"]),score
    return (best_key,best_score) if best_score>=0.62 else (default,best_score)


def _state(probability: float, source_count: int, confirmed: bool, fading: bool) -> str:
    if fading:
        return "FADING"
    if confirmed:
        return "CONFIRMED"
    if probability >= 72 and source_count >= 2:
        return "HIGH_PROBABILITY"
    if probability >= 48:
        return "RISING"
    return "CLUE"


def infer_hypothesis(event: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Cluster evidence and update a probabilistic, auditable event hypothesis."""
    now = now or datetime.now().astimezone()
    text = f"{event.get('title', '')} {event.get('summary', '')} {event.get('reason', '')}".strip()
    cues = extract_cues(text)
    code=str(event.get("code") or "") or None
    source_api = str(event.get("source_api") or "unknown")
    source = str(event.get("source_identity") or source_api)
    initialize()
    c = connect()
    cutoff = (now - timedelta(hours=72)).isoformat(timespec="seconds")
    cluster_key,semantic_similarity=_resolve_cluster(c,text,code,str(event.get("topic") or "") or None,cutoff)
    rows = c.execute(
        "select * from v8_event_evidence where cluster_key=? and observed_at>=? order by observed_at",
        (cluster_key, cutoff),
    ).fetchall()
    evidence = [dict(row) for row in rows]
    if not any(str(row.get("event_id")) == str(event.get("event_id")) for row in evidence):
        c.execute(
            """insert or ignore into v8_event_evidence
               (event_id,cluster_key,source_api,published_at,observed_at,title,cues_json,direction,details_json)
               values(?,?,?,?,?,?,?,?,?)""",
            (event.get("event_id"), cluster_key, source_api, event.get("published_at"), now.isoformat(timespec="seconds"),
             event.get("title"), json.dumps(cues, ensure_ascii=False), event.get("direction"),
             json.dumps(dict(event), ensure_ascii=False, default=str)),
        )
        c.commit()
        evidence.append({"event_id": event.get("event_id"), "source_api": source_api,
                         "details_json": json.dumps(dict(event),ensure_ascii=False,default=str),
                         "cues_json": json.dumps(cues, ensure_ascii=False)})

    sources_set=set()
    for row in evidence:
        details={}
        try:details=json.loads(row.get("details_json") or "{}")
        except (TypeError,json.JSONDecodeError):pass
        sources_set.add(str(details.get("source_identity") or row.get("source_api") or "unknown"))
    sources = sorted(sources_set)
    all_cues: dict[str, dict[str, Any]] = {}
    for row in evidence:
        raw = row.get("cues_json") or "[]"
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            items = []
        for item in items:
            all_cues[str(item.get("name"))] = item

    support = [item["mechanism"] for item in all_cues.values() if item.get("name") != "DEESCALATION"]
    contrary = [item["mechanism"] for item in all_cues.values() if item.get("name") == "DEESCALATION"]
    source_bonus = min(20, max(0, len(sources) - 1) * 8)
    cue_score = min(50, sum(float(item.get("weight") or 0) for item in all_cues.values()))
    probability = min(92, 18 + cue_score + source_bonus)
    denied="AUTHORITATIVE_DENIAL" in all_cues and str(event.get("issuer_level") or "") in {"NATIONAL_CORE","MINISTRY_OFFICIAL","REGULATOR_OFFICIAL","LOCAL_OFFICIAL"}
    confirmed = any(name in all_cues for name in ("CORPORATE_RISK",)) or (
        len(sources) >= 2 and any(name in all_cues for name in ("SUPPLY_DISRUPTION", "SECURITY_ESCALATION", "DEMAND_SURGE"))
    )
    fading = "DEESCALATION" in all_cues
    authority=str(event.get("issuer_level") or "")
    if authority in {"NATIONAL_CORE","MINISTRY_OFFICIAL","REGULATOR_OFFICIAL"}:probability=min(96,probability+18)
    state = "REVERSED" if denied else _state(probability, len(sources), confirmed, fading)
    previous = c.execute("select state from v8_event_clusters where cluster_key=?", (cluster_key,)).fetchone()
    previous_state = str(previous[0]) if previous else None
    chains = []
    for name in all_cues:
        for node in TRANSMISSION.get(name, []):
            if node not in chains:
                chains.append(node)
    record = {
        "cluster_key": cluster_key,
        "state": state,
        "previous_state": previous_state,
        "probability": probability,
        "confidence": "HIGH" if probability >= 72 and len(sources) >= 2 else ("MEDIUM" if probability >= 48 else "LOW"),
        "source_count": len(sources),
        "sources": sources,
        "evidence_count": len(evidence),
        "supporting_evidence": support,
        "cue_names": sorted(all_cues),
        "contrary_evidence": contrary,
        "next_checks": ["等待独立来源确认", "检查商品/板块/资金是否共振", "检查价格是否已充分反映"],
        "generic_chain": chains[:6],
        "state_changed": previous_state is not None and previous_state != state,
        "semantic_similarity": round(semantic_similarity,3),
        "authority_level": authority or "UNKNOWN",
        "policy_stage": event.get("policy_stage"),
    }
    c.execute(
        """insert into v8_event_clusters(cluster_key,topic,state,probability,source_count,evidence_count,
           first_seen_at,last_seen_at,support_json,contrary_json,next_checks_json,details_json)
           values(?,?,?,?,?,?,?,?,?,?,?,?) on conflict(cluster_key) do update set
           topic=excluded.topic,state=excluded.state,probability=excluded.probability,
           source_count=excluded.source_count,evidence_count=excluded.evidence_count,last_seen_at=excluded.last_seen_at,
           support_json=excluded.support_json,contrary_json=excluded.contrary_json,
           next_checks_json=excluded.next_checks_json,details_json=excluded.details_json""",
        (cluster_key, event.get("topic"), state, probability, len(sources), len(evidence),
         now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"),
         json.dumps(support, ensure_ascii=False), json.dumps(contrary, ensure_ascii=False),
         json.dumps(record["next_checks"], ensure_ascii=False), json.dumps(record, ensure_ascii=False)),
    )
    c.commit()
    c.close()
    return record


def should_push_hypothesis(result: Mapping[str, Any], holding_hard_risk: bool = False) -> bool:
    if holding_hard_risk:
        return True
    state = str(result.get("state") or "CLUE")
    if state in {"HIGH_PROBABILITY", "CONFIRMED", "FADING", "REVERSED"}:
        return bool(result.get("state_changed")) or result.get("previous_state") is None
    return False
