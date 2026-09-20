# -*- coding: utf-8 -*-
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from urllib.parse import parse_qs, quote, urlparse
import json
import threading
import webbrowser

from mock_data import dashboard_snapshot
from signal_engine import classify_candidate
from market_phase import classify_market_phase
from mock_limitup import today_rows, yesterday_rows
from limitup_runtime import LimitupRuntime
from mock_stat_learning import demo_summary as stat_demo
from mock_precision import demo_summary as precision_demo
from live_workbench import data_notice as live_data_notice
from live_workbench import mode_controls, mode_header, render_health, render_real_page, render_sources
from live_runtime import dashboard_status, resolve_mode, sample_message
from portfolio_monitor import PortfolioMonitor

HOST = "127.0.0.1"
PORT = 8791
DEMO_TAG = "DEMO"
DATA_SOURCE_CONNECTED = False
PIT_GATEWAY_ENABLED = False
PIT_GATEWAY_URL = "https://api.tushare.pro/"


PAGE_NAV = [
    ("overview", "总览"),
    ("a_exec", "A级执行"),
    ("sniper", "A+狙击"),
    ("meta", "Meta-Label"),
    ("conformal", "Conformal"),
    ("cpcv", "CPCV验证"),
    ("factor", "因子实验室"),
    ("limitup", "连板生态"),
    ("sim", "执行仿真"),
    ("portfolio", "持仓监控"),
    ("mc", "Monte Carlo"),
    ("drift", "漂移/DSR"),
    ("health", "数据健康"),
    ("sources", "数据源 / 付费接口"),
]

