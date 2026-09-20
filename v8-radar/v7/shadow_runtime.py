from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .config import CONFIG, V72_CONFIG
from .experimental_engine import ENGINE_VERSION, evaluate_candidate
from .paid_enrichment import enrich_candidate
from .signal_lab import initialize, record_signal
from .shadow_state_runtime import register_watch

_initialized = False
V6_VERSION = "V6.6-legacy-baseline"
FILTER_VERSION = "V6.6+V7.1-risk-quality-filter"


def _ensure_init():
    global _initialized
    if not _initialized:
        initialize(); _initialized = True


def _record_pair(candidate: Mapping[str, Any], sector_item: Mapping[str, Any], market_regime: str,
                 *, source: str, stamp: str, sector: str = "", use_paid_enrichment: bool = False) -> tuple[int, int, int, int]:
    code = str(candidate.get("code") or "").split(".")[0].zfill(6)
    if not code.strip("0"):
        return 0, 0, 0, 0
    deep = candidate.get("_v64") or {}
    v6_score = deep.get("buy_score", candidate.get("score"))
    v6_grade = "确认" if deep.get("confirmed") else "观察"
    common_snapshot = {
        "snapshot_time": stamp, "market_regime": market_regime, "sector": sector,
        "sector_score": sector_item.get("score"), "sector_level": sector_item.get("level"),
        "sector_accel": sector_item.get("accel"), "sector_breadth": sector_item.get("breadth"),
        "candidate_pct": candidate.get("pct"), "candidate_move": candidate.get("move"),
        "candidate_amount_delta": candidate.get("amount_delta"), "candidate_stage": candidate.get("stage"),
        "v64_deep": deep, "risk_context": candidate.get("_v66_risk") or candidate.get("risk_context") or {},
    }
    event_suffix = f"{stamp[:16]}|{source}|{sector}|{code}"
    a = record_signal(
        engine="V6_LEGACY", strategy_version=V6_VERSION, code=code, name=str(candidate.get("name") or ""),
        sector=sector, price=candidate.get("price"), grade=v6_grade, score=v6_score, source=source,
        snapshot=common_snapshot, evidence={"basis": "legacy_output", "strategy_version": V6_VERSION},
        signal_time=stamp, event_key=f"V6|{event_suffix}")
    paid = {}
    enrichment_failed = 0
    if use_paid_enrichment:
        try:
            paid = enrich_candidate(code)
        except Exception:
            paid = {"status": "DATA_UNAVAILABLE"}
            enrichment_failed = 1
    v7 = evaluate_candidate(candidate, sector_item, market_regime, paid_factors=paid)
    b = record_signal(
        engine="V7_SHADOW", strategy_version=ENGINE_VERSION, code=code, name=str(candidate.get("name") or ""),
        sector=sector, price=candidate.get("price"), grade=v7["grade"], score=v7["score"], source=source,
        snapshot=common_snapshot, evidence=v7, signal_time=stamp, event_key=f"V7|{event_suffix}")
    # V7.2 RESEARCH_2000 is an independent Shadow track. It never changes V6/V7 CURRENT output.
    if V72_CONFIG.research_enable and use_paid_enrichment:
        try:
            from .portfolio_runtime_v72 import fetch_daily_features
            from .research_v72 import evaluate_research, RESEARCH_RULE_VERSION
            from .forward_v72 import freeze_snapshot, get_or_create_episode
            from .signal_lab import _LOCK, _connect, DEFAULT_DB
            from .runtime_log_v72 import log_event
            features = fetch_daily_features(code)
            features["board_type"] = "科创板" if code.startswith("688") else ("创业板" if code.startswith(("300","301")) else "主板")
            features["market_regime"] = market_regime
            research = evaluate_research(features)
            episode = get_or_create_episode(code, stamp[:10], direction="LONG", signal_type="FIRST_SIGNAL")
            try:
                with _LOCK:
                    conn = _connect(DEFAULT_DB)
                    try:
                        conn.execute("""INSERT OR IGNORE INTO v72_research_snapshots
                            (signal_id,code,observed_at,v66_score,current_v71_score,research_2000_score,feature_json,research_json,
                             research_rule_version,research_feature_version,research_sample_version,research_config_hash)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (int(b["id"]),code,stamp,v6_score,v7.get("score"),research.get("research_2000_score"),
                             __import__('json').dumps(features,ensure_ascii=False,default=str),__import__('json').dumps(research,ensure_ascii=False,default=str),
                             research.get("research_rule_version"),research.get("research_feature_version"),research.get("research_sample_version"),research.get("research_config_hash")))
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as exc:
                log_event("research_snapshot", "Research快照写库失败", code=code, exc=exc, degraded=True, event="research_snapshot_write")
            price = candidate.get("price")
            if price not in (None, ""):
                try:
                    freeze_snapshot(code=code, signal_time=stamp, entry_reference_price=float(price), features=features, research=research,
                                    current_score=v7.get("score"), research_score=research.get("research_2000_score"), board_type=features.get("board_type"),
                                    market_regime=market_regime, episode_id=episode.get("episode_id"))
                except Exception as exc:
                    log_event("forward_freeze", "Forward快照冻结失败", code=code, exc=exc, degraded=True, event="forward_snapshot_freeze")
            record_signal(engine="V72_RESEARCH", strategy_version=RESEARCH_RULE_VERSION, code=code, name=str(candidate.get("name") or ""),
                          sector=sector, price=candidate.get("price"), grade="RESEARCH_ONLY", score=research.get("research_2000_score"), source=source,
                          snapshot={**common_snapshot,"research_features":features}, evidence=research, signal_time=stamp, event_key=f"V72R|{event_suffix}")
        except Exception as exc:
            try:
                from .runtime_log_v72 import log_event
                log_event("research_pipeline", "Research流程降级失败", code=code, exc=exc, degraded=True, event="research_pipeline")
            except Exception:
                pass
    # Persist V7.1 Shadow candidate state independently. Failure must never affect the V6.6 formal flow.
    try:
        register_watch(trade_date=stamp[:10], candidate=candidate, sector_item=sector_item, market_regime=market_regime,
                       paid=paid, changed_at=stamp)
    except Exception as exc:
        try:
            from .runtime_log_v72 import log_event
            log_event("candidate_watch", "V7候选WATCH登记失败", code=code, exc=exc, degraded=True, event="candidate_watch")
        except Exception:
            pass
    c_inserted = 0
    if v7.get("pass_all_gates"):
        c = record_signal(
            engine="V6_V7_FILTERED", strategy_version=FILTER_VERSION, code=code,
            name=str(candidate.get("name") or ""), sector=sector, price=candidate.get("price"),
            grade=v6_grade, score=v6_score, source=source, snapshot=common_snapshot,
            evidence={"basis": "legacy_output_after_v7_risk_quality_filter", "strategy_version": FILTER_VERSION,
                      "v7_filter": {"pass": True, "veto_reasons": [], "quality_filter_reasons": []}},
            signal_time=stamp, event_key=f"FILTER|{event_suffix}")
        c_inserted = int(c["inserted"])
    return int(a["inserted"]), int(b["inserted"]), c_inserted, enrichment_failed


def _v6_rank(candidate: Mapping[str, Any]) -> float:
    deep=candidate.get("_v64") or {}
    try: return float(deep.get("buy_score",candidate.get("score",0)) or 0)
    except Exception: return 0.0


def capture_parallel_signals(radar: Sequence[Mapping[str, Any]], market_regime: str, *,
                             source: str = "板块共振", signal_time: str | None = None) -> dict[str, int]:
    if not CONFIG.shadow_enabled:
        return {"enabled": 0, "v6": 0, "v7": 0, "filtered": 0}
    _ensure_init(); stamp = signal_time or datetime.now().astimezone().isoformat(timespec="seconds")
    counts = {"enabled": 1, "v6": 0, "v7": 0, "filtered": 0,"original_candidates":0,
              "enriched_candidates":0,"skipped_candidates":0,"enrichment_failures":0}
    flattened=[]
    for item in radar or []:
        sector = str(item.get("sector") or "")
        candidates = item.get("_v66_raw_candidates") or item.get("candidates") or []
        flattened.extend((candidate,item,sector) for candidate in candidates)
    paid_ids={id(x[0]) for x in sorted(flattened,key=lambda x:_v6_rank(x[0]),reverse=True)[:CONFIG.top_candidates_for_enrichment]}
    counts["original_candidates"]=len(flattened)
    for candidate,item,sector in flattened:
        use_paid=id(candidate) in paid_ids
        a,b,c,failed = _record_pair(candidate,item,market_regime,source=source,stamp=stamp,sector=sector,
                                    use_paid_enrichment=use_paid)
        counts["enriched_candidates"] += int(use_paid); counts["enrichment_failures"] += failed
        counts["v6"] += a; counts["v7"] += b; counts["filtered"] += c
    counts["skipped_candidates"]=counts["original_candidates"]-counts["enriched_candidates"]
    return counts


def capture_candidate_batch(candidates: Sequence[Mapping[str, Any]], market_regime: str, *, source: str,
                            signal_time: str | None = None, context: Mapping[str, Any] | None = None) -> dict[str, int]:
    if not CONFIG.shadow_enabled:
        return {"enabled": 0, "v6": 0, "v7": 0, "filtered": 0}
    _ensure_init(); stamp = signal_time or datetime.now().astimezone().isoformat(timespec="seconds")
    counts = {"enabled": 1, "v6": 0, "v7": 0, "filtered": 0,"original_candidates":len(candidates or []),
              "enriched_candidates":0,"skipped_candidates":0,"enrichment_failures":0}
    pseudo = dict(context or {}); pseudo.setdefault("sector", "")
    paid_ids={id(x) for x in sorted(candidates or [],key=_v6_rank,reverse=True)[:CONFIG.top_candidates_for_enrichment]}
    for candidate in candidates or []:
        use_paid = id(candidate) in paid_ids
        a,b,c,failed = _record_pair(candidate, pseudo, market_regime, source=source, stamp=stamp,
                             sector=str(pseudo.get("sector") or ""), use_paid_enrichment=use_paid)
        counts["enriched_candidates"] += int(use_paid); counts["enrichment_failures"] += failed
        counts["v6"] += a; counts["v7"] += b; counts["filtered"] += c
    counts["skipped_candidates"]=counts["original_candidates"]-counts["enriched_candidates"]
    return counts
