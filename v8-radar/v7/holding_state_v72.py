from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .config import V72_CONFIG
from .portfolio import ensure_schema
from .signal_lab import DEFAULT_DB, _LOCK, _connect

PRIORITY = {'HARD_RISK':100,'TRUE_WEAKNESS':80,'EXHAUSTION':60,'WASHOUT':40,'MAIN_RISE':20,'SECOND_STRENGTH':15,'NEUTRAL':0}


def _num(v: Any, default: float | None = None) -> float | None:
    try: return float(v)
    except Exception: return default


def classify_holding(e: Mapping[str, Any]) -> dict[str, Any]:
    ann = str(e.get('announcement_risk_level') or 'UNKNOWN')
    if ann in {'HARD_BLOCK','HIGH'}:
        return {'state':'HARD_RISK','confidence':1.0,'reasons':['重大公告风险'], 'evidence_strength':'HIGH'}
    fresh = bool(e.get('data_fresh', False)); missing = list(e.get('missing_fields') or [])
    if not fresh:
        return {'state':'NEUTRAL','confidence':0.25,'reasons':['关键实时数据不新鲜'], 'evidence_strength':'LOW', 'missing_fields':missing}
    td=_num(e.get('true_decline_risk_similarity'),0) or 0; wash=_num(e.get('washout_similarity'),0) or 0; main=_num(e.get('main_rise_similarity'),0) or 0
    exhaustion=_num(e.get('exhaustion_score'),0) or 0; sector=_num(e.get('sector_strength'))
    slope=_num(e.get('ma20_slope10'))
    if slope is None: slope=_num(e.get('ma20_slope'))
    gap=_num(e.get('ma20_gap_pct')); minute_quality=str(e.get('minute_quality') or 'UNKNOWN').upper()
    news_risk=str(e.get('news_risk_level') or 'UNKNOWN').upper(); chip_pressure=bool(e.get('chip_pressure_high'))
    blow=bool(e.get('blowoff_reversal')); reasons=[]
    # Similarity alone is never a sell signal. Require at least two explicit,
    # non-missing corroborating observations. Missing sector/slope is neutral.
    weakness=[]
    if slope is not None and slope<0: weakness.append('MA20斜率向下')
    if gap is not None and gap<0: weakness.append('价格位于MA20下方')
    if sector is not None and sector<.45: weakness.append('板块转弱')
    if minute_quality=='BAD' or blow: weakness.append('分钟结构转弱')
    if news_risk in {'MEDIUM','HIGH'}: weakness.append('新闻风险上升')
    if chip_pressure: weakness.append('筹码获利压力偏高')
    if td>=.72 and len(weakness)>=2:
        reasons=['真实下跌风险相似度高',*weakness]
        confidence=min(.95,max(td,.72+min(.18,.04*len(weakness))))
        return {'state':'TRUE_WEAKNESS','confidence':confidence,'reasons':reasons,'evidence_strength':'HIGH'}
    if exhaustion>=.68 or (blow and main<.6):
        return {'state':'EXHAUSTION','confidence':max(.68,exhaustion),'reasons':['高位推进效率下降/冲高回落'], 'evidence_strength':'MEDIUM'}
    if wash>=.68 and td<.55 and (gap is None or gap>=-3):
        return {'state':'WASHOUT','confidence':wash,'reasons':['健康洗盘相似度较高且真实下跌风险未同步升高'],'evidence_strength':'MEDIUM'}
    if main>=.68 and td<.5 and ((slope is not None and slope>0) or (gap is not None and gap>=0)):
        return {'state':'MAIN_RISE','confidence':main,'reasons':['主升延续证据占优'],'evidence_strength':'MEDIUM'}
    return {'state':'NEUTRAL','confidence':.5,'reasons':['证据未形成一致方向'],'evidence_strength':'LOW'}


def _stamp(v: str | None = None) -> str:
    return v or datetime.now().astimezone().isoformat(timespec='seconds')


def get_holding_state(position_id: str, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); r=c.execute('select * from v72_holding_state_current where position_id=?',(position_id,)).fetchone(); c.close(); return dict(r) if r else None


