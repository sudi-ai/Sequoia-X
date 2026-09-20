# -*- coding: utf-8 -*-
"""Rich read-only LIVE/PIT presentation for V8.6.6."""
from __future__ import annotations

from html import escape

from intraday_workbench import load_intraday_snapshot
from live_analysis import load_live_analysis, load_pit_history
from live_runtime import discovery_sample_message, sample_message


def _s(value):
    return escape(str(value if value not in (None, "") else "--"))


def _n(value, digits=1):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _pct(value):
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "--"


def _legacy_pool(value):
    text = str(value or "")
    return "旧V8原池（源文件编码损坏）" if "\ufffd" in text or text.count("?") >= 2 else (text or "未标注")


def mode_header(mode, status):
    colors = {"DEMO": "#f2b84b", "LIVE": "#25d07f", "PIT": "#4bb8ff"}
    source = "程序内模拟数据" if mode == "DEMO" else ("全市场盘中PIT快照 / dailyfetch/Tushare" if mode == "LIVE" else "本地不可变 PIT 快照")
    color = colors.get(mode, "#8794a8")
    return f"<span class='mode-pill' style='border-color:{color};color:{color}'>{mode}</span><span class='source-line'>来源：{escape(source)}｜更新时间：{_s(status.get('updated_at'))}</span>"


def mode_controls(page, active):
    links = []
    for mode in ("DEMO", "LIVE", "PIT"):
        cls = "mode-link active" if mode == active else "mode-link"
        links.append(f"<a class='{cls}' href='/?page={escape(page)}&mode={mode}'>{mode}</a>")
    return "<div class='mode-controls'>" + "".join(links) + "</div>"


def data_notice(mode, status):
    message = "当前只使用程序内模拟数据，真实接口不会混入本页数值。" if mode == "DEMO" else "当前只读分析真实数据，不下单、不推送、不调参、不自动开启 A+。"
    return ("<div class='card w4'><div class='panel-title'>数据边界</div>"
            f"<p><b>{mode}</b>｜{escape(message)}</p>"
            f"<p>Token：{'已配置' if status['token']['configured'] else '未配置'}｜镜像：{_s(status['mirror'].get('status'))}｜盘中PIT：{'已接入' if status.get('intraday_ready') else '未接入'}｜历史PIT：{'可用' if status.get('pit_ready') else '未建立'}</p>"
            "<p class='muted'>T+1 是 V8 规则估计，不是真实胜率。缺失字段保持未知，不使用 DEMO 补值。</p></div>")


def _sample_domains_panel(status):
    domains = status.get("sample_domains", {})
    discovery = domains.get("discovery", {})
    signal = domains.get("trade_signal", {})
    checkpoints = "｜".join(
        f"{label}：{discovery.get('checkpoints', {}).get(label, {}).get('days', 0)}天"
        for label in ("09:25", "09:35", "10:00", "11:00", "13:30", "14:30")
    )
    return ("<div class='card w2'><p class='panel-title'>全市场发现样本</p>"
            f"<p class='kpi'>{_s(discovery.get('pit_market_rows', 0))}</p><p>真实PIT股票快照行</p>"
            f"<p>完整交易日 {_s(discovery.get('complete_trade_days', 0))}｜召回已标注 {_s(discovery.get('recall_labeled_days', 0))} 天</p>"
            f"<p class='muted'>{escape(checkpoints)}</p><p class='warn'>{_s(discovery.get('status'))}</p>"
            "<p>仅用于：赢家发现、Top20/Top50召回、全市场强度排序。</p></div>"
            "<div class='card w2'><p class='panel-title'>真实交易信号样本</p>"
            f"<p class='kpi'>{_s(signal.get('trade_signals', 0))}</p><p>A/B/A+/Primary Signal</p>"
            f"<p>已配对 {_s(signal.get('paired', 0))}｜T+1 {_s(signal.get('t1_labeled', 0))}｜Meta有效 {_s(signal.get('meta_eligible', 0))}</p>"
            f"<p class='muted'>已排除普通观察/发现候选 {_s(signal.get('excluded_non_signals', 0))} 条</p>"
            f"<p class='warn'>{escape(sample_message(status))}</p>"
            "<p>仅用于：真实胜率、Meta、A+、CPCV、Conformal及交易统计。</p></div>"
            "<div class='card w4'><p class='panel-title'>样本隔离规则</p>"
            "<p><b>发现层直接抓全市场；胜率层只认系统当时真实产生的交易信号。</b> 两套样本可以关联验证，但绝不互相冒充。</p></div>")


