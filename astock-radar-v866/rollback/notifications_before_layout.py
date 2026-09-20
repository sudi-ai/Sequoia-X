"""Research-only WeCom adapter. Never called unless explicitly enabled."""
from fusion_engine import FAMILIES
from fusion_wecom import send_text


def message(payload):
    state = payload.get('notification_state', payload.get('state', 'WAIT_DATA'))
    plan = payload.get('decision_plan') or {}
    title = {'WATCH': '研究观察，不是买入指令', 'WAIT_DATA': '数据失效，暂停参考',
             'EXPIRED': '观察条件失效', 'BLOCKED': '风险否决'}.get(state, '研究状态更新')
    lines = [title, '新版研究链路｜不代表老V8B指令',
             f"{payload.get('name', '')} {payload.get('ts_code', '')}",
             FAMILIES.get(payload.get('family'), str(payload.get('family', ''))),
             '当前动作：仅观察，不买入' if state == 'WATCH' else '当前动作：原观察暂停或失效，请复核；不是自动卖出指令',
             f"首次发现：{payload.get('first_seen_at', '未知')} / {payload.get('first_seen_price', '未知')}",
             f"本次价格：{payload.get('price') if payload.get('price') is not None else '不可用'}",
             f"行情时间：{payload.get('source_trade_time') or '不可用'}",
             f"接收时间：{payload.get('observed_at') or '不可用'}",
             f"观察有效至：{plan.get('valid_until') or '无有效观察期限'}",
             '记录：' + str(payload.get('signal_id', '')),
             '依据：' + '；'.join(plan.get('why') or ['状态变化，请到工作台复核']),
             f"后续观察：{payload.get('next_condition', '等待新鲜数据并重新评估')}",
             f"失效条件：{payload.get('invalidation', '数据过期或结构条件不再满足')}",
             '未通过门槛：' + '；'.join(plan.get('blockers') or ['风险及执行资格尚未核验']),
             '；'.join(str(x) for x in payload.get('issues', [])),
             '无样本外胜率保证，无自动下单。观察消息仅在行情新鲜且条件仍成立时有效。',
             f"记录：{payload.get('signal_id', '')}"]
    return '\n'.join(lines).encode('utf-8')[:1800].decode('utf-8', errors='ignore')


def send_research(payload):
    return send_text(message(payload))