def observe_holding_state(position_id: str, classification: Mapping[str, Any], *, evidence: Mapping[str, Any] | None = None,
                          data_time: str | None = None, observed_at: str | None = None, db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Persist state with two-round anti-flicker confirmation except hard risk.

    TRUE_WEAKNESS cannot jump directly back to MAIN_RISE; it must first recover to
    NEUTRAL/SECOND_STRENGTH on a later confirmed observation.
    """
    ensure_schema(db_path); now=_stamp(observed_at); proposed=str(classification.get('state') or 'NEUTRAL')
    conf=float(classification.get('confidence') or 0); reasons=list(classification.get('reasons') or []); strength=str(classification.get('evidence_strength') or 'LOW')
    immediate = proposed == 'HARD_RISK'
    required = 1 if immediate else V72_CONFIG.state_confirm_rounds
    with _LOCK:
        c=_connect(db_path); cur=c.execute('select * from v72_holding_state_current where position_id=?',(position_id,)).fetchone()
        if not cur:
            current='NEUTRAL'; pending=None; count=0
            c.execute('insert into v72_holding_state_current(position_id,current_state,pending_state,pending_count,last_update_at) values(?,?,?,?,?)',
                      (position_id,current,None,0,now))
        else:
            current=str(cur['current_state']); pending=cur['pending_state']; count=int(cur['pending_count'] or 0)
        if current == 'TRUE_WEAKNESS' and proposed == 'MAIN_RISE':
            proposed = 'NEUTRAL'; reasons = ['真实转弱后需先完成独立恢复确认，禁止一次反弹直接跳到主升延续']
        if proposed == current:
            c.execute('update v72_holding_state_current set pending_state=NULL,pending_count=0,last_update_at=?,state_confidence=?,evidence_strength=?,reason_json=?,evidence_json=?,data_time=? where position_id=?',
                      (now,conf,strength,json.dumps(reasons,ensure_ascii=False),json.dumps(dict(evidence or {}),ensure_ascii=False,default=str),data_time,position_id)); c.commit()
            r=c.execute('select * from v72_holding_state_current where position_id=?',(position_id,)).fetchone(); c.close(); return {**dict(r),'changed':False,'confirmed':True}
        if pending == proposed: count += 1
        else: pending = proposed; count = 1
        if count < required:
            first_seen = cur['first_seen_at'] if cur and cur['pending_state']==proposed else now
            c.execute('update v72_holding_state_current set pending_state=?,pending_count=?,first_seen_at=?,last_update_at=?,state_confidence=?,evidence_strength=?,reason_json=?,evidence_json=?,data_time=? where position_id=?',
                      (pending,count,first_seen,now,conf,strength,json.dumps(reasons,ensure_ascii=False),json.dumps(dict(evidence or {}),ensure_ascii=False,default=str),data_time,position_id)); c.commit()
            r=c.execute('select * from v72_holding_state_current where position_id=?',(position_id,)).fetchone(); c.close(); return {**dict(r),'changed':False,'confirmed':False}
        from_state=current; confirmed_at=now; first_seen = cur['first_seen_at'] if cur and cur['pending_state']==proposed else now
        c.execute('update v72_holding_state_current set current_state=?,pending_state=NULL,pending_count=0,first_seen_at=NULL,confirmed_at=?,last_update_at=?,state_confidence=?,evidence_strength=?,reason_json=?,evidence_json=?,data_time=? where position_id=?',
                  (proposed,confirmed_at,now,conf,strength,json.dumps(reasons,ensure_ascii=False),json.dumps(dict(evidence or {}),ensure_ascii=False,default=str),data_time,position_id))
        c.execute('insert into v72_holding_state_history(position_id,from_state,to_state,first_seen_at,confirmed_at,confirmation_count,state_confidence,evidence_strength,reason_json,evidence_json,data_time) values(?,?,?,?,?,?,?,?,?,?,?)',
                  (position_id,from_state,proposed,first_seen,confirmed_at,count,conf,strength,json.dumps(reasons,ensure_ascii=False),json.dumps(dict(evidence or {}),ensure_ascii=False,default=str),data_time))
        c.commit(); r=c.execute('select * from v72_holding_state_current where position_id=?',(position_id,)).fetchone(); c.close(); return {**dict(r),'changed':True,'confirmed':True,'from_state':from_state,'to_state':proposed}


def holding_history(position_id: str, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    ensure_schema(db_path)
    with _LOCK:
        c=_connect(db_path); rows=[dict(r) for r in c.execute('select * from v72_holding_state_history where position_id=? order by id',(position_id,)).fetchall()]; c.close(); return rows