def _intraday_panel(status, limit=20):
    """Visualize persisted P0 market data without upgrading it to a trade signal."""
    intraday = status.get("intraday") or load_intraday_snapshot(limit=limit)
    if not intraday.get("ready"):
        return "<div class='card w4'><p class='panel-title'>今日盘中PIT数据</p><p class='warn'>尚无可展示的盘中快照：" + _s(intraday.get("error")) + "</p></div>"
    snap, coverage = intraday.get("snapshot", {}), intraday.get("coverage", {})
    issues = "；".join(str(x) for x in snap.get("issues", []) if x) or "无"
    checkpoints = "｜".join(f"{label} {info.get('days', 0)}天" for label, info in intraday.get("checkpoints", {}).items())
    sectors = "".join(f"<tr><td>{_s(item.get('name'))}</td><td>{_s(item.get('stocks'))}</td><td>{_n(item.get('avg_pct'),2)}%</td><td>{_n(item.get('up_ratio'),1)}%</td></tr>" for item in intraday.get("sectors", [])[:8])
    discovery_by_code = {}
    for item in intraday.get("discoveries", []):
        discovery_by_code.setdefault(str(item.get("ts_code")), item)
    leader_rows = []
    for item in intraday.get("leaders", [])[:limit]:
        discovered = discovery_by_code.get(str(item.get("ts_code")))
        marker = _s(discovered.get("discovery_type")) if discovered else "全市场强度排名"
        leader_rows.append(f"<tr><td>{_s(item.get('name'))}<br><code>{_s(item.get('ts_code'))}</code></td><td>{_s(item.get('industry'))}</td><td>{_n(item.get('price'),3)}</td><td>{_n(item.get('pct_chg'),2)}%</td><td>{_s(item.get('market_rank'))}</td><td>{_n(item.get('amount_velocity'),2)}</td><td>{_n(item.get('persistence_score'),1)}</td><td>{marker}</td></tr>")
    return ("<div class='grid'>"
            f"<div class='card'><p class='panel-title'>最新盘中快照</p><p class='kpi'>{_s(snap.get('mandatory_label') or '持续采集')}</p><p>{_s(snap.get('trade_date'))}｜{_s(snap.get('observed_at'))}</p></div>"
            f"<div class='card'><p class='panel-title'>覆盖股票</p><p class='kpi'>{_s(coverage.get('stocks'))}</p><p>上涨 {_s(coverage.get('up'))}｜下跌 {_s(coverage.get('down'))}</p></div>"
            f"<div class='card'><p class='panel-title'>全市场强势位</p><p class='kpi'>{_s(coverage.get('top20'))} / {_s(coverage.get('top50'))}</p><p>Top20 / Top50 当时快照</p></div>"
            f"<div class='card'><p class='panel-title'>发现候选</p><p class='kpi'>{_s(len(intraday.get('discoveries', [])))}</p><p>仅发现证据，不等于可买信号</p></div>"
            "<div class='card w2'><p class='panel-title'>板块实时扩散</p><table><tr><th>板块</th><th>股票数</th><th>平均涨跌</th><th>上涨占比</th></tr>" + sectors + "</table></div>"
            "<div class='card w2'><p class='panel-title'>盘中发现与强度排名</p><div class='table-scroll'><table><tr><th>股票</th><th>板块</th><th>快照价</th><th>涨跌</th><th>排名</th><th>成交加速度</th><th>持续性</th><th>发现状态</th></tr>" + "".join(leader_rows) + "</table></div></div>"
            f"<div class='card w4'><p class='panel-title'>PIT证据与限制</p><p>来源：{_s(snap.get('source'))}｜快照行数：{_s(snap.get('row_count'))}｜字段数：{_s(snap.get('field_count'))}｜数据质量：{_n(snap.get('data_quality'),2)}｜PIT安全：{'是' if snap.get('is_pit_safe') else '否'}</p><p>固定时点：{_s(checkpoints)}</p><p class='warn'>时间证据：{_s(issues)}。尚未生成收盘赢家标签前，不显示 Top20/Top50 Recall 或任何真实胜率。</p></div></div>")


def _intraday_watch_table(status, limit=8):
    """Show observable intraday candidates without mislabelling them as execution signals."""
    intraday = status.get("intraday") or {}
    rows = intraday.get("discoveries") or intraday.get("leaders") or []
    if not rows:
        return '<div class="empty">暂未形成可展示的盘中观察候选。请先确认“市场总览”的PIT快照已写入。</div>'
    tradable_rows = []
    excluded_limitups = 0
    for item in rows:
        try:
            pct_value = float(item.get("pct_chg") or item.get("change_pct") or 0)
        except (TypeError, ValueError):
            pct_value = 0.0
        # A board-locked stock cannot be treated as an executable intraday idea.
        # Keep the PIT record for discovery/next-day research, but not this table.
        if pct_value >= 9.5:
            excluded_limitups += 1
            continue
        tradable_rows.append(item)
    if not tradable_rows:
        suffix = "；已涨停或不可成交记录已移至“涨停与题材”研究，不作为执行候选" if excluded_limitups else ""
        return '<div class="empty">当前没有可交易的盘中观察候选%s。</div>' % suffix
    rendered = []
    for item in tradable_rows[:limit]:
        code = item.get("ts_code") or item.get("code") or "--"
        name = item.get("name") or "未命名"
        price = _n(item.get("price") or item.get("last"), 3)
        pct = _n(item.get("pct_chg") or item.get("change_pct"), 2)
        reason = item.get("discovery_type") or item.get("reason") or "盘中观察"
        observed = item.get("observed_at") or "--"
        rendered.append("<tr><td><b>%s</b><br><small>%s</small></td><td>%s</td><td>%s%%</td><td>%s</td><td><small>%s</small></td></tr>" % (escape(str(name)), escape(str(code)), price, pct, escape(str(reason)), escape(str(observed))))
    return '<table><thead><tr><th>股票</th><th>最新价</th><th>涨跌幅</th><th>发现原因</th><th>观察时间</th></tr></thead><tbody>%s</tbody></table>' % "".join(rendered)