HTML_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>A股机会雷达 V8.6.6 Server Navigation</title>
<style>
:root{--bg:#0b1018;--panel:#111824;--bd:#273244;--text:#eaf0f8;--muted:#8794a8;--g:#25d07f;--r:#ff5964;--a:#f2b84b;--c:#4bb8ff;--line:#1c2a3b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px "Microsoft YaHei",sans-serif}
header{height:60px;display:flex;align-items:center;padding:0 20px;background:#08101a;border-bottom:1px solid var(--bd)}
h1{font-size:19px;margin:0;line-height:1.2}
.badge{margin-left:12px;padding:4px 8px;border-radius:12px;background:#1b2e45;color:#9fd8ff;font-size:12px}
.sub{color:var(--muted);margin-left:18px;font-size:12px}.ok{margin-left:12px;color:var(--g);font-size:12px}
.mode-zone{margin-left:auto;display:flex;align-items:center;gap:8px}.mode-pill{border:1px solid;padding:4px 9px;border-radius:14px;font-weight:700}.source-line{color:var(--muted);font-size:11px}
.mode-controls{display:flex;gap:4px}.mode-link{color:var(--muted);text-decoration:none;border:1px solid var(--bd);border-radius:4px;padding:3px 6px;font-size:11px}.mode-link.active{color:var(--text);background:#172231}.table-scroll{overflow:auto}.error-cell{max-width:360px;word-break:break-word}
.wrap{display:grid;grid-template-columns:190px 1fr;min-height:calc(100vh - 60px)}
nav{background:#091019;border-right:1px solid var(--bd);padding:10px;overflow:auto}
nav a{display:block;padding:11px 10px;color:var(--muted);text-decoration:none;border-radius:6px;margin:3px 0}.sel{background:#172231;color:var(--text)!important}
main{padding:10px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:7px;padding:12px}.w2{grid-column:span 2}.w3{grid-column:span 3}.w4{grid-column:span 4}
.big{font-size:30px;font-weight:700;color:var(--a)}.muted{color:var(--muted)}.green{color:var(--g)}.red{color:var(--r)}.cyan{color:var(--c)}.amber{color:var(--a)}
table{width:100%;border-collapse:collapse;margin-top:8px}th,td{padding:7px;border-bottom:1px solid var(--bd);text-align:left;font-size:13px}th{color:var(--muted)}
ul{padding-left:18px}.warn{color:#ffbd72}
.panel-title{font-size:14px;color:var(--muted);margin:0 0 8px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.kpi{font-size:20px}
code{color:#8bd4ff;background:#0d1824;padding:1px 4px;border-radius:4px}
@media (max-width:1280px){.grid{grid-template-columns:repeat(2,1fr)} .row{grid-template-columns:1fr}}
</style></head><body>
"""


def _fmt_num(x, digits=2):
    try:
        if isinstance(x, (int, float)):
            return f"{x:,.{digits}f}" if digits else str(x)
    except Exception:
        return "--"
    return str(x)


def _fmt_pct(x):
    try:
        return f"{float(x)*100:.1f}%"
    except Exception:
        return "--"


def _status_badge(ok: bool):
    return f"<span class=\"{'green' if ok else 'red'}\">{'✓' if ok else '✗'} {'接通' if ok else '未接入'}</span>"


def _demo_flag():
    return f"<span class='badge'>[{DEMO_TAG}] 示例数据</span>"


def _data_notice():
    return live_data_notice("DEMO", dashboard_status())


def _safe(v):
    return escape(str(v)) if v is not None else "--"


def _safe_float_percent(value) -> float:
    try:
        return float(str(value).replace(",", "").strip()) / 100.0
    except (TypeError, ValueError):
        return 0.0


def _quote_message(value: str) -> str:
    return quote(str(value), safe="")


def _candidate_rows(snapshot):
    mp = classify_market_phase(snapshot["market"])
    rows = []
    for c in snapshot["candidates"]:
        r = dict(c)
        r["market_temp"] = mp["temperature"]
        r["market_phase"] = mp["phase"]
        r.setdefault("data_quality", 0.98)
        x = classify_candidate(r)
        x["sector_heat"] = c.get("sector_strength")
        x["trend"] = c.get("trend_score")
        x["buy_timing"] = c.get("buy_timing_score")
        x["rr"] = c.get("rr")
        rows.append(x)
    return rows, mp


def _pick_a_level(rows):
    a_candidates = [x for x in rows if x.get("pool") == "A"]
    if not a_candidates:
        return []
    return sorted(a_candidates, key=lambda x: x.get("score", 0), reverse=True)


def _a_exec_card(name, item):
    veto = item.get("veto") or "-"
    score = item.get("score", 0)
    p = item.get("t1_probability", 0)
    return (
        f"<div class='card w4'><div class='panel-title'>A级执行｜{name}</div>"
        "<div class='row'>"
        f"<div><p>评分拆解</p>"
        f"<p>基础评分：<b>{_fmt_num(score,1)}</b></p>"
        f"<p>趋势得分：{_safe(item.get('trend'))}</p>"
        f"<p>资金得分：{_safe(item.get('main_flow_score'))}</p>"
        f"<p>筹码锁定：{_safe(item.get('chip_lock_score'))}</p>"
        f"<p>买点评分：{_safe(item.get('buy_timing'))}</p>"
        "</div>"
        f"<div><p>交易信号细节</p>"
        f"<p>T+1：{_safe(item.get('t1_probability'))}</p>"
        f"<p>隔夜风险：{_safe(item.get('overnight_risk'))}</p>"
        f"<p>板块：{_safe(item.get('sector'))}</p>"
        f"<p>动作：{_safe(item.get('action'))}</p>"
        f"<p>支撑：{_safe(item.get('defense'))} ｜ 目标：{_safe(item.get('target'))}</p>"
        f"<p>风险否决：{_safe(veto)}</p>"
        "</div></div>"
        "<table><tr><th>证据维度</th><th>状态</th><th>证据值</th></tr>"
        f"<tr><td>资金</td><td class=\"green\">已具备</td><td>主力资金={_safe(item.get('main_flow_score'))}</td></tr>"
        f"<tr><td>筹码</td><td class=\"green\">已具备</td><td>筹码锁定={_safe(item.get('chip_lock_score'))}</td></tr>"
        f"<tr><td>竞价</td><td class=\"green\">已具备</td><td>竞价质量={_safe(item.get('auction_quality'))}</td></tr>"
        "</table>"
        "</div>"
    )


def _render_nav(page_key, mode="DEMO"):
    parts = ["<nav>"]
    for key, label in PAGE_NAV:
        cls = "sel" if key == page_key else ""
        parts.append(f"<a class='{cls}' href='/?page={key}&mode={mode}'>{escape(label)}</a>")
    parts.append("</nav>")
    return "".join(parts)


def _render_overview(snapshot, rows, market, pool):
    top = _pick_a_level(rows)[:3]
    kpi_cards = (
        f"<div class='card'><p class='panel-title'>市场阶段</p><div class='big'>{market['temperature']}</div>"
        f"<p>{escape(market['phase'])} / {escape(snapshot['time'])}</p></div>"
        f"<div class='card'><p class='panel-title'>候选池</p><div class='kpi'>{pool['A']} / {pool['B']} / {pool['C']}</div>"
        "<p class='muted'>A / B / C</p></div>"
        f"<div class='card'><p class='panel-title'>样本健康</p><div class='kpi'>{_fmt_pct(snapshot['candidates'][0].get('daily_pct',0)/100)}</div>"
        "<p class='muted'>日内涨跌示例字段（演示）</p></div>"
        f"<div class='card'><p class='panel-title'>指数快照</p><p>上证 {snapshot['indices'][0]['value']}</p>"
        f"<p class='muted'>涨跌 {snapshot['indices'][0]['pct']:+.2f}%</p></div>"
    )

    rows_html = []
    for item in top:
        rows_html.append(
            f"<tr><td><a href='/?page=stock_detail&code={escape(item['ts_code'])}'>"
            f"{escape(item.get('name'))}</a></td><td>{_fmt_num(item.get('score',0),1)}</td>"
            f"<td>{_fmt_pct(item.get('t1_probability',0))}</td><td>{_safe(item.get('market_phase'))}</td>"
            f"<td>{_safe(item.get('sector'))}</td></tr>"
        )

    return (
        f"<div class='grid'>{kpi_cards}"
        f"<div class='card w2'><p class='panel-title'>A级 Top3</p><table><tr><th>标的</th><th>评分</th><th>T+1</th><th>阶段</th><th>板块</th></tr>{''.join(rows_html)}</table></div>"
        f"<div class='card w2'><p class='panel-title'>热门主题</p><ul>{''.join(f'<li>{_safe(t["name"])}</li>' for t in snapshot['themes'])}</ul></div>"
        "<div class='card w2'><p class='panel-title'>今日摘要</p>"
        f"<p>上证涨跌 {snapshot['indices'][0]['pct']}%，深证涨跌 {snapshot['indices'][1]['pct']}%</p>"
        f"<p>涨停 {snapshot['market']['limit_up']}，跌停 {snapshot['market']['limit_down']}，破板率 {snapshot['market']['broken_rate']}%</p></div>"
        f"{_data_notice()}"
        "</div>"
    )


def _render_a_exec(rows):
    rows = _pick_a_level(rows)
    cards = [_a_exec_card(c.get("name"), c) for c in rows] or ["<div class='card w4'>当前A+池无可交易标的，演示模式下保留空态。</div>"]
    return f"<div class='grid'>{''.join(cards)}{_data_notice()}</div>"


def _sniper_thresholds(item):
    score = item.get("score", 0)
    risk = (item.get("event_risk", 0) + item.get("distribution_risk", 0)) / 2
    criteria = [
        ("Meta概率 >= 0.85", score >= 85, _fmt_num(score,1)),
        ("CPCV Precision >= 0.70", True, "演示值 74%"),
        ("Conformal 集合非空", True, "{1}"),
        ("无风险否决", not bool(item.get("veto")), _safe(item.get("veto") or "无")),
        ("T+1上涨概率 >= 0.62", float(item.get("t1_probability",0)) >= 0.62, _fmt_pct(item.get("t1_probability",0)/100)),
        ("隔夜风险 <= 12", (item.get("overnight_risk", 0) <= 12), _safe(item.get("overnight_risk"))),
        ("板块热度 >= 80", item.get("sector_strength", 0) >= 80, _safe(item.get('sector_strength'))),
        ("执行可达性", item.get("action") not in ("观察", "观望"), _safe(item.get('action'))),
    ]
    html = ["<div class='card w4'><p class='panel-title'>A+ 狙击规则通过状态</p><table><tr><th>门槛</th><th>结果</th><th>观测值</th></tr>"]
    for name, ok, val in criteria:
        cls = "green" if ok else "red"
        icon = "✓" if ok else "✗"
        html.append(f"<tr><td>{escape(name)}</td><td class='{cls}'>{icon}</td><td>{escape(val)}</td></tr>")
    html.append("</table></div>")
    html.append(f"<div class='card w2'><p class='panel-title'>Sniper 评分</p><p class='kpi'>{_fmt_num(score,1)}</p><p class='muted'>基于 A+ 审核规则聚合</p></div>")
    return "<div class='grid'>" + "".join(html) + _data_notice() + "</div>"


def _render_meta(p):
    return (
        "<div class='grid'><div class='card w2'><p class='panel-title'>Meta-Label</p>"
        f"<p>原始概率: {_safe(p['meta']['prob'])}</p><p>校准后: {_safe(p['meta']['calibrated'])}</p>"
        f"<p>角色: {_safe(p['meta']['role'])}</p><p>授权: {'已批准' if p['meta']['approved'] else '未批准，禁止实盘'}</p>"
        "</div><div class='card w2'><p class='panel-title'>解释层</p><p>规则：标签再标定 + 单向校正。</p>"
        "<p>目标：降低乐观偏差，控制条件外推。</p><p>漂移保护：当置信度低于阈值会降级。</p></div>"
        f"<div class='card w2'><p class='panel-title'>置信区间</p><p class='kpi'>{_safe(p['conformal']['alpha'])}</p><p class='muted'>校准参数示例</p></div>"
        f"<div class='card w2'><p class='panel-title'>未授权提示</p><p>当前环境未接入 PIT 历史样本。</p>"
        f"<p>下一步: data_provenance + tushare_client 对接。</p></div>{_data_notice()}</div>"
    )


def _render_conformal(p):
    return (
        "<div class='grid'><div class='card w4'><p class='panel-title'>Conformal 预测集合</p>"
        f"<p>集合描述：{_safe(p['conformal']['set'])}</p><p>置信水平：{_safe(p['conformal']['alpha'])}</p><p>状态：{_safe(p['conformal']['status'])}</p></div>"
        "<div class='card w2'><p class='panel-title'>控制目标</p><p class='kpi'>FDR &gt; 目标下限</p>"
        "<p>解释：当不确定性高时输出 abstain，避免过度交易。</p></div>"
        "<div class='card w2'><p class='panel-title'>误差控制</p><p>误差边界由历史窗口 + 当前质量分位数联合控制。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_cpcv(p):
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>CPCV 评估</p>"
        f"<p>Precision: {_safe(p['cpcv']['precision'])}</p><p>Recall: {_safe(p['cpcv']['recall'])}</p>"
        f"<p>Brier: {_safe(p['cpcv']['brier'])}</p><p class='muted'>{_safe(p['cpcv']['note'])}</p></div>"
        f"<div class='card'><p class='panel-title'>有效样本</p><p>{_safe(p['uniqueness']['effective'])} / {_safe(p['uniqueness']['raw'])}</p>"
        "<p>重叠样本降权，降低乐观估计。</p></div>"
        f"<div class='card w2'><p class='panel-title'>状态</p><p class='kpi'>PASS</p><p class='muted'>样本口径一致且可复核。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_factor(stat):
    factors = stat['factor_health']
    factor_rows = [
        f"<tr><td>{escape(x['name'])}</td><td>{_fmt_num(x['rank_ic'],3)}</td><td>{escape(x['status'])}</td></tr>"
        for x in factors
    ]
    return (
        "<div class='grid'><div class='card w3'><p class='panel-title'>因子验证矩阵</p><table><tr><th>因子</th><th>rank_ic</th><th>状态</th></tr>"
        + "".join(factor_rows) + "</table></div>"
        "<div class='card'><p class='panel-title'>回归模型</p>"
        f"<p>角色：{_safe(stat['ml']['role'])}</p><p>后端：{_safe(stat['ml']['backend'])}</p><p>状态：{_safe(stat['ml']['status'])}</p></div>"
        "<div class='card'><p class='panel-title'>剔除机制</p><p>高相关因子与重复项剔除后再训练。</p>"
        f"<p>样本保留：{_safe(stat['ml']['top_rank'])}</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_limitup(limitup):
    eco = limitup["ecology"]
    first_board = limitup["first_board_potential"]
    high_board = limitup["high_board_risk"]
    first_rows = [
        f"<tr><td>{escape(x['name'])}</td><td>{_safe(x.get('sector'))}</td><td>{_safe(x.get('board_potential'))}</td><td>{_safe(x.get('board_grade'))}</td></tr>"
        for x in first_board[:8]
    ]
    high_rows = [
        f"<tr><td>{escape(x.get('name'))}</td><td>{_safe(x.get('board'))}</td><td>{_safe(x.get('leader_risk'))}</td></tr>"
        for x in high_board[:8]
    ]
    return (
        "<div class='grid'><div class='card w4'><p class='panel-title'>连板生态</p>"
        f"<p>本地分层：最大连板 {eco['max_board']} 板；梯队数 {eco['multi_board_count']}</p>"
        "<p>状态：梯队扩散与接力并存（演示）</p></div>"
        "<div class='card'><p class='panel-title'>梯队摘要</p><table><tr><th>首板潜力</th><th>板块</th><th>潜力分</th><th>等级</th></tr>"
        + "".join(first_rows) + "</table></div>"
        "<div class='card'><p class='panel-title'>高标风险</p><table><tr><th>标的</th><th>板数</th><th>高标风险</th></tr>"
        + "".join(high_rows) + "</table></div>"
        "<div class='card w2'><p class='panel-title'>首板策略入口</p><p>首板池优先级与次日封装用于执行预审。</p>"
        "<p class='warn'>注：未接入真实连板快照源，当前为示例。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_sim(stat):
    exe = stat["execution"]
    return (
        "<div class='grid'><div class='card w2'><p class='panel-title'>执行参数</p>"
        f"<p>T+1：{_safe(exe['t1'])}</p><p>涨跌停成交：{_safe(exe['limit'])}</p></div>"
        "<div class='card w2'><p class='panel-title'>费用与滑点</p>"
        f"<p>费用：{_safe(exe['fees'])}</p><p>滑点：{_safe(exe['slippage'])}</p></div>"
        "<div class='card w4'><p class='panel-title'>执行模拟</p><p>保守触发：若出现风控否决不发单。</p><p>示例结果：按可成交价差与可买卖量回测。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_monte_carlo(stat):
    mc = stat['monte_carlo']
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>盈利分布</p>"
        f"<p class='kpi'>{_safe(mc['profit'])}</p><p>目标达成 {mc['target']}</p></div>"
        f"<div class='card'><p class='panel-title'>回撤分布</p><p class='kpi'>{_safe(mc['dd20'])}</p><p>20%回撤阈值越过概率</p></div>"
        f"<div class='card w2'><p class='panel-title'>方法说明</p>"
        "<p>蒙特卡洛按路径采样，覆盖滑点和滑移不确定性。</p>"
        "<p class='warn'>当前为示例模拟，需对接真实成交与盘口序列。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_drift(stat):
    d = stat['drift']
    dsr = stat.get('dsr', {'prob': '样本不足', 'status': '未生成'})
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>漂移监控</p>"
        f"<p>PSI：{_safe(d['psi'])}</p><p>近期胜率：{_safe(d['recent_win'])}</p>"
        f"<p>状态：{_safe(d['level'])}</p></div>"
        f"<div class='card'><p class='panel-title'>D-SR</p><p class='kpi'>{_safe(dsr['prob'])}</p>"
        f"<p>状态：{_safe(dsr['status'])}</p></div>"
        f"<div class='card w2'><p class='panel-title'>漂移动作</p>"
        "<p>PSI过高时触发阈值退化与模型降级。</p><p>样本窗口重估触发自动降级。</p></div>"
        f"{_data_notice()}</div>"
    )


def _render_health():
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>数据入口</p>"
        f"<p>Tushare：{_status_badge(False)}</p><p>PIT：{_status_badge(PIT_GATEWAY_ENABLED)}</p>"
        f"<p>token配置：{_safe('未读取到 secrets.local.json 中的 TUSHARE_TOKEN')}</p></div>"
        "<div class='card'><p class='panel-title'>接口授权</p><p>数据源：未授权</p><p>请在 secrets.local.json 中配置 Token 与 PIT 凭证。</p></div>"
        "<div class='card w2'><p class='panel-title'>下一步指引</p>"
        "<p>1) 配置真实数据源密钥；2) 切换到 PIT 接口；3) 运行 2_TEST.bat 进行路由与回测联测。</p>"
        f"<p class=\"warn\">当前接口位为 {escape(PIT_GATEWAY_URL)}</p></div>"
        f"<div class='card w4'><p class='panel-title'>检查建议</p><ul><li>检查网络与证书链</li><li>核对接口配额</li><li>确认 secrets.local.json 可读</li></ul>"
        f"<p class=\"muted\">{_safe('数据未接入')}: 所有统计均为DEMO示例。</p></div></div>"
    )


def _render_stock_detail(snapshot, code):
    for row in snapshot["candidates"]:
        if row.get("ts_code") == code:
            score = classify_candidate(dict(row, market_temp=70, market_phase="主升", data_quality=.98))
            detail_rows = [
                ("日内涨跌", f"{row.get('daily_pct')}%"),
                ("评分", _fmt_num(score.get('score'),1)),
                ("T+1", _fmt_pct(score.get('t1_probability',0))),
                ("隔夜风险", _safe(score.get('overnight_risk'))),
                ("信号", _safe(score.get('veto') or '无')),
                ("动作", _safe(score.get('action'))),
            ]
            lines = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k,v in detail_rows)
            return (
                "<div class='card w4'><div class='panel-title'>股票详情</div>"
                f"<h3>{escape(row.get('name'))}｜{escape(code)}</h3>"
                f"<table>{lines}</table>"
                "<p class=\"muted\">证据来源：资金、筹码、竞价子模块均为示例演示。</p>"
                f"{_data_notice()}"
                "</div>"
            )
    return "<div class='card w4'><p>未找到标的：参数代码不在当前快照</p></div>"


def _portfolio_input(label, name, value="", input_type="text", required=False, placeholder="", step="", readonly=False):
    requirement = " required" if required else ""
    step_attr = f" step='{escape(step)}'" if step else ""
    readonly_attr = " readonly" if readonly else ""
    return (
        f"<label>{escape(label)}<input name='{escape(name)}' type='{escape(input_type)}' "
        f"value='{escape(str(value or ''))}' placeholder='{escape(placeholder)}'{step_attr}{readonly_attr}{requirement}></label>"
    )


def _render_portfolio(mode, edit_code="", message="", error=""):
    monitor = PortfolioMonitor()
    data = monitor.snapshot()
    edit = monitor.position(edit_code) or {}
    settings = data["settings"]
    total = data["total_value"]
    exposure = data["exposure"]
    notice = "<p class='portfolio-note'>仅保存手工录入的持仓信息；不会连接券商、不会自动交易、不会推送买卖指令。</p>"
    feedback = ""
    if message:
        feedback = f"<div class='portfolio-feedback success'>{escape(message)}</div>"
    if error:
        feedback = f"<div class='portfolio-feedback error'>{escape(error)}</div>"
    setting_form = (
        "<div class='card w2 portfolio-card'><p class='panel-title'>账户设置</p>"
        "<form method='post' action='/api/portfolio/settings' class='portfolio-form compact'>"
        f"<input type='hidden' name='mode' value='{escape(mode)}'>"
        + _portfolio_input("账户总资产（元）", "nav", settings["nav"], "number", False, "例如 100000")
        + _portfolio_input("当日账户盈亏（%）", "daily_pnl_pct", settings["daily_pnl_pct"] * 100, "number", False, "可选，例如 -1.2")
        + "<button class='portfolio-button' type='submit'>保存账户设置</button></form></div>"
    )
    add_form = (
        "<div class='card w2 portfolio-card'><p class='panel-title'>"
        + ("更新持仓" if edit else "录入持仓")
        + "</p><form method='post' action='/api/portfolio/position' class='portfolio-form'>"
        f"<input type='hidden' name='mode' value='{escape(mode)}'>"
        + "<p class='portfolio-note'>只需填写股票代码、买入价、数量和日期。名称和最新价均来自带时间戳的本地盘中PIT快照；没有真实报价时不估值。</p>"
        + _portfolio_input("股票代码", "ts_code", edit.get("ts_code", ""), "text", True, "例如 600519 或 600519.SH")
        + _portfolio_input("股票名称（自动识别）", "name", edit.get("name", ""), "text", False, "输入代码后自动显示", readonly=True)
        + "<p class='portfolio-lookup muted' data-portfolio-lookup>输入 6 位代码后自动读取名称和最新报价</p>"
        + _portfolio_input("买入价（元，成本可保留三位小数）", "cost_price", edit.get("cost_price", ""), "number", True, "例如 12.345", step="0.001")
        + _portfolio_input("持仓数量（股）", "quantity", edit.get("quantity", ""), "number", True)
        + _portfolio_input("买入日期", "entry_date", edit.get("entry_date", ""), "date")
        + "<input type='hidden' name='current_price' value=''><input type='hidden' name='sector' value=''>"
        + "<input type='hidden' name='stop_price' value=''><input type='hidden' name='target_price' value=''><input type='hidden' name='note' value=''>"
        + "<div class='portfolio-actions'><button class='portfolio-button' type='submit'>保存持仓</button>"
        + f"<a class='portfolio-link' href='/?page=portfolio&amp;mode={escape(mode)}'>清空表单</a></div></form></div>"
        + "<script>(function(){const code=document.querySelector(\"input[name='ts_code']\"),name=document.querySelector(\"input[name='name']\"),hint=document.querySelector('[data-portfolio-lookup]');if(!code||!name)return;async function lookup(){const raw=code.value.trim();if(!/^\\d{6}(?:\\.(?:SH|SZ|BJ))?$/i.test(raw)){return;}hint.textContent='正在读取股票信息...';try{const r=await fetch('/api/portfolio/lookup?code='+encodeURIComponent(raw));const d=await r.json();name.value=d.name||'';hint.textContent=d.name?('已识别：'+d.name+(d.last_price?'｜最新价 '+Number(d.last_price).toFixed(2)+'｜'+String(d.price_observed_at||'').replace('T',' '):'｜暂无带时间戳的真实报价')):'未识别，请检查代码或数据源';}catch(e){hint.textContent='暂时无法读取，请保存时重试';}}code.addEventListener('change',lookup);code.addEventListener('blur',lookup);if(code.value)lookup();})();</script>"
    )
    rows = []
    for item in data["positions"]:
        allocation = "--" if item["allocation"] is None else _fmt_pct(item["allocation"])
        pnl_class = "market-up" if item["pnl"] is not None and item["pnl"] >= 0 else "market-down"
        rows.append(
            "<tr>"
            f"<td><b>{escape(item['name'])}</b><br><span class='muted'>{escape(item['ts_code'])}</span></td>"
            f"<td>{escape(item.get('sector') or '--')}</td><td>{_fmt_num(item['cost_price'])}</td><td>{_fmt_num(item['current_price']) if item['current_price'] is not None else '--'}<br><span class='muted'>{escape(item.get('price_observed_at') or '暂无真实报价')}</span></td>"
            f"<td>{_fmt_num(item['quantity'], 0)}</td><td>{_fmt_num(item['market_value']) if item['market_value'] is not None else '--'}</td>"
            f"<td class='{pnl_class}'>{_fmt_num(item['pnl']) if item['pnl'] is not None else '--'}<br>{_fmt_pct(item['pnl_pct']) if item['pnl_pct'] is not None else '--'}</td>"
            f"<td>{allocation}</td><td>{_fmt_num(item.get('stop_price') or item.get('auto_stop') or 0) if (item.get('stop_price') or item.get('auto_stop')) else '--'} / {_fmt_num(item.get('target_price') or item.get('auto_target') or 0) if (item.get('target_price') or item.get('auto_target')) else '--'}<br><span class='muted'>{escape(item.get('defense_action') or 'HOLD')}</span></td>"
            f"<td>{escape(item['status'])}<br><span class='muted'>{escape(item.get('defense_reason') or '等待PIT证据')}</span></td>"
            f"<td><a class='portfolio-link' href='/?page=portfolio&amp;mode={escape(mode)}&amp;edit={escape(item['ts_code'])}'>更新</a>"
            "<form method='post' action='/api/portfolio/delete' class='portfolio-inline'>"
            f"<input type='hidden' name='mode' value='{escape(mode)}'><input type='hidden' name='ts_code' value='{escape(item['ts_code'])}'>"
            "<button class='portfolio-delete' type='submit'>删除</button></form></td></tr>"
        )
    table = (
        "<div class='card w4 portfolio-card'><p class='panel-title'>当前持仓</p><div class='focus-table-shell'>"
        "<table class='focus-wide'><tr><th>标的</th><th>板块</th><th>成本价</th><th>当前价</th><th>数量</th><th>市值</th><th>浮动盈亏</th><th>仓位</th><th>动态防守 / 目标</th><th>状态</th><th>操作</th></tr>"
        + ("".join(rows) if rows else "<tr><td colspan='11' class='muted'>尚未录入持仓。先在上方填写账户总资产，再录入已建仓股票。</td></tr>")
        + "</table></div></div>"
    )
    risk_lines = data["risks"] or ["当前未触发默认风险线。风险线仅用于提示，不会自动卖出。"]
    risk_html = "".join(f"<li>{escape(text)}</li>" for text in risk_lines)
    metrics = (
        "<div class='card focus-metric'><p class='panel-title'>持仓市值</p><p class='kpi'>" + _fmt_num(total, 0) + "</p><p>元</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>总仓位</p><p class='kpi'>" + ("--" if exposure is None else _fmt_pct(exposure)) + "</p><p>相对账户总资产</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>持仓数量</p><p class='kpi'>" + str(len(data["positions"])) + "</p><p>不设系统数量上限</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>动态防守风险估计</p><p class='kpi'>" + _fmt_num(data["estimated_stop_risk"], 0) + "</p><p>手工线优先，否则采用已确认动态防守线</p></div>"
    )
    return (
        "<div class='grid portfolio-grid'>" + metrics + setting_form + add_form
        + "<div class='card w4 portfolio-card'><p class='panel-title'>风险提示</p><ul class='portfolio-risk-list'>" + risk_html + "</ul>" + notice + "</div>"
        + table + feedback + "</div>"
    )


def _json_payload(snapshot, page, mode="DEMO", status=None):
    status = status or dashboard_status()
    rows, mp = _candidate_rows(snapshot)
    p = precision_demo()
    s = stat_demo()
    lim = LimitupRuntime().analyze_day(today_rows(), "20260902", yesterday_rows(), 25.8, 9, lambda _: {})

    if page == "a_exec":
        cards = [_pick_a_level(rows)]
    return {
        "market": mp,
        "pages": [k for k,_ in PAGE_NAV],
        "a_count": len([x for x in rows if x.get("pool") == "A"]),
        "pools": {"A": len([x for x in rows if x.get('pool') == 'A']), "B": len([x for x in rows if x.get('pool') == 'B']), "C": len([x for x in rows if x.get('pool') == 'C'])},
        "meta": p.get("meta"),
        "conformal": p.get("conformal"),
        "cpcv": p.get("cpcv"),
        "selective": p.get("selective"),
        "sniper": p.get("sniper"),
        "drift": s["drift"],
        "monte_carlo": s["monte_carlo"],
        "execution": s["execution"],
        "factor_health": s["factor_health"],
        "limitup": {"max_board": lim["ecology"]["max_board"], "multi_board_count": lim["ecology"]["multi_board_count"]},
        "source": {"connected": status.get("live_ready", False), "pit_gateway": status.get("pit_ready", False), "mode": mode, "updated_at": status.get("updated_at")},
        "real_statistics": {"ready": status.get("statistics_ready", False), "sample_n": status.get("real_sample_n", 0), "message": sample_message(status)},
    }


def render_page(page, snapshot, rows, mp, p, s, lim, code=None, mode="DEMO", status=None, edit_code="", message="", error=""):
    status = status or dashboard_status()
    mode = resolve_mode(mode, status)
    if page == "portfolio":
        body = _render_portfolio(mode, edit_code, message, error)
    elif page == "sources":
        body = render_sources(status, mode)
    elif page == "health":
        body = render_health(status, mode)
    elif mode != "DEMO":
        body = render_real_page(page, status, mode, code or "")
    elif page == "overview":
        body = _render_overview(snapshot, rows, mp, {"A": len([x for x in rows if x.get('pool') == 'A']), "B": len([x for x in rows if x.get('pool') == 'B']), "C": len([x for x in rows if x.get('pool') == 'C'])})
    elif page == "a_exec":
        body = _render_a_exec(rows)
    elif page == "sniper":
        rows_a = _pick_a_level(rows)
        body = rows_a[0] and _sniper_thresholds(rows_a[0]) if rows_a else "<div class='card w4'>无A类候选可评估</div>"
    elif page == "meta":
        body = _render_meta(p)
    elif page == "conformal":
        body = _render_conformal(p)
    elif page == "cpcv":
        body = _render_cpcv(p)
    elif page == "factor":
        body = _render_factor(s)
    elif page == "limitup":
        body = _render_limitup(lim)
    elif page == "sim":
        body = _render_sim(s)
    elif page == "mc":
        body = _render_monte_carlo(s)
    elif page == "drift":
        body = _render_drift(s)
    elif page == "stock_detail":
        body = _render_stock_detail(snapshot, code or "")
    else:
        body = "<div class='card w4'>未知页面</div>"

    return (
        HTML_HEAD
        + f"<header><h1>🎯 A股机会雷达 V8.6.6</h1><div class=sub>Rich Workbench / Server Navigation</div><div class='mode-zone'>{mode_controls(page, mode)}{mode_header(mode, status)}</div><span class=ok>● READY</span></header>"
        + "<div class=wrap>" + _render_nav(page, mode) + "<main>" + body + "</main></div></body></html>"
    )


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        page = qs.get("page", ["overview"])[0]
        status = dashboard_status()
        requested_mode = qs.get("mode", ["AUTO"])[0]
        mode = resolve_mode(requested_mode, status)
        path_for_route = parsed.path.lower()
        if path_for_route.startswith("/api"):
            if path_for_route == "/api/health":
                body = {
                    "status": "ok",
                    "version": "V8.6.6",
                    "page": page,
                    "data_mode": mode,
                    "source_connected": status.get("live_ready", False),
                    "pit_ready": status.get("pit_ready", False),
                    "statistics_ready": status.get("statistics_ready", False),
                }
                encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self._write_json(encoded)
                return

            if path_for_route == "/api/data-sources":
                self._write_json(json.dumps(status, ensure_ascii=False).encode("utf-8"))
                return

            if path_for_route == "/api":
                snap = dashboard_snapshot()
                rows, mp = _candidate_rows(snap)
                p = precision_demo(); s = stat_demo(); lim = LimitupRuntime().analyze_day(today_rows(), "20260902", yesterday_rows(), 25.8, 9, lambda _: {})
                payload = _json_payload(snap, page, mode, status)
                self._write_json(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return

            if path_for_route.startswith("/api/page"):
                name = qs.get("name", ["overview"])[0]
                snap = dashboard_snapshot(); rows, mp = _candidate_rows(snap)
                p = precision_demo(); s = stat_demo(); lim = LimitupRuntime().analyze_day(today_rows(), "20260902", yesterday_rows(), 25.8, 9, lambda _: {})
                real_count = len(_pick_a_level(rows)) if mode == "DEMO" else 0
                self._write_json(json.dumps({"page": name, "content": "ok", "a_count": real_count, "data_mode": mode, "statistics_ready": status.get("statistics_ready", False), "sample_message": sample_message(status)}, ensure_ascii=False).encode("utf-8"))
                return

            if path_for_route.startswith("/api/stock"):
                code = qs.get("code", [""])[0]
                snap = dashboard_snapshot()
                item = next((x for x in snap["candidates"] if x.get("ts_code") == code), None) if mode == "DEMO" else None
                detail = {"found": bool(item)}
                if item:
                    score = classify_candidate(dict(item, market_temp=70, market_phase="主升", data_quality=.98))
                    detail.update({"code": code, "name": item.get("name"), "market": item.get("market_phase"), "score": score.get("score"), "action": score.get("action")})
                elif mode != "DEMO":
                    detail.update({"code": code, "data_mode": mode, "reason": "没有已验证的真实 signal_snapshot，禁止回退 DEMO"})
                self._write_json(json.dumps(detail, ensure_ascii=False).encode("utf-8"))
                return

            if path_for_route == "/api/portfolio/lookup":
                try:
                    payload = PortfolioMonitor().lookup_security(qs.get("code", [""])[0])
                    self._write_json(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                except ValueError as exc:
                    self._write_json(json.dumps({"name": "", "error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                return

            self.send_error(404, "Not Found")
            return

        # HTML pages
        snap = dashboard_snapshot()
        rows, mp = _candidate_rows(snap)
        p = precision_demo()
        s = stat_demo()
        lim = LimitupRuntime().analyze_day(today_rows(), "20260902", yesterday_rows(), 25.8, 9, lambda _: {})
        if parsed.path == "/" or parsed.path == "":
            if page == "stock_detail":
                html = render_page("stock_detail", snap, rows, mp, p, s, lim, code=qs.get("code", [""])[0], mode=mode, status=status)
            else:
                html = render_page(page, snap, rows, mp, p, s, lim, mode=mode, status=status, edit_code=qs.get("edit", [""])[0], message=qs.get("message", [""])[0], error=qs.get("error", [""])[0])
            self._write_html(html)
            return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(max(0, length)).decode("utf-8", errors="replace")
        form = {key: values[0] if values else "" for key, values in parse_qs(raw, keep_blank_values=True).items()}
        mode = form.get("mode", "LIVE").upper()
        redirect_base = f"/?page=portfolio&mode={escape(mode)}"
        try:
            monitor = PortfolioMonitor()
            if parsed.path == "/api/portfolio/settings":
                monitor.save_settings(form.get("nav"), _safe_float_percent(form.get("daily_pnl_pct")))
                self._redirect(redirect_base + "&message=账户设置已保存")
                return
            if parsed.path == "/api/portfolio/position":
                code = monitor.save_position(form)
                self._redirect(redirect_base + "&message=" + _quote_message(f"{code} 持仓已保存"))
                return
            if parsed.path == "/api/portfolio/delete":
                monitor.delete_position(form.get("ts_code", ""))
                self._redirect(redirect_base + "&message=持仓已删除")
                return
            self.send_error(404, "Not Found")
        except ValueError as exc:
            self._redirect(redirect_base + "&error=" + _quote_message(str(exc)))

    def _redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _write_json(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_html(self, body: str):
        body = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}/"
    threading.Timer(.4, lambda: webbrowser.open(url)).start()
    print("A股机会雷达 V8.6.6 Server Navigation", url)
    server = ThreadingHTTPServer((HOST, PORT), H)
    server.serve_forever()
