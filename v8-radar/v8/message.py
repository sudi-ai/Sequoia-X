from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


def _v(value: Any, default: str = "数据不足") -> Any:
    return default if value in (None, "") else value


def _price(value: Any, default: str = "数据不足") -> str:
    if value in (None, ""):
        return default
    try:
        # Round to cents, but keep the established compact layout (29.8
        # instead of 29.80) so existing message consumers/tests stay stable.
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value)


def _n(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "无数据"


def _clock(value: Any) -> str:
    if value in (None, ""):
        return "未提供"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M:%S")
    except ValueError:
        return text[-8:] if len(text) >= 8 else text


def _label(value: Any) -> str:
    labels = {
        "ATTACK": "进攻", "NORMAL": "正常", "DEFENSE": "防守",
        "STRONG": "强", "PARTIAL": "部分共振", "WEAK": "弱",
        "UNKNOWN": "待确认", "OK": "正常", "LOW": "低", "MEDIUM": "中等", "HIGH": "高",
        "HARD_BLOCK": "严重风险", "FRESH": "数据新鲜", "STALE": "数据陈旧",
        "STALE_OR_UNKNOWN": "数据陈旧或待确认", "PRICE_IN": "可能已反映", "NEUTRAL": "中性",
        "EVENT_WATCH": "事件观察", "EVENT_MARKET_RESPONSE": "市场已响应", "EVENT_OVERHEATED": "过热警告",
        "NOT_MATURED": "尚未成熟", "PENDING_EXECUTABLE": "等待可执行价格",
        "NEWS": "新闻", "MAJOR_NEWS": "重大新闻", "ANNS_D": "公告", "REPORT_RC": "研报",
        "MAIN_RISE": "主升延续", "WASHOUT": "健康洗盘", "TRUE_WEAKNESS": "真实转弱",
        "SECOND_STRENGTH": "二次转强", "OBSERVE": "观察", "HOLD": "持有",
    }
    text = str(value or "待确认")
    return labels.get(text.upper(), text)


def build_decision_message(event: Mapping[str, Any], decision: Mapping[str, Any]) -> str:
    action = str(decision.get("action") or "WATCH")
    styles = {
        "WATCH": ("🟡", "继续观察"),
        "BLOCKED": ("🔴", "风险拦截"),
        "SHADOW_ENTRY_CONFIRMED": ("🟢", "建仓条件确认"),
    }
    icon, title = styles.get(action, ("🔵", _label(action)))
    plan = decision.get("trade_plan") if isinstance(decision.get("trade_plan"), Mapping) else {}
    checkpoint = int(event.get("checkpoint_minute") or 0)
    round_text = "首次发现" if checkpoint <= 0 else f"{checkpoint}分钟复核"
    reasons = "；".join(str(x) for x in (decision.get("reasons") or [])) or "证据待补充"
    vetoes = "；".join(str(x) for x in (decision.get("vetoes") or []))
    operation = {
        "WATCH": "继续观察，等待持续度与回踩承接确认。未确认前不追高。",
        "BLOCKED": "取消本轮机会；不买入、不补仓，等待新的独立信号。",
        "SHADOW_ENTRY_CONFIRMED": "允许记录测试建仓；优先等待回踩承接，超过观察区不追高。",
    }.get(action, "继续观察。")
    lines = [
        f"{icon} A股机会雷达 V8｜{title}",
        "",
        f"📌 {event.get('name', '')} {event.get('code', '')}｜{event.get('sector') or '板块待确认'}",
        f"💰 参考 {_price(event.get('executable_price'))}｜观察区 {_price(plan.get('entry_low'))}—{_price(plan.get('entry_high'))}",
        f"🛡 防守 {_price(plan.get('stop_price'))}｜仓位上限 {_v(decision.get('position_pct'), 0)}%",
        "",
        f"⭐ 机会 {_v(decision.get('opportunity_score'))}｜市场 {_label(decision.get('market', {}).get('label'))} {_v(decision.get('market', {}).get('score'))}",
        f"🏭 板块 {_label(decision.get('sector', {}).get('label'))} {_v(decision.get('sector', {}).get('score'))}｜趋势 {_v(decision.get('trend', {}).get('score'))}",
        f"💵 资金 {_v(decision.get('fund', {}).get('score'))}｜持续 {_v(decision.get('persistence', {}).get('score'))}｜风险 {_v(decision.get('risk_score'))}",
        f"🕘 {round_text}｜数据时间 {_clock(event.get('data_time'))}｜评估 {_clock(event.get('evaluation_time'))}",
        "",
        "👉 操作",
        operation,
        f"🔎 依据：{reasons}",
    ]
    if vetoes:
        lines.append(f"❌ 否决：{vetoes}")
    elif action == "SHADOW_ENTRY_CONFIRMED":
        lines.append("✅ 硬风险：未发现")
    lines.append("📌 V8研究验证，不自动下单，不构成投资建议。")
    return "\n".join(lines)


def build_holding_message(position: Mapping[str, Any], action: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    action_code = str(action.get("action") or "OBSERVE")
    styles = {
        "T1_LOCKED_RISK_ALERT": ("🔴", "T+1锁定风险"),
        "AUCTION_RISK_ALERT": ("🟠", "次日优先处理·竞价预警"),
        "DEFENSE_WARNING": ("🟠", "自动防守预警"),
        "PROFIT_PROTECT": ("🟠", "利润保护"),
        "STOP_RAISED": ("🔵", "自动防守上移"),
        "STOP_ESTABLISHED": ("🔵", "自动防守建立"),
        "REDUCE": ("🟠", "持仓减仓提醒"),
        "EXIT": ("🔴", "持仓退出提醒"),
        "HOLD": ("🟢", "继续持有"),
        "OBSERVE": ("🟡", "持仓观察"),
    }
    icon, title = styles.get(action_code, ("🔵", "持仓提醒"))
    pnl = _v(evidence.get("pnl_pct"))
    try:
        pnl = f"{float(pnl):+.2f}%"
    except (TypeError, ValueError):
        pnl = f"{pnl}%"
    operation = str(action.get("reason") or "继续观察")
    if action_code == "T1_LOCKED_RISK_ALERT":
        operation += "。停止加仓，次交易日优先处理。"
    elif action_code == "AUCTION_RISK_ALERT":
        operation += "。09:25只做预警；若无硬风险，等待09:35分时结构后再决定。"
    elif action_code == "REDUCE":
        operation += "。根据实际可卖数量人工核对，系统不自动下单。"
    elif action_code == "EXIT":
        operation += "。防守优先，禁止补仓摊低成本。"
    elif action_code == "DEFENSE_WARNING":
        operation += "。禁止补仓；等待下一根不同时间的有效分钟K线。"
    elif action_code == "PROFIT_PROTECT":
        operation += "。持仓不足200股不拆分卖出，按上移后的防守继续跟踪。"
    elif action_code == "STOP_RAISED":
        operation += "。新防守价只会上移，不会下调。"
    elif action_code == "STOP_ESTABLISHED":
        operation += "。后续由系统自动上移，无需手工填写防守价。"
    shares=int(float(evidence.get("shares") or position.get("shares") or 0))
    available=int(float(evidence.get("available_shares") or 0))
    suggested=int(float(evidence.get("suggested_sell_shares") or 0))
    r_value=evidence.get("current_r_multiple")
    try:r_text=f"{float(r_value):.2f}R"
    except (TypeError,ValueError):r_text="待确认"
    return "\n".join([
        f"{icon} A股机会雷达 V8｜{title}",
        "",
        f"📌 {position.get('name', '')} {position.get('code', '')}",
        f"💰 现价 {_price(evidence.get('price'))}｜成本 {_price(position.get('cost_price'))}｜浮盈亏 {pnl}",
        f"📦 持仓 {shares}股｜可用 {available}股｜建议处理 {suggested}股",
        f"🛡 初始防守 {_price(evidence.get('initial_stop'))}｜移动防守 {_price(evidence.get('trailing_stop'))}",
        f"📍 状态 {_label(evidence.get('state'))}｜盈利阶段 {r_text}｜数据 {_clock(evidence.get('data_time'))}",
        "",
        "👉 操作",
        operation,
        "📊 固定周期与动态防守分别统计，本提醒不改写历史信号。",
        "📌 V8只负责监测提醒，不自动下单，不构成投资建议。",
    ])


def build_state_transition_message(position: Mapping[str, Any], state: str, evidence: Mapping[str, Any]) -> str:
    styles = {
        "MAIN_RISE": ("🟢", "主升延续", "继续持有观察，采用移动防守，不按固定天数机械卖出。"),
        "WASHOUT": ("🟡", "健康洗盘", "结构尚未破坏，不补仓；等待回踩承接恢复。"),
        "SECOND_STRENGTH": ("🟢", "二次转强", "重新进入重点观察；等待量价和板块持续确认，不追高。"),
    }
    icon, title, operation = styles[state]
    pnl = evidence.get("pnl_pct")
    try: pnl_text = f"{float(pnl):+.2f}%"
    except (TypeError, ValueError): pnl_text = "数据不足"
    return "\n".join([
        f"{icon} A股机会雷达 V8｜持仓{title}", "",
        f"📌 {position.get('name','')} {position.get('code','')}",
        f"💰 现价 {_price(evidence.get('price'))}｜成本 {_price(position.get('cost_price'))}｜浮盈亏 {pnl_text}",
        f"📍 状态 {title}｜移动防守 {_price(evidence.get('trailing_stop'))}", "",
        "👉 操作", operation,
        f"🔎 依据：{_v(evidence.get('state_reason'),'多证据状态确认')}",
        "📌 状态首次变化提醒；后续无变化不重复推送。",
        "V8研究验证｜不自动下单｜不构成投资建议",
    ])


def build_heartbeat_message(slot: str, holding_count: int, health: Mapping[str, Any]) -> str:
    status = "接口预算正常" if not health.get("circuit_open") else "接口处于降级保护"
    portfolio = health.get("portfolio_details") if isinstance(health.get("portfolio_details"),Mapping) else {}
    lines = [
        f"🔵 A股机会雷达 V8｜{slot}运行心跳", "",
        "✅ V8主程序正在运行",
        f"📌 当前监测持仓 {holding_count}只",
    ]
    if holding_count == 0:
        lines.append("⚠ 当前未录入持仓，洗盘、主升、止盈止损等个股监控不会产生提醒。")
    lines += [
        f"🔌 {status}｜近1分钟调用 {health.get('calls_last_minute',0)}/{health.get('limit','-')}", "",
        f"🛡 持仓扫描 {_label(health.get('portfolio_status'))}｜最近完成 {_clock(health.get('portfolio_checked_at'))}｜耗时 {_v(portfolio.get('elapsed_seconds'))}秒",
        "👉 操作", "无需操作；若后续出现确认或风险变化，系统将单独提醒。",
        "V8研究验证阶段｜不自动下单",
    ]
    return "\n".join(lines)


def build_portfolio_summary_message(slot: str, positions: list[Mapping[str, Any]]) -> str:
    lines = [f"📋 A股机会雷达 V8｜{slot}持仓摘要", "", f"当前持仓：{len(positions)}只"]
    if not positions:
        lines.append("暂无录入V8的实际持仓。")
    for index, item in enumerate(positions, 1):
        state = _label(item.get("current_state") or "待确认")
        try: details = item.get("details") if isinstance(item.get("details"), Mapping) else {}
        except Exception: details = {}
        try: pnl = f"{float(details.get('pnl_pct')):+.2f}%"
        except (TypeError, ValueError): pnl = "数据不足"
        lines.append(f"{index}. {item.get('name','')} {item.get('code','')}｜{state}｜浮盈亏 {pnl}")
    lines += ["", "👉 操作", "按状态变化提醒执行观察；摘要本身不触发买卖。",
              "📌 数据不足的持仓保持待确认，不作负面判断。", "V8只负责监测提醒，不自动下单。"]
    return "\n".join(lines)


def build_evening_review_message(stats: Mapping[str, Any]) -> str:
    actions = stats.get("actions") if isinstance(stats.get("actions"), Mapping) else {}
    forward = stats.get("forward") if isinstance(stats.get("forward"), list) else []
    event_forward=stats.get('event_forward') if isinstance(stats.get('event_forward'),list) else []
    discovery=stats.get('discovery') if isinstance(stats.get('discovery'),Mapping) else {}
    queue=stats.get('recheck_queue') if isinstance(stats.get('recheck_queue'),Mapping) else {}
    failures=stats.get('recheck_failures') if isinstance(stats.get('recheck_failures'),list) else []
    delivery=stats.get('delivery') if isinstance(stats.get('delivery'),Mapping) else {}
    lines = ["📊 A股机会雷达 V8｜盘后复盘", "",
             f"候选观察 {actions.get('WATCH',0)}｜建仓确认 {actions.get('SHADOW_ENTRY_CONFIRMED',0)}｜风险拦截 {actions.get('BLOCKED',0)}",
             f"复核候选 {discovery.get('total',0)}｜完成多轮复核 {discovery.get('completed',0)}｜防守强候选 {discovery.get('defense_strong',0)}",
             f"低位提前观察 {discovery.get('early_shadow',0)}｜首次发现平均 {_n(discovery.get('avg_first_seen_pct'),2,'%')}｜当前平均 {_n(discovery.get('avg_latest_pct'),2,'%')}",
             f"复核任务 完成{queue.get('DONE',0)}｜待处理{queue.get('PENDING',0)+queue.get('RETRY',0)+queue.get('RUNNING',0)}｜失败{queue.get('FAILED',0)}",
             f"强势股审计 {stats.get('audit_total',0)}｜提前发现 {stats.get('discovered',0)}｜拦截 {stats.get('blocked',0)}｜完全漏选 {stats.get('missed',0)}", "",
             "【成熟收益】"]
    if discovery.get('avg_confirm_minutes') is not None:
        lines.insert(5,f"⏱ 首次发现至正式确认平均 {_n(discovery.get('avg_confirm_minutes'),1,'分钟')}")
    if delivery.get('pushed'):
        avg_move='无数据' if delivery.get('avg_move') is None else f"{float(delivery['avg_move']):+.2f}%"
        late='无数据' if delivery.get('late_over_4_pct') is None else f"{float(delivery['late_over_4_pct']):.1f}%"
        lines.insert(5,f"建仓确认送达 {delivery.get('pushed',0)}｜发现至确认平均 {avg_move}｜超过4%后确认占比 {late}")
    if failures:
        lines.insert(6,"复核失败原因："+"；".join(f"{row.get('reason')} {row.get('n',0)}次" for row in failures))
    if not forward:
        lines.append("D1—D7暂无成熟样本，不计算胜率。")
    else:
        for row in forward:
            mean = "无数据" if row.get("mean") is None else f"{float(row['mean']):+.2f}%"
            lines.append(f"{row.get('horizon')}｜成熟 {row.get('n',0)}｜平均净收益 {mean}")
    lines += ["", "👉 结论", "本消息只报告真实数据库结果；未成熟样本不计入胜率。",
              "📌 单日结果不能证明策略有效，不构成投资建议。"]
    lines.insert(-3,"")
    lines.insert(-3,"【事件雷达成熟结果】")
    if not event_forward:lines.insert(-3,"暂无成熟事件样本，不计算胜率。")
    else:
        for row in reversed(event_forward):
            mean='无数据' if row.get('mean') is None else f"{float(row['mean']):+.2f}%"
            lines.insert(-3,f"{row.get('horizon')}｜成熟{row.get('mature_n',0)}｜未成熟{row.get('immature_n',0)}｜净收益均值{mean}")
    return "\n".join(lines)


def build_event_message(event: Mapping[str, Any]) -> str:
    kind = str(event.get("alert_type") or "EVENT_WATCH")
    styles = {
        "HOLDING_HARD_RISK": ("🔴", "持仓重大事件风险"),
        "CANDIDATE_CATALYST": ("🟡", "候选催化观察"),
        "MARKET_CATALYST": ("🔵", "市场事件观察"),
        "REPORT_CHANGE": ("🟣", "研报实质变化观察"),
        "REPORT_OBSERVATION": ("⚪", "普通研报记录"),
        "EVENT_RISK": ("🔴", "事件风险提醒"),
    }
    icon,title=styles.get(kind,("🔵","事件观察"))
    target=(f"{event.get('name','')} {event.get('code','')}".strip() or
            f"相关方向：{event.get('topic') or '尚未确认'}")
    direction={"POSITIVE":"偏利好","NEGATIVE":"偏利空","NEUTRAL":"中性"}.get(str(event.get("direction")),"待判断")
    action={
      "HOLDING_HARD_RISK":"优先核对公告原文和持仓风险；系统不自动卖出。",
      "CANDIDATE_CATALYST":"加入催化观察，仍需行情、板块和持续度确认，不能直接买入。",
      "MARKET_CATALYST":"仅观察相关板块或个股是否出现真实资金响应，不提前追涨。",
      "REPORT_CHANGE":"研报只作中期预期变化证据，不能单独触发买入。",
      "EVENT_RISK":"暂停新增关注，先核实事件影响范围和原文。",
    }.get(kind,"继续观察并核实原文。")
    category_cn={"STOCK_EVENT":"个股事件","NATIONAL_POLICY":"国家政策","INDUSTRY_EVENT":"行业事件",
      "MACRO_EVENT":"宏观事件","UNRESOLVED":"尚未映射A股"}.get(str(event.get('primary_category') or ''),'事件')
    report_cn={"FIRST_COVERAGE":"首次覆盖","MATERIAL_UPGRADE":"观点/预测上调","MATERIAL_DOWNGRADE":"观点/预测下调",
      "RATING_CHANGED":"评级变化","ROUTINE_RESEARCH":"普通研报"}.get(str(event.get('report_change_kind') or ''),'')
    source_display=_label(event.get('source_identity') or event.get('source_api'))
    lines=[
      f"{icon} A股机会雷达 V8｜{title}","",f"📌 {target}",
      f"📰 {event.get('title','')}",
      f"🧭 {category_cn}｜方向 {direction}｜风险 {_label(event.get('risk_level'))}",
      f"🏛 来源 {source_display}",
      f"🕘 发布时间 {_clock(event.get('published_at'))}｜主题 {event.get('topic') or '待确认'}",
    ]
    if report_cn:lines.append(f"📑 研报变化：{report_cn}")
    if event.get('state'):
        state_cn={"CLUE":"早期线索","RISING":"风险/机会升温","HIGH_PROBABILITY":"高概率待确认",
                  "CONFIRMED":"事件已确认","SPREADING":"影响扩散","FADING":"影响衰减/反转",
                  "CLOSED":"事件结束","REVERSED":"权威辟谣/反转"}.get(str(event.get('state')),str(event.get('state')))
        lines += [f"🧠 推演状态 {state_cn}｜线索强度 {event.get('probability','-')}/100｜证据分 {event.get('confidence','-')}",
                  f"🔎 独立来源 {event.get('source_count',0)}｜证据 {event.get('evidence_count',0)}"]
    chain=event.get('causal_chain') if isinstance(event.get('causal_chain'),list) else []
    demand=event.get('demand') if isinstance(event.get('demand'),list) else []
    beneficiaries=event.get('beneficiaries') if isinstance(event.get('beneficiaries'),list) else []
    affected=event.get('affected_companies') if isinstance(event.get('affected_companies'),list) else []
    benefit_sectors=event.get('direct_benefit_sectors') if isinstance(event.get('direct_benefit_sectors'),list) else []
    negative=event.get('negative_sectors') if isinstance(event.get('negative_sectors'),list) else []
    if chain:
        lines += ["",f"🔗 因果链：{' → '.join(str(x) for x in chain[:4])}",
                  f"📦 潜在需求：{'、'.join(str(x) for x in demand[:4])}",
                  f"🎯 证据评分 {event.get('confidence','-')}｜影响周期 {_label(event.get('impact_horizon'))}｜价格反映 {_label(event.get('realization_status'))}"]
        if benefit_sectors:lines.append(f"📈 可能受益板块：{'、'.join(str(x) for x in benefit_sectors[:6])}")
        related=event.get('related_topics') if isinstance(event.get('related_topics'),list) else []
        if related:lines.append(f"🧩 关联方向：{'、'.join(str(x) for x in related)}")
        if negative:lines.append(f"📉 潜在承压：{'、'.join(str(x) for x in negative[:4])}")
    supporting=event.get('supporting_evidence') if isinstance(event.get('supporting_evidence'),list) else []
    contrary=event.get('contrary_evidence') if isinstance(event.get('contrary_evidence'),list) else []
    checks=event.get('next_checks') if isinstance(event.get('next_checks'),list) else []
    if supporting:lines.append(f"✅ 支持证据：{'；'.join(str(x) for x in supporting[:4])}")
    if contrary:lines.append(f"↩ 反证/缓和：{'；'.join(str(x) for x in contrary[:3])}")
    if checks:lines.append(f"⏳ 下一验证：{'；'.join(str(x) for x in checks[:3])}")
    strong_beneficiaries=[x for x in beneficiaries if str(x.get('tier') or '') in {'A','B'}]
    strong_affected=[x for x in affected if str(x.get('tier') or '') in {'A','B'}]
    if strong_beneficiaries:
        lines += ["", "【受益方向直接映射】"]
        for item in strong_beneficiaries[:5]:
            pct='未知' if item.get('day_pct') is None else f"{float(item['day_pct']):+.2f}%"
            evidence='、'.join(str(x) for x in (item.get('evidence') or [])[:3]) or item.get('relation_type','')
            lines.append(f"• {item.get('name','')} {item.get('code','')}｜{item.get('mapped_sector') or '相关产业'}｜{item.get('tier','-')}级 {item.get('relevance_score','-')}｜当日 {pct}")
            lines.append(f"  依据：{evidence}")
    if strong_affected:
        lines += ["", "【承压方向直接映射】"]
        for item in strong_affected[:5]:
            pct='未知' if item.get('day_pct') is None else f"{float(item['day_pct']):+.2f}%"
            evidence='、'.join(str(x) for x in (item.get('evidence') or [])[:3]) or item.get('relation_type','')
            lines.append(f"• {item.get('name','')} {item.get('code','')}｜{item.get('mapped_sector') or '相关产业'}｜{item.get('tier','-')}级 {item.get('relevance_score','-')}｜当日 {pct}")
            lines.append(f"  依据：{evidence}")
    if not strong_beneficiaries and not strong_affected:
        lines += ["", "⚪ 暂未找到A/B级主营业务直接映射，本次不列股票，避免强行推荐。"]
    market=event.get('market_shadow') if isinstance(event.get('market_shadow'),Mapping) else {}
    if market:
        up='未知' if market.get('up_ratio_pct') is None else f"{float(market['up_ratio_pct']):.1f}%"
        volume='未知' if market.get('volume_ratio') is None else f"{float(market['volume_ratio']):.2f}倍"
        lines += ["",f"🌐 行情验证：{_label(market.get('event_state'))}｜已知样本 {market.get('known_count',0)}/{market.get('sample_count',0)}｜上涨广度 {up}｜成交倍率 {volume}"]
    lines += ["","👉 操作",action,
      "📌 事件推演只做研究观察；业务匹配不等于利润一定受益，未经过V8多层确认不构成建仓信号。"]
    return "\n".join(lines)


def build_software_issue_message(issues:list[str])->str:
    lines=["⚠ A股机会雷达 V8｜软件缺口主动提醒","",*[f"• {item}" for item in issues],"",
           "👉 操作","这些问题会降低数据完整性或提醒及时性；系统已保持降级运行，不会用缺失数据强行判断。",
           "📌 此消息是软件运行审计，不是股票交易信号。"]
    return "\n".join(lines)