def render_sources(status, mode):
    rows = []
    for name, item in status["interfaces"].items():
        state = str(item.get("status", "NOT_TESTED"))
        cls = "green" if state in ("OK", "CACHED") else ("amber" if state in ("EMPTY", "NOT_TESTED") else "red")
        rows.append(f"<tr><td><code>{escape(name)}</code></td><td>{_s(item.get('permission'))}</td><td>{_s(item.get('last_call_time'))}</td><td>{_s(item.get('latency_ms'))} ms</td><td>{_s(item.get('rows'))}</td><td>{_s(item.get('fields'))}</td><td class='{cls}'>{escape(state)}</td><td class='error-cell'>{_s(item.get('last_error'))}</td></tr>")
    token_text = "已配置（密钥不显示）" if status["token"]["configured"] else "未配置"
    return ("<div class='grid'><div class='card'><p class='panel-title'>Tushare Token</p>"
            f"<p class='kpi'>{escape(token_text)}</p><p class='muted'>来源：{_s(status['token'].get('source'))}</p></div>"
            "<div class='card'><p class='panel-title'>dailyfetch 镜像</p>"
            f"<p class='kpi'>{_s(status['mirror'].get('status'))}</p><p class='muted'>{_s(status['mirror'].get('url'))}</p></div>"
            f"<div class='card'><p class='panel-title'>真实接口行</p><p class='kpi'>{_s(status.get('live_rows'))}</p><p>接口探测行数，不等于信号</p></div>"
            f"<div class='card'><p class='panel-title'>交易信号有效样本</p><p class='kpi'>{_s(status.get('real_sample_n'))}</p><p>仅真实信号且PIT安全、带T+1</p></div>"
            "<div class='card w4'><p class='panel-title'>付费接口探测明细</p><div class='table-scroll'><table><tr><th>接口</th><th>权限</th><th>最近调用</th><th>延迟</th><th>行数</th><th>字段</th><th>状态</th><th>最近错误</th></tr>"
            + "".join(rows) + "</table></div></div>"
            "<div class='card w4'><p class='panel-title'>刷新真实分析</p><p>双击 <code>REFRESH_LIVE_ANALYSIS.bat</code>，完成后刷新浏览器。以后启动新版也会后台刷新。</p><p class='warn'>接口失败不会静默切换 DEMO，也不会阻止软件打开。</p></div>"
            + _sample_domains_panel(status) + data_notice(mode, status) + "</div>")


def render_health(status, mode):
    failed = sum(1 for item in status["interfaces"].values() if item.get("status") in ("ERROR", "NO_PERMISSION", "TIMEOUT"))
    analysis = load_live_analysis()
    return ("<div class='grid'>"
            f"<div class='card'><p class='panel-title'>模式</p><p class='kpi'>{mode}</p><p>LIVE：{'READY' if status.get('live_ready') else 'NOT READY'}</p></div>"
            f"<div class='card'><p class='panel-title'>真实分析</p><p class='kpi'>{_s(analysis.get('status'))}</p><p>行情 {_s(analysis.get('market_data_date'))}｜范围 {_s(analysis.get('universe_rows'))}</p></div>"
            f"<div class='card'><p class='panel-title'>异常接口</p><p class='kpi'>{failed}</p><p>单接口失败已隔离</p></div>"
            f"<div class='card'><p class='panel-title'>统计资格</p><p class='warn'>{escape(sample_message(status))}</p></div>"
            + _sample_domains_panel(status) + data_notice(mode, status) + "</div>")


def _candidate_table(candidates, mode, limit=30):
    rows = []
    for item in candidates[:limit]:
        code, grade = str(item.get("ts_code", "")), str(item.get("pool", "C"))
        cls = "green" if grade == "A" else ("amber" if grade == "B" else "muted")
        discovery = " / ".join(item.get("discovery_pools", [])) or "--"
        rows.append(f"<tr><td><a href='/?page=stock_detail&code={escape(code)}&mode={mode}'>{_s(item.get('name'))}<br><code>{escape(code)}</code></a></td>"
                    f"<td>Top {_n(item.get('market_alpha_top_pct'),2)}%</td><td>{_s(discovery)}</td>"
                    f"<td class='{cls}'>{escape(grade)}</td><td>{_n(item.get('score'))}</td><td>{_pct(item.get('t1_probability'))}</td>"
                    f"<td>{_n(item.get('daily_pct'),2)}%</td><td>{_s(item.get('sector'))}</td><td>{_n(item.get('main_flow_score'))}</td>"
                    f"<td>{_n(item.get('data_quality'),2)}</td><td>{_s(item.get('action'))}</td></tr>")
    if not rows:
        return "<p class='warn'>尚无真实候选。请运行 REFRESH_LIVE_ANALYSIS.bat 后刷新页面。</p>"
    return "<div class='table-scroll'><table><tr><th>股票</th><th>Alpha排名</th><th>发现池</th><th>执行级别</th><th>V8评分</th><th>T+1规则估计</th><th>涨跌</th><th>板块</th><th>资金分</th><th>质量</th><th>动作</th></tr>" + "".join(rows) + "</table></div>"


