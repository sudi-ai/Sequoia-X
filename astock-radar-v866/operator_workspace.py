"""Read-only operator queue over existing holdings and defense evidence."""
import json
import sqlite3
from contextlib import closing

from runtime_freshness import quote_guard, market_session, parse_cn

STATE_NAMES = {
    'MAIN_RISE': '趋势延续条件满足', 'WASHOUT': '回撤承接条件满足（非洗盘定论）',
    'SECOND_STRENGTH': '二次转强条件满足', 'TRUE_WEAKNESS': '转弱规则触发',
    'OBSERVE': '方向待确认', 'EVENT_RISK_REVIEW': '事件风险待复核',
}


def holding_rows(data):
    from cloud_product_workbench import ROOT, connection
    try:
        with closing(connection(ROOT / 'data' / 'portfolio_monitor.db')) as conn:
            positions = [dict(r) for r in conn.execute('SELECT * FROM portfolio_position WHERE quantity>0')]
    except (sqlite3.Error, OSError):
        return [], '持仓数据库未接通，不能将缺失显示为零持仓。'
    defenses = {}
    try:
        with closing(connection(ROOT / 'data' / 'portfolio_defense.db')) as conn:
            defenses = {r['ts_code']: dict(r) for r in conn.execute('SELECT * FROM portfolio_defense_state')}
    except (sqlite3.Error, OSError):
        pass
    rows = []
    for position in positions:
        code = position['ts_code']
        quote = data['by_code'].get(code, {})
        defense = defenses.get(code, {})
        receipt_ok, receipt_reason = quote_guard(dict(quote, source_trade_time=data['snapshot'].get('source_trade_time')))
        qt, dt = parse_cn(quote.get('observed_at')), parse_cn(defense.get('observed_at'))
        aligned = bool(qt and dt and 0 <= (qt-dt).total_seconds() <= 180)
        action = defense.get('action') or 'WAITING'
        priority, task = 3, '持续监控，等待规则状态变化'
        if not quote:
            priority, task = 0, '报价缺失：暂停判断，检查采集'
        elif not market_session():
            priority, task = 2, '非连续交易时段：查看最近记录，不作为实时触发'
        elif not receipt_ok:
            priority, task = 0, '报价过期或质量不足：暂停新的策略判断'
        elif not aligned:
            priority, task = 0, '防守计算未跟上报价：检查防守任务'
        elif action in {'DYNAMIC_DEFENSE', 'EVENT_RISK_REVIEW', 'RISK_REVIEW'}:
            priority, task = 1, '优先复核规则触发与持仓计划'
        elif action == 'DEFENSE_WARNING':
            priority, task = 1, '防守预警：等待后续独立分钟确认'
        try:
            evidence = json.loads(defense.get('evidence_json') or '{}')
            if not isinstance(evidence, dict):
                evidence = {}
        except (TypeError, ValueError):
            evidence = {}
        rows.append(dict(position=position, quote=quote, defense=defense, evidence=evidence,
                         priority=priority, task=task, receipt_reason=receipt_reason, aligned=aligned))
    return sorted(rows, key=lambda row: (row['priority'], row['position']['ts_code'])), ''


def operator_panel(data):
    from cloud_product_workbench import panel, note, text, fmt, link, ROOT, read_json
    rows, error = holding_rows(data)
    if error:
        return panel('操作员工作队列', note(error)+link('health', '检查数据连接'))
    body = note('先处理数据故障和持仓风险，再看机会。以下为既有规则的复核任务，不自动下单；服务器接收时间不等于交易所行情时间。')
    if not rows:
        body += note('当前没有非零持仓。录入后纳入现有防守任务；不要求手工填写自动防守价。')
    for row in rows:
        p, q, d, e = (row[k] for k in ('position', 'quote', 'defense', 'evidence'))
        status = STATE_NAMES.get(d.get('current_state'), '等待防守计算')
        heading = f"{p['name']} {p['ts_code']} · {row['task']}"
        facts = [('最新快照价', fmt(q.get('close'))), ('初始防守', fmt(d.get('initial_stop'))),
                 ('移动防守', fmt(d.get('trailing_stop'))), ('分段目标', fmt(d.get('dynamic_target'))),
                 ('原防守', fmt(e.get('previous_stop'))), ('原目标', fmt(e.get('previous_target')))]
        body += '<article class="pd-panel"><h3>'+text(heading)+'</h3><div class="pd-facts">'
        body += ''.join('<div><dt>'+text(k)+'</dt><dd>'+text(v)+'</dd></div>' for k,v in facts)+'</div>'
        body += '<p>规则状态：'+text(status)+' · 待确认：'+text(STATE_NAMES.get(d.get('pending_state'), '无'))+'</p>'
        body += '<p>依据：'+text(d.get('state_reason'), '尚无可核验判断')+'</p>'
        body += '<p>防守调整：'+text(e.get('stop_reason'))+' · 波动依据：'+text(d.get('atr_source'))+'</p>'
        body += '<p>报价接收：'+text(q.get('observed_at'))+' · 防守计算依据时点：'+text(d.get('observed_at'))+'</p>'
        body += '<details><summary>展开确认条件和规则证据</summary><p>有效跌破确认：'+text(e.get('break_count'))+'/'+text(e.get('break_required'))
        body += '；均价 '+fmt(e.get('vwap'))+'；5分钟变化 '+fmt(e.get('return_5m'))+'%；15分钟变化 '+fmt(e.get('return_15m'))+'%</p>'
        body += '<p>价格、VWAP、持续性或板块条件改变后需重新评估；状态评分不是胜率。</p><pre>'+text(json.dumps(e, ensure_ascii=False, indent=2))+'</pre></details>'
        body += link('stock_detail', '查看个股报价证据', p['ts_code'])+' · '+link('portfolio', '持仓记录')+'</article>'
    audits = []
    for name, filename in [('持仓提醒', 'PORTFOLIO_ALERT_AUDIT.json'), ('市场简报', 'MARKET_DIGEST_AUDIT.json'), ('A级提醒', 'PUSH_BRIDGE_AUDIT.json')]:
        audit = read_json(ROOT/'data'/filename)
        audits.append('<li>'+text(name)+'：'+text(audit.get('status'), '未接通')+' · 检查 '+text(audit.get('checked_at'))+' · 本轮接口接受 '+text(audit.get('sent'), '未知')+' 条</li>')
    body += '<details class="pd-panel"><summary>微信任务状态</summary><ul>'+''.join(audits)+'</ul><p>接口接受不等于手机已读；审计时间过旧不能证明任务仍正常。</p></details>'
    return panel('优先处理 / 持仓防守', body)
