"""Compact research cards. Dedicated new group only; never trading instructions."""
from fusion_engine import FAMILIES, number
from fusion_wecom import send_text


def shown(value, digits=2):
    parsed = number(value)
    return format(parsed, '.' + str(digits) + 'f') if parsed is not None else '未核验'


def clip(value, size=90):
    return str(value or '未核验')[:size]


def message(payload):
    state = payload.get('notification_state', payload.get('state', 'WAIT_DATA'))
    plan = payload.get('decision_plan') or {}
    evidence = payload.get('evidence') or {}
    levels = plan.get('levels') or {}
    titles = {'WATCH': '🟡 研究观察', 'WAIT_DATA': '⚠ 数据暂停',
              'EXPIRED': '❌ 观察失效', 'BLOCKED': '🔴 风险否决',
              'PAUSED': '⏸ 非交易时段暂停'}
    active = state == 'WATCH'
    lines = [
        titles.get(state, '🟡 状态更新') + '｜A股机会雷达 新版',
        '📌 ' + clip(payload.get('name'), 24) + ' ' + clip(payload.get('ts_code'), 16),
        '类型：' + clip(FAMILIES.get(payload.get('family'), '研究状态'), 20),
        '💰 本次价 ' + shown(payload.get('price')) + '｜涨幅 ' + shown(evidence.get('pct_chg')) + '%',
        '初见 ' + shown(payload.get('first_seen_price')) + '｜' + clip(payload.get('first_seen_at'), 32),
        '🕘 源行情：' + clip(payload.get('source_trade_time'), 32),
        '接收：' + clip(payload.get('observed_at'), 32),
        '有效至：' + clip(plan.get('valid_until') if active else '原观察已暂停或失效', 32),
        '',
        '【当前判断】',
        '仅观察，不具备买入执行资格' if active else '停止沿用原观察结论，等待重新评估',
        '',
        '🔎 依据与变化',
        clip('；'.join(plan.get('why') or payload.get('issues') or ['等待证据同步']), 110),
    ]
    if active:
        lines += ['🎯 结构参考', '支撑 ' + shown(levels.get('support')) + '｜阻力 ' + shown(levels.get('resistance')),
                  '仅供结构复核，不是买入价或保证成交的止损价']
    lines += [
        '⚠ 未通过门槛',
        clip('；'.join(plan.get('blockers') or ['风险与执行资格未核验']), 100),
        '',
        '👉 现在怎么做',
        '未持有 → 观察，不按本消息买入' if active else '未持有 → 取消原观察依据，不按旧消息买入',
        '已持有 → 核对当前行情、实际持仓及原计划；不是自动卖出指令',
        '下一条件：' + clip(payload.get('next_condition'), 85),
        '失效条件：' + clip(payload.get('invalidation'), 75),
        '',
        '📌 无样本外胜率保证；旧V8B评级不授予新版执行资格。',
        '记录：' + clip(payload.get('signal_id'), 32),
    ]
    # Keep the action, qualification and tracking ID even under WeCom's byte limit.
    text = '\n'.join(lines)
    if len(text.encode('utf-8')) > 1800:
        lines = lines[:11] + [
            '【当前判断】仅研究，不具备买入执行资格',
            '👉 未持有不据此买入；已持有核对实际持仓和原计划。',
            '⚠ ' + clip('；'.join(payload.get('issues') or plan.get('blockers') or ['风险未核验']), 65),
            '条件与完整证据请看工作台；无样本外胜率保证。',
            '记录：' + clip(payload.get('signal_id'), 32),
        ]
        text = '\n'.join(lines)
    return text


def send_research(payload):
    return send_text(message(payload))