def _candidate_card(item):
    evidence = item.get("evidence", {}) or {}; veto = item.get("veto") or "无硬否决；事件风险仍需人工核验"
    return ("<div class='card w4'>"
            f"<p class='panel-title'>{_s(item.get('name'))} <code>{_s(item.get('ts_code'))}</code>｜{_s(item.get('pool'))}级｜{_s(item.get('action'))}</p>"
            f"<div class='grid'><div><p class='kpi'>{_n(item.get('score'))}</p><p>V8规则评分</p></div><div><p class='kpi'>{_pct(item.get('t1_probability'))}</p><p>T+1规则估计</p></div><div><p class='kpi'>{_n(item.get('overnight_risk'))}</p><p>隔夜风险</p></div><div><p class='kpi'>{_n(item.get('rr'),2)}</p><p>估算RR</p></div></div>"
            f"<p>市场 {_s(item.get('market_phase'))} / {_n(item.get('market_temp'))}｜板块 {_s(item.get('sector'))} / {_n(item.get('sector_strength'))}</p>"
            f"<p>竞价 {_n(item.get('auction_quality'))}｜资金 {_n(item.get('main_flow_score'))}｜筹码 {_n(item.get('chip_lock_score'))}｜趋势 {_n(item.get('trend_score'))}｜买点 {_n(item.get('buy_timing_score'))}</p>"
            f"<p>支撑 {_n(item.get('support'),3)} / 强支撑 {_n(item.get('strong_support'),3)}｜压力 {_n(item.get('resistance'),3)} / 强压力 {_n(item.get('strong_resistance'),3)}</p>"
            f"<p>派发风险 {_n(item.get('distribution_risk'))}｜事件风险 {_n(item.get('event_risk'))}｜风险否决：<span class='warn'>{escape(veto)}</span></p>"
            f"<p class='muted'>日线：{_s(evidence.get('daily'))}<br>板块：{_s(evidence.get('sector'))}<br>资金：{_s(evidence.get('moneyflow'))}<br>竞价：{_s(evidence.get('auction'))}<br>筹码：{_s(evidence.get('chip'))}<br>风险：{_s(evidence.get('risk'))}</p>"
            f"<p class='muted'>来源：{_s(item.get('source'))}｜observed_at：{_s(item.get('observed_at'))}｜data_quality：{_n(item.get('data_quality'),2)}</p></div>")


def _analysis_header(analysis):
    market, pools = analysis.get("market", {}) or {}, analysis.get("pool_counts", {}) or {}
    discovery = analysis.get("discovery", {}) or {}
    warnings = "".join(f"<li>{_s(item)}</li>" for item in analysis.get("warnings", []))
    return (f"<div class='card'><p class='panel-title'>市场阶段</p><p class='kpi'>{_n(market.get('temperature'))}</p><p>{_s(market.get('phase'))}｜上涨占比 {_n(market.get('advance_rate'))}%</p></div>"
            f"<div class='card'><p class='panel-title'>分析范围</p><p class='kpi'>{_s(analysis.get('universe_rows'))}</p><p>完整日线 {_s(analysis.get('market_data_date'))}</p></div>"
            f"<div class='card'><p class='panel-title'>宽发现层</p><p class='kpi'>{_s(discovery.get('realtime_winners',0))} / {_s(discovery.get('early_startup',0))} / {_s(discovery.get('reversal_repair',0))}</p><p>赢家 / 新启动 / 弱转强</p></div>"
            f"<div class='card'><p class='panel-title'>数据覆盖</p><p>资金 {_s(analysis.get('moneyflow_rows'))}｜竞价 {_s(analysis.get('auction_rows'))}</p><p>基础 {_s(analysis.get('basic_rows'))}｜涨跌停 {_s(analysis.get('limit_rows'))}</p></div>"
            f"<div class='card w4'><p class='panel-title'>真实数据警示</p><ul>{warnings}</ul></div>")


def _pit_table(history, limit=40):
    rows = []
    for item in history.get("signals", [])[:limit]:
        t1 = _n(item.get("t1_return"), 2) if item.get("t1_return") is not None else "NULL"
        rows.append(f"<tr><td>{_s(item.get('decision_time'))}</td><td>{_s(item.get('name'))}<br><code>{_s(item.get('ts_code'))}</code></td><td>{_n(item.get('price'),3)}</td><td>{_n(item.get('score'))}</td><td>{_s(_legacy_pool(item.get('pool')))}</td><td>{'是' if item.get('is_pit_safe') else '否'}</td><td>{t1}</td><td>{_s(item.get('source'))}</td></tr>")
    return "<div class='table-scroll'><table><tr><th>决策时间</th><th>股票</th><th>价格</th><th>旧V8评分</th><th>原池</th><th>PIT安全</th><th>T+1收益</th><th>来源</th></tr>" + "".join(rows) + "</table></div>"


def _statistics_page(title, methods, status, default_domain="trade_signal"):
    discovery = status.get("sample_domains", {}).get("discovery", {})
    signal = status.get("sample_domains", {}).get("trade_signal", {})
    rows = []
    for method in methods:
        name, domain = method if isinstance(method, tuple) else (method, default_domain)
        if domain == "discovery":
            state = "可离线验证" if discovery.get("ready_for_alpha_rank") else "等待完整交易日"
            count = f"{discovery.get('pit_market_rows', 0)} 行 / {discovery.get('complete_trade_days', 0)} 个完整交易日"
            source = "全市场发现样本"
        else:
            state = "可进入样本外验证" if signal.get("statistics_ready") else "交易信号样本不足"
            count = f"{signal.get('meta_eligible', 0)} / {signal.get('min_meta_samples', 600)}"
            source = "真实交易信号样本"
        rows.append(f"<tr><td>{escape(name)}</td><td>{escape(source)}</td><td class='amber'>{escape(state)}</td><td>{escape(count)}</td></tr>")
    notice = discovery_sample_message(status) if default_domain == "discovery" else sample_message(status)
    return (f"<div class='card w4'><p class='panel-title'>{escape(title)}</p><p class='warn'>{escape(notice)}</p>"
            f"<table><tr><th>统计模块</th><th>允许的数据</th><th>状态</th><th>有效真实样本</th></tr>{''.join(rows)}</table>"
            "<p>演示百分比已隐藏；样本域和门槛未通过前不训练、不校准、不输出真实胜率。</p></div>"
            + _sample_domains_panel(status))


