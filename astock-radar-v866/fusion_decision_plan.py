"""Research decision contract. No legacy writes, orders, or probability claims."""
import datetime as dt

from fusion_engine import number, timestamp

DESIGN_VERSION = 'RESEARCH_DESK_3'


def build_plan(result, now):
    evidence = result.get('evidence') or {}
    structure = evidence.get('structure') or {}
    family = result.get('family', '')
    state = result.get('state', 'WAIT_DATA')
    observed = timestamp(result.get('observed_at'))
    sourced = timestamp(result.get('source_trade_time'))
    valid_until = min(observed, sourced) + dt.timedelta(seconds=180) if observed and sourced else None
    fresh = bool(valid_until and now <= valid_until and sourced.date() == now.date()
                 and observed.date() == now.date() and sourced <= now + dt.timedelta(seconds=30)
                 and observed <= now + dt.timedelta(seconds=30))
    reasons = []
    condition = '等待数据与结构证据完整，不生成买入条件'
    if family == 'LATENT_BASE':
        reasons = ['60日区间位置不高于60%，20日振幅不高于18%',
                   '20日均线不下降，近期低点保持，成交量收缩条件成立']
        condition = '继续核对平台上沿、板块相对强度与公告；当前规则只识别蓄势，不确认突破买点'
    elif family == 'TREND_PULLBACK':
        reasons = ['趋势均线条件成立', '已观察到先回撤、后收复前高和VWAP的时间序列']
        condition = '复核回踩收复是否持续；VWAP单位尚需接口契约核验，不把本次收复直接当买点'
    elif family == 'EARLY_IGNITION':
        reasons = ['连续三个独立时点价格上升', '成交额速率加速度达到研究阈值1.2']
        condition = '核对后续持续性、板块共振和可成交性；短时加速不等于趋势确定'
    levels = {k: number(structure.get(k)) for k in ('support', 'resistance', 'ma20', 'ma60')}
    levels['vwap'] = number(evidence.get('vwap'))
    blockers = ['真实样本外验证未完成', '成交与组合风险门槛未接通',
                'VWAP成交量单位尚未通过固定接口契约核验']
    if result.get('event_coverage') != 'VERIFIED':
        blockers.insert(0, '公告风险覆盖未核验')
    if not fresh:
        blockers.insert(0, '行情时间缺失或过期')
    if state == 'BLOCKED':
        blockers.insert(0, '已确认风险否决')
    active = state == 'WATCH' and fresh
    return {
        'design_version': DESIGN_VERSION,
        'action': '仅观察，不买入' if active else '暂停参考，不新增决策',
        'execution_eligible': False,
        'why': reasons if active else ['当前不具备有效研究观察状态'],
        'next_condition': condition if active else '等待新鲜行情、完整结构与风险复核后重新评估',
        'invalidation': '源行情或接收时间超过180秒、原策略条件不再成立、或出现已确认重大风险时撤销观察',
        'levels': levels,
        'levels_policy': '历史支撑/阻力仅供结构复核，不是已验证买入价、止损价或保证成交价',
        'blockers': blockers,
        'valid_until': valid_until.isoformat() if active else None,
        'legacy_policy': '老V8B只读参考；旧评级和历史补录不能授予新版执行资格',
        'sample_policy': '发现收益、实际成交收益与样本外胜率分别统计，不混用',
    }


def attach_plan(result, now):
    plan = build_plan(result, now)
    return dict(result, decision_plan=plan, execution_eligible=False,
                next_condition=plan['next_condition'], invalidation=plan['invalidation'])
