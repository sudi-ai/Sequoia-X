"""Compact research funnel for low-position, event, portfolio and pullback states."""
import hashlib
import json

VERSION = 'ACTION_FUNNEL_1'


def _key(row):
    return str(row.get('ts_code') or row.get('code') or '')


def _rank(row):
    evidence = row.get('evidence') or {}
    structure = evidence.get('structure') or row.get('structure') or {}
    aligned = sum((evidence.get(k) or {}).get('status') == 'SUPPORTS'
                  for k in ('business', 'demand', 'flow', 'sector'))
    position = structure.get('position60')
    contraction = structure.get('volume_contraction')
    # Transparent ordering metric, deliberately not a probability or recommendation score.
    return (-aligned, float(position) if position is not None else 9,
            float(contraction) if contraction is not None else 9, _key(row))


def build_action_center(candidates, event_status, portfolio):
    event_rows = list((event_status or {}).get('candidates') or [])
    core_rows = list(candidates or [])
    low = [r for r in core_rows if r.get('state') == 'WATCH' and
           ('LATENT_BASE' in (r.get('matched_families') or [r.get('family')]))]
    low += [r for r in event_rows if r.get('state') in ('LOW_POSITION_WATCH', 'EVIDENCE_ALIGNED')]
    seen, ranked = set(), []
    for row in sorted(low, key=_rank):
        code = _key(row)
        if code and code not in seen:
            seen.add(code); ranked.append(row)
    pullbacks = []
    breakdowns = []
    for row in core_rows:
        evidence = row.get('evidence') or {}
        structure = evidence.get('structure') or {}
        sequence = evidence.get('pullback_sequence') or {}
        price, support = row.get('price'), structure.get('support')
        if (row.get('state') == 'WATCH' and sequence.get('confirmed') and
                'TREND_PULLBACK' in (row.get('matched_families') or [row.get('family')])):
            pullbacks.append(dict(row, pattern_label='疑似洗盘／回踩收复', intent_confirmed=False))
        if price is not None and support is not None and float(price) < float(support):
            breakdowns.append(dict(row, pattern_label='支撑破位'))
    for row in event_rows:
        if row.get('state') in ('FADING', 'BLOCKED'):
            breakdowns.append(dict(row, pattern_label='结构走弱或风险否决'))
    position_items = list((portfolio or {}).get('items') or [])
    urgent = [r for r in position_items if r.get('stage') in ('STOP_REVIEW', 'TARGET_REVIEW', 'WAIT_DATA')]
    actionable = []  # Reserved: no current rule grants execution eligibility.
    status = 'NO_ACTION' if not actionable else 'REVIEW_REQUIRED'
    return {
        'version': VERSION, 'status': status, 'execution_eligible': False,
        'actionable': actionable, 'actionable_count': 0,
        'low_position_count': len(ranked), 'low_position': ranked[:5],
        'event_count': len(event_rows),
        'pullback_count': len(pullbacks), 'possible_washout': sorted(pullbacks,key=_rank)[:5],
        'breakdown_count': len(breakdowns), 'breakdowns': breakdowns[:5],
        'portfolio_status': (portfolio or {}).get('status','NO_NEW_VERSION_POSITIONS'),
        'portfolio_items': position_items, 'portfolio_attention': urgent,
        'policy': '最多展示5只研究候选；疑似洗盘不代表主力意图；没有完整证据时行动标的为0。'
    }


def notify_change(result, operations, now, enabled):
    compact = {k: result[k] for k in ('status','low_position_count','pullback_count','breakdown_count','portfolio_status')}
    compact['low_codes'] = [_key(x) for x in result['low_position']]
    compact['pullback_codes'] = [_key(x) for x in result['possible_washout']]
    fingerprint = hashlib.sha256(json.dumps(compact,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    if operations.get('action_funnel') == fingerprint:
        return 'UNCHANGED'
    operations.set('action_funnel',fingerprint)
    if not enabled:
        return 'HELD'
    # Empty/closed states stay on the workbench; avoid repeated no-action chat messages.
    if not (result['low_position_count'] or result['pullback_count'] or result['breakdown_count'] or result['portfolio_attention']):
        return 'NO_MESSAGE'
    lines=['🧭 A股机会雷达｜研究漏斗更新',
           '行动标的：0只（执行资格未开放）',
           '低位研究：'+str(result['low_position_count'])+'只｜疑似回踩收复：'+str(result['pullback_count'])+'只',
           '破位/风险：'+str(result['breakdown_count'])+'只｜持仓状态：'+str(result['portfolio_status']),
           '前列研究：'+'、'.join((_key(x)+' '+str(x.get('name') or '')) for x in result['low_position'][:5]) if result['low_position'] else '前列研究：无',
           '“疑似洗盘”只描述价格结构，不确认主力意图；请在工作台核对证据、有效期和失效条件。',
           'Shadow｜不自动下单｜不构成投资建议']
    return operations.emit('ACTION_FUNNEL:'+fingerprint,'ACTION_FUNNEL','\n'.join(lines),now,True)