def _first_discovery_times(status, candidates):
    """Return today's first persisted PIT observation for each displayed code."""
    intraday = status.get("intraday") or {}
    trade_date = str((intraday.get("snapshot") or {}).get("trade_date") or "")
    codes = [str(item.get("ts_code") or "") for item in candidates if item.get("ts_code")]
    if not trade_date or not codes:
        return {}
    try:
        sqlite3 = __import__("sqlite3")
        database = __import__("p0_realtime").DB_PATH
        placeholders = ",".join("?" for _ in codes)
        with sqlite3.connect(str(database)) as conn:
            rows = conn.execute(
                "SELECT ts_code, MIN(observed_at) FROM discovery_observation "
                "WHERE trade_date=? AND ts_code IN (%s) GROUP BY ts_code" % placeholders,
                [trade_date] + codes,
            ).fetchall()
        return {str(code): str(observed_at) for code, observed_at in rows}
    except Exception:
        return {}


def _eod_candidate_timeline(candidates, status, limit=10):
    first_seen = _first_discovery_times(status, candidates)
    rows = []
    for item in candidates[:limit]:
        code = str(item.get("ts_code") or "--")
        seen = first_seen.get(code)
        seen_text = seen.replace("T", " ") if seen else "今日未被盘中PIT发现"
        rows.append(
            "<tr><td><b>%s</b><br><code>%s</code></td><td>%s</td><td>%s</td><td>仅历史收盘候选，不执行</td></tr>"
            % (escape(str(item.get("name") or "未命名")), escape(code), _s(item.get("rank_label") or "--"), escape(seen_text))
        )
    if not rows:
        return "<p class='muted'>暂无收盘候选。</p>"
    return "<table><tr><th>股票</th><th>昨日收盘排名</th><th>今日首次盘中发现</th><th>当前状态</th></tr>%s</table>" % "".join(rows)


def _today_discovery_timeline(status, limit=20):
    """Render evidence of what the live discovery layer saw, and when it saw it."""
    intraday = status.get("intraday") or {}
    snapshot = intraday.get("snapshot") or {}
    trade_date = str(snapshot.get("trade_date") or "")
    latest_snapshot_id = str(snapshot.get("snapshot_id") or "")
    if not trade_date or not latest_snapshot_id:
        return {"total": 0, "early": 0, "continued": 0, "table": "<p class='muted'>尚无今日PIT快照。</p>"}
    try:
        sqlite3 = __import__("sqlite3")
        database = __import__("p0_realtime").DB_PATH
        with sqlite3.connect(str(database), timeout=0.5) as conn:
            conn.row_factory = sqlite3.Row
            first_rows = conn.execute(
                "SELECT ts_code, MIN(observed_at) AS first_at FROM discovery_observation "
                "WHERE trade_date=? GROUP BY ts_code ORDER BY first_at ASC LIMIT ?", (trade_date, limit)
            ).fetchall()
            rows = []
            for first in first_rows:
                code, first_at = str(first["ts_code"]), str(first["first_at"])
                found = conn.execute(
                    "SELECT d.price, d.pct_chg, d.discovery_type, q.name, q.industry "
                    "FROM discovery_observation d LEFT JOIN intraday_quote q "
                    "ON q.snapshot_id=d.snapshot_id AND q.ts_code=d.ts_code "
                    "WHERE d.trade_date=? AND d.ts_code=? AND d.observed_at=? LIMIT 1",
                    (trade_date, code, first_at),
                ).fetchone()
                current = conn.execute(
                    "SELECT close, pct_chg FROM intraday_quote WHERE snapshot_id=? AND ts_code=?", (latest_snapshot_id, code)
                ).fetchone()
                rows.append({
                    "ts_code": code, "first_at": first_at, "first_price": found["price"] if found else None,
                    "first_pct": found["pct_chg"] if found else None, "discovery_types": found["discovery_type"] if found else "盘中发现",
                    "name": found["name"] if found else None, "industry": found["industry"] if found else None,
                    "current_price": current["close"] if current else None,
                    "current_pct": current["pct_chg"] if current else None,
                })
    except Exception as exc:
        return {"total": 0, "early": 0, "continued": 0, "table": "<p class='warn'>读取今日发现时间线失败：%s</p>" % escape(str(exc))}
    rendered, early, continued = [], 0, 0
    for row in rows:
        item = dict(row)
        first_pct = float(item.get("first_pct") or 0)
        first_price = item.get("first_price")
        current_price = item.get("current_price")
        current_pct = item.get("current_pct")
        current_change = float(current_pct) - first_pct if current_pct is not None else None
        post_return = None
        if first_price not in (None, 0) and current_price is not None:
            post_return = (float(current_price) / float(first_price) - 1.0) * 100.0
        if first_pct <= 3.0:
            early += 1
        if current_change is not None and current_change >= 3.0:
            continued += 1
        if current_pct is None or post_return is None:
            move_class, move_label, state, state_class = "move-unavailable", "等待快照", "尚无当前报价", "state-neutral"
        elif current_pct is not None and float(current_pct) >= 9.5:
            move_class, move_label, state, state_class = "move-limit", "涨停附近", "涨停附近｜不追高，等待下一独立信号", "state-limit"
        elif post_return >= 3.0 and first_pct <= 3.0:
            move_class, move_label, state, state_class = "move-strong", "早发现后强势", "发现后持续走强｜仍需通过A级门槛", "state-strong"
        elif post_return >= 1.0:
            move_class, move_label, state, state_class = "move-positive", "发现后走强", "发现后走强｜等待回踩或下一独立信号", "state-positive"
        elif post_return <= -2.0:
            move_class, move_label, state, state_class = "move-negative", "发现后明显回落", "发现后回落｜本轮观察降级", "state-negative"
        elif post_return < -0.5:
            move_class, move_label, state, state_class = "move-negative", "发现后回落", "发现后回落｜未通过A级门槛", "state-negative"
        else:
            move_class, move_label, state, state_class = "move-flat", "发现后横盘", "盘中观察｜未通过A级门槛", "state-neutral"
        rendered.append(
            "<tr><td><b>%s</b><br><code>%s</code><br><small>%s</small></td>"
            "<td><time>%s</time><br><small>真实PIT首次写入</small></td>"
            "<td><div class='discovery-baseline'><small>发现基准</small><b>%s</b><span>当时涨幅 %s%%</span></div></td>"
            "<td><div class='discovery-current'><small>当前快照</small><b>%s</b><span>当前涨幅 %s%%</span></div></td>"
            "<td><div class='post-move %s'><small>%s</small><b>%s%%</b><span>日内加速 %s%%</span></div></td>"
            "<td><span class='timeline-state %s'>%s</span></td></tr>"
            % (
                escape(str(item.get("name") or "未命名")), escape(str(item.get("ts_code") or "--")),
                escape(str(item.get("discovery_types") or "盘中发现")), escape(str(item.get("first_at") or "--").replace("T", " ")),
                _n(first_price, 3), _n(first_pct, 2), _n(current_price, 3), _n(current_pct, 2),
                move_class, move_label, _n(post_return, 2), _n(current_change, 2), state_class, escape(state),
            )
        )
    table = ("<div class='timeline-legend'><span class='legend-baseline'>蓝灰：发现时基准</span><span class='legend-up'>红：发现后走强</span><span class='legend-flat'>灰：发现后横盘</span><span class='legend-down'>绿：发现后回落</span></div>"
             "<div class='table-scroll'><table class='discovery-timeline'><tr><th>股票 / 发现类型</th><th>首次PIT发现</th><th>发现时基准</th>"
             "<th>当前快照</th><th>发现后实际表现</th><th>当前判读</th></tr>"
             + ("".join(rendered) if rendered else "<tr><td colspan='7' class='muted'>今日尚无真实盘中发现记录。</td></tr>")
             + "</table></div>")
    return {"total": len(rows), "early": early, "continued": continued, "table": table}


def _realtime_discovery_table(status, limit=40):
    """Render the current PIT discovery pool instead of an end-of-day ranking."""
    intraday = status.get("intraday") or {}
    snapshot = intraday.get("snapshot") or {}
    observed_at = str(snapshot.get("observed_at") or "尚未采集").replace("T", " ")
    rows_by_code = {}
    for item in list(intraday.get("leaders") or []) + list(intraday.get("discoveries") or []):
        code = str(item.get("ts_code") or "")
        if code:
            rows_by_code[code] = item
    records = sorted(rows_by_code.values(), key=lambda item: float(item.get("pct_chg") or 0), reverse=True)[:limit]
    if not records:
        return "<p class='warn'>当前没有满足盘中发现条件的股票；这不是收盘候选，也不等于可买信号。</p>"

    def number(value, digits=2):
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "--"

    table_rows = []
    for item in records:
        pct = float(item.get("pct_chg") or 0)
        pct_class = "market-up" if pct > 0 else "market-down" if pct < 0 else ""
        table_rows.append(
            "<tr>"
            f"<td><strong>{_s(item.get('name'))}</strong><br><span class='muted'>{_s(item.get('ts_code'))}</span></td>"
            f"<td>{number(item.get('price'), 3)}</td>"
            f"<td class='{pct_class}'>{number(pct, 2)}%</td>"
            f"<td>{_s(item.get('industry'))}</td>"
            f"<td>{number(item.get('vwap_strength'), 1)}</td>"
            f"<td>{number(item.get('persistence_score'), 1)}</td>"
            f"<td>{_s(item.get('sequence_state') or '盘中发现')}</td>"
            "</tr>"
        )
    return (
        f"<p class='muted'>盘中 PIT 时间：{_s(observed_at)}｜覆盖 {_s(snapshot.get('row_count'))} 只｜质量 {_s(snapshot.get('data_quality'))}</p>"
        "<table><thead><tr><th>股票</th><th>最新价</th><th>涨跌幅</th><th>行业</th><th>相对VWAP</th><th>持续度</th><th>盘中状态</th></tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody></table>"
    )


def render_real_page(page, status, mode, code=""):
    analysis, history = load_live_analysis(), load_pit_history()
    candidates, market = analysis.get("candidates", []) or [], analysis.get("market", {}) or {}
    if mode == "PIT" and page in ("overview", "a_exec", "stock_detail"):
        body = ("<div class='grid'>"
                f"<div class='card'><p class='panel-title'>真实交易信号</p><p class='kpi'>{history.get('signal_count',0)}</p></div>"
                f"<div class='card'><p class='panel-title'>已配对</p><p class='kpi'>{history.get('paired_count',0)}</p></div>"
                f"<div class='card'><p class='panel-title'>T+1标签</p><p class='kpi'>{history.get('t1_count',0)}</p></div>"
                f"<div class='card'><p class='panel-title'>Meta有效</p><p class='kpi'>{history.get('meta_eligible',0)}</p></div>"
                + _sample_domains_panel(status)
                + f"<div class='card w4'><p class='panel-title'>真实交易信号历史（原值保留）</p>{_pit_table(history)}</div></div>")
    elif page == "overview":
        winners = [item for item in candidates if "REALTIME_WINNERS" in item.get("discovery_pools", [])]
        startups = [item for item in candidates if "EARLY_STARTUP" in item.get("discovery_pools", [])]
        repairs = [item for item in candidates if "REVERSAL_REPAIR" in item.get("discovery_pools", [])]
        discovery = analysis.get("discovery", {}) or {}
        intraday_ready = mode == "LIVE" and bool(status.get("intraday_ready"))
        live_snapshot = (status.get("intraday") or {}).get("snapshot") or {}
        discovery_title = "全市场盘中发现｜实时 PIT" if intraday_ready else f"全市场赢家发现｜{_s(discovery.get('mode'))}"
        discovery_notice = (f"当前展示当日PIT发现池；数据时间 {_s(live_snapshot.get('observed_at'))}。仅供人工复核，不等于可买信号。"
                            if intraday_ready else "当前是收盘横截面排名，不冒充盘中实时。执行级别与发现池相互独立。")
        primary_table = _realtime_discovery_table(status, 40) if intraday_ready else _candidate_table(winners, mode, 40)
        secondary_panels = "" if intraday_ready else (
            "<div class='card w2'><p class='panel-title'>新启动 EARLY_STARTUP</p>" + _candidate_table(startups, mode, 15) + "</div>"
            + "<div class='card w2'><p class='panel-title'>弱转强 REVERSAL_REPAIR</p>" + _candidate_table(repairs, mode, 15) + "</div>"
        )
        body = ("<div class='grid'>" + _analysis_header(analysis)
                + f"<div class='card w4'><p class='panel-title'>{discovery_title}</p><p class='warn'>{discovery_notice}</p>" + primary_table + "</div>"
                + secondary_panels
                + f"<div class='card w4'><p class='panel-title'>Top Winner Recall</p><p class='warn'>{_s(discovery.get('recall_status'))}｜{_s(discovery.get('recall_reason'))}</p><p>缺失因子：{_s(', '.join(discovery.get('missing_factors', [])))}</p></div>"
                + f"<div class='card w4'><p class='panel-title'>旧V8历史库</p><p>信号 {history.get('signal_count',0)}｜结果 {history.get('outcome_count',0)}｜T+1标签 {history.get('t1_count',0)}｜PIT安全 {history.get('pit_safe_count',0)}。历史原值不会被今天数据补写。</p></div></div>")
    elif page == "a_exec":
        a_rows = [item for item in candidates if item.get("pool") == "A"]
        if a_rows:
            body = "<div class='grid'><div class='card w4'><p class='panel-title'>A级执行</p><p class='warn'>当前 %s 条A级候选，仍处于只读验证阶段。</p></div>%s</div>" % (len(a_rows), "".join(_candidate_card(item) for item in a_rows))
        else:
            body = "<div class='grid'><div class='card w4'><p class='panel-title'>A级执行</p><p class='warn'>当前没有通过全部执行门槛的A级信号：不执行。下方仅显示未涨停、仍可能成交的盘中观察候选；已涨停记录不进入执行候选。</p></div><div class='card w4'><p class='panel-title'>盘中可交易观察候选（未通过A级门槛）</p>%s</div></div>" % _intraday_watch_table(status)
    elif page == "sniper":
        top = next((item for item in candidates if item.get("pool") == "A"), candidates[0] if candidates else {})
        gates = [("主信号已保存", bool(top and top.get("pool") == "A"), "需真实A级并写入PIT"),
                 ("元标签样本达标", bool(status.get("statistics_ready")), sample_message(status)),
                 ("组合净化交叉验证通过", False, "尚无合格样本外结果"), ("置信集合校准完成", False, "尚无合格校准集"),
                 ("风险否决通过", bool(top and not top.get("veto")), top.get("veto") or "当前规则未触发硬否决"),
                 ("人工批准 A+", False, "安全规则要求人工批准")]
        rows = "".join(f"<tr><td>{escape(name)}</td><td class='{'green' if passed else 'red'}'>{'通过' if passed else '未通过'}</td><td>{_s(reason)}</td></tr>" for name, passed, reason in gates)
        body = f"<div class='grid'><div class='card w4'><p class='panel-title'>A+狙击门槛</p><table><tr><th>门槛</th><th>状态</th><th>证据/原因</th></tr>{rows}</table><p class='warn'>A+自动开启保持关闭。</p></div></div>"
    elif page in ("meta", "conformal", "cpcv", "factor", "mc", "drift"):
        definitions = {
            "meta": ("元标签验证", ("元标签模型", "概率校准", "交易信号自助法置信区间"), "trade_signal"),
            "conformal": ("置信集合校准", ("校准集", "预测集合", "覆盖率检验"), "trade_signal"),
            "cpcv": ("组合净化交叉验证", ("净化交叉验证", "组合净化交叉验证", "样本唯一性", "样本外精确率"), "trade_signal"),
            "factor": ("因子实验室", (("赢家发现因子", "discovery"), ("全市场强度排序", "discovery"), ("因子稳定性", "discovery"), ("交易胜率自助法", "trade_signal")), "discovery"),
            "mc": ("蒙特卡洛压力测试", ("路径模拟", "收益分布", "最大回撤分布"), "trade_signal"),
            "drift": ("漂移与稳健性监控", ("数据分布漂移", "性能漂移", "折损夏普比率"), "trade_signal"),
        }
        title, methods, domain = definitions[page]; body = "<div class='grid'>" + _statistics_page(title, methods, status, domain) + "</div>"
    elif page == "limitup":
        body = f"<div class='grid'><div class='card'><p class='panel-title'>涨停</p><p class='kpi'>{_s(market.get('limit_up'))}</p></div><div class='card'><p class='panel-title'>跌停</p><p class='kpi'>{_s(market.get('limit_down'))}</p></div><div class='card'><p class='panel-title'>触板失败率</p><p class='kpi'>{_n(market.get('broken_rate'))}%</p></div><div class='card'><p class='panel-title'>PIT记录</p><p class='kpi'>{status['pit_counts'].get('limitup_pool',0)}</p></div><div class='card w4'><p class='warn'>跨日晋级标签未补齐前不生成真实连板胜率。</p></div></div>"
    elif page == "sim":
        timeline = _today_discovery_timeline(status)
        body = f"<div class='grid'><div class='card'><p class='panel-title'>今日真实发现</p><p class='kpi'>{timeline['total']}</p><p>只统计写入PIT的首次发现</p></div><div class='card'><p class='panel-title'>低位首次发现</p><p class='kpi'>{timeline['early']}</p><p>首次发现时涨幅不高于 3%</p></div><div class='card'><p class='panel-title'>发现后仍走强</p><p class='kpi'>{timeline['continued']}</p><p>当前涨幅较首次发现增加至少 3%</p></div><div class='card'><p class='panel-title'>真实下单</p><p class='kpi red'>关闭</p><p>本页不构成买入建议</p></div><div class='card w4'><p class='panel-title'>今日真实发现时间线</p><p class='warn'>这张表回答“系统何时发现、发现时是否已经上涨、当前相对发现时是否仍走强”。收盘最高涨幅会在后台预计算；它不是胜率，也不是买入名单。</p>{timeline['table']}</div></div>"
    elif page == "stock_detail":
        item = next((row for row in candidates if str(row.get("ts_code")) == str(code)), None)
        if not item:
            intraday = status.get("intraday") or {}
            item = next((row for row in (intraday.get("discoveries") or intraday.get("leaders") or []) if str(row.get("ts_code")) == str(code)), None)
        if item and (item.get("score") is not None or item.get("pool") is not None):
            body = "<div class='grid'>" + _candidate_card(item) + "</div>"
        elif item:
            body = "<div class='grid'><div class='card w4'><p class='panel-title'>%s｜%s</p><p>最新价：<b>%s</b>｜涨跌幅：<b>%s%%</b></p><p>发现原因：%s｜观察时间：%s</p><p class='warn'>这是盘中PIT观察记录，不等同于执行信号；请回到“执行候选”查看是否通过全部门槛。</p></div></div>" % (escape(str(item.get("name") or "未命名")), escape(str(item.get("ts_code") or "--")), _n(item.get("price") or item.get("last"), 3), _n(item.get("pct_chg") or item.get("change_pct"), 2), escape(str(item.get("discovery_type") or item.get("reason") or "盘中观察")), escape(str(item.get("observed_at") or "--")))
        else:
            body = "<div class='grid'><div class='card w4'><p class='warn'>当前真实分析快照中没有该股票。</p></div></div>"
    else:
        body = "<div class='grid'><div class='card w4'><p>当前页面暂无可展示的真实记录。</p></div></div>"
    intraday_body = _intraday_panel(status, 24) if mode == "LIVE" and page == "overview" else ""
    live_snapshot = (status.get("intraday") or {}).get("snapshot") or {}
    live_display = mode == "LIVE" and bool(status.get("intraday_ready"))
    footer_source = live_snapshot.get("source") if live_display else analysis.get("source", mode)
    footer_observed = live_snapshot.get("observed_at") if live_display else analysis.get("generated_at", status.get("updated_at"))
    footer_date = live_snapshot.get("trade_date") if live_display else analysis.get("market_data_date")
    footer = ("<div class='grid'>"
              f"<div class='card w2'><p class='panel-title'>数据血缘</p><p>source：{_s(footer_source)}</p><p>observed_at：{_s(footer_observed)}</p><p>行情日期：{_s(footer_date)}</p><p>{'自动刷新：60秒｜仅当日PIT' if live_display else '当前为收盘/历史数据'}</p></div>"
              "<div class='card w2'><p class='panel-title'>安全状态</p><p class='green'>仅验证和展示</p><p>交易：关闭｜推送：关闭｜自动调参：关闭｜自动晋级：关闭</p></div>"
              + data_notice(mode, status) + "</div>")
    auto_refresh = "<script>setTimeout(function(){ window.location.reload(); }, 60000);</script>" if live_display else ""
    return intraday_body + body + footer + auto_refresh
