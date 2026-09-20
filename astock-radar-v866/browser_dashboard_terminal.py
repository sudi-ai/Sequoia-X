"""Clean Chinese market-terminal shell for the V8.6.6 dashboard.

Only presentation is replaced. Routes, modes, data and strategy calculations
continue to come from browser_dashboard.py.
"""

from html import escape
from http.server import ThreadingHTTPServer
import os

import browser_dashboard as core


_render_original = core.render_page


NAV_GROUPS = (
    ("今日决策", (
        ("overview", "市", "今日总览", "市场状态与机会"),
        ("a_exec", "A", "A级执行", "严格门槛信号"),
        ("sniper", "A+", "A+狙击", "最高等级观察"),
        ("limitup", "板", "连板观察", "涨停与板块扩散"),
    )),
    ("研究验证", (
        ("meta", "样", "样本表现", "真实结果统计"),
        ("conformal", "界", "置信边界", "预测不确定范围"),
        ("cpcv", "验", "样本外检验", "防止回测过拟合"),
        ("factor", "因", "因子分析", "证据贡献拆解"),
        ("sim", "执", "执行模拟", "滑点与成交能力"),
        ("mc", "压", "风险压力", "极端情景测试"),
        ("drift", "稳", "稳定性监控", "模型与数据漂移"),
    )),
    ("数据系统", (
        ("health", "健", "数据健康", "完整度与时效"),
        ("sources", "源", "数据源与权限", "付费接口状态"),
    )),
)


def render_nav(page_key, mode="DEMO"):
    mode = mode if mode in {"DEMO", "LIVE", "PIT"} else "DEMO"
    out = [
        "<nav class='terminal-nav' aria-label='功能工作台'>",
        "<div class='workspace-title'><span class='workspace-dot'></span>"
        "<span><b>功能工作台</b><small>WORKSPACE</small></span></div>",
    ]
    for title, items in NAV_GROUPS:
        out.append("<p class='nav-group'>%s</p>" % escape(title))
        for key, icon, label, detail in items:
            selected = " selected" if key == page_key else ""
            out.append(
                "<a class='terminal-nav-item%s' href='/?page=%s&amp;mode=%s'>"
                "<span class='nav-icon'>%s</span>"
                "<span class='nav-text'><b>%s</b><small>%s</small></span>"
                "<span class='nav-arrow'>›</span></a>"
                % (
                    selected,
                    escape(key),
                    escape(mode),
                    escape(icon),
                    escape(label),
                    escape(detail),
                )
            )
    out.extend((
        "<div class='validation-state'><i></i><span><b>验证观察模式</b>"
        "<small>不自动交易 · 不生成虚假胜率</small></span></div>",
        "</nav>",
    ))
    return "".join(out)


CSS = r"""
:root {
  --bg: #edf1f5;
  --paper: #ffffff;
  --paper-2: #f7f9fc;
  --nav: #0a213a;
  --nav-2: #071a2e;
  --top: #06182b;
  --blue: #2475c7;
  --blue-2: #155594;
  --blue-soft: #e8f2fc;
  --ink: #17283b;
  --text: #3f5368;
  --muted: #758599;
  --line: #d6dfe8;
  --line-2: #c6d2df;
  --up: #d83b32;
  --down: #168d62;
  --warn: #aa731b;
}

* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0 !important;
  color: var(--ink) !important;
  background: var(--bg) !important;
  font-family: "Microsoft YaHei UI", "Source Han Sans SC", "PingFang SC", sans-serif !important;
  font-size: 14px !important;
  line-height: 1.55 !important;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}

header {
  position: sticky !important;
  top: 0 !important;
  z-index: 80 !important;
  min-height: 62px !important;
  padding: 8px 18px !important;
  display: grid !important;
  grid-template-columns: 225px minmax(0, 1fr) auto !important;
  grid-template-rows: 25px 18px !important;
  align-items: center !important;
  column-gap: 20px !important;
  color: #fff !important;
  background: linear-gradient(90deg, #06182b, #0a2948 58%, #0c355c) !important;
  border: 0 !important;
  border-bottom: 1px solid #244c73 !important;
  box-shadow: 0 3px 12px rgba(4, 20, 38, .22) !important;
}
header::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, #3185d3, transparent 62%);
}
header h1 {
  grid-column: 1 !important;
  grid-row: 1 !important;
  margin: 0 !important;
  color: #fff !important;
  font-size: 18px !important;
  font-weight: 760 !important;
  line-height: 1.2 !important;
  letter-spacing: -.03em !important;
}
header .sub {
  grid-column: 1 !important;
  grid-row: 2 !important;
  color: #83a9cc !important;
  font-size: 9px !important;
  font-weight: 600 !important;
  letter-spacing: .11em !important;
}
header .mode-zone {
  grid-column: 2 !important;
  grid-row: 1 / 3 !important;
  min-width: 0;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-end !important;
  gap: 9px !important;
}
header .ok {
  grid-column: 3 !important;
  grid-row: 1 / 3 !important;
  padding: 5px 9px !important;
  color: #b9d9f7 !important;
  background: rgba(58, 127, 193, .16) !important;
  border: 1px solid rgba(113, 169, 221, .3) !important;
  border-radius: 3px !important;
  font-size: 10px !important;
  white-space: nowrap;
}
.mode-controls {
  display: inline-flex !important;
  padding: 2px !important;
  gap: 2px !important;
  background: rgba(0, 7, 15, .34) !important;
  border: 1px solid #2b5377 !important;
  border-radius: 4px !important;
}
.mode-link {
  min-width: 48px;
  padding: 4px 8px !important;
  color: #86a5c1 !important;
  background: transparent !important;
  border: 0 !important;
  border-radius: 2px !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  text-align: center;
  text-decoration: none !important;
}
.mode-link.active { color: #fff !important; background: #2475c7 !important; }
.mode-pill {
  padding: 4px 7px !important;
  color: #f0c4bf !important;
  background: rgba(200, 61, 48, .15) !important;
  border: 1px solid rgba(226, 106, 95, .42) !important;
  border-radius: 3px !important;
  font-size: 9px !important;
  font-weight: 700 !important;
}
.source-line {
  max-width: 500px;
  overflow: hidden;
  color: #94afc8 !important;
  font-size: 10px !important;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wrap {
  display: grid !important;
  grid-template-columns: 252px minmax(0, 1fr) !important;
  align-items: start !important;
  min-height: calc(100vh - 62px);
}
.terminal-nav {
  position: sticky !important;
  top: 62px !important;
  width: 252px !important;
  height: calc(100vh - 62px) !important;
  padding: 12px 10px 28px !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
  color: #d8e5f2 !important;
  background: linear-gradient(180deg, var(--nav), var(--nav-2)) !important;
  border-right: 1px solid #1c4367 !important;
  scrollbar-width: thin;
  scrollbar-color: #31587b transparent;
}
.workspace-title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 49px;
  padding: 9px 11px;
  color: #fff;
  background: #0e3154;
  border: 1px solid #285477;
  border-radius: 5px;
}
.workspace-dot {
  position: relative;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  background: linear-gradient(145deg, #3489d9, #1b60a4);
  border-radius: 4px;
}
.workspace-dot::before,
.workspace-dot::after {
  content: "";
  position: absolute;
  background: rgba(255, 255, 255, .85);
}
.workspace-dot::before { left: 7px; right: 7px; top: 8px; height: 2px; box-shadow: 0 5px 0 rgba(255,255,255,.85), 0 10px 0 rgba(255,255,255,.85); }
.workspace-dot::after { top: 5px; bottom: 5px; left: 12px; width: 2px; opacity: .22; }
.workspace-title b,
.workspace-title small { display: block; }
.workspace-title b { font-size: 14px; font-weight: 720; }
.workspace-title small { margin-top: 1px; color: #78a4cb; font-size: 8px; letter-spacing: .17em; }
.nav-group {
  margin: 17px 9px 6px !important;
  padding-bottom: 5px;
  color: #7297b9 !important;
  border-bottom: 1px solid rgba(115, 155, 192, .14);
  font-size: 10px !important;
  font-weight: 700 !important;
  letter-spacing: .12em;
}
.terminal-nav-item {
  display: grid !important;
  grid-template-columns: 31px minmax(0, 1fr) 10px;
  align-items: center !important;
  gap: 9px !important;
  min-height: 49px;
  margin: 2px 0 !important;
  padding: 6px 8px !important;
  color: #b7cde1 !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 4px !important;
  text-decoration: none !important;
  transition: background .14s ease, border-color .14s ease;
}
.terminal-nav-item:hover {
  color: #fff !important;
  background: #103a61 !important;
  border-color: #24547c !important;
}
.terminal-nav-item.selected {
  color: #fff !important;
  background: linear-gradient(90deg, #185f9e, #104675) !important;
  border-color: #3778ae !important;
  box-shadow: inset 3px 0 0 #84c3f4;
}
.nav-icon {
  display: grid;
  place-items: center;
  width: 29px;
  height: 29px;
  color: #8fb5d7;
  background: rgba(85, 141, 192, .1);
  border: 1px solid rgba(105, 158, 207, .18);
  border-radius: 3px;
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 10px;
  font-weight: 750;
}
.selected .nav-icon { color: #fff; background: #2475c7; border-color: #5799d4; }
.nav-text { min-width: 0; }
.nav-text b,
.nav-text small { display: block; }
.nav-text b { font-size: 13px; font-weight: 680; line-height: 1.35; }
.nav-text small { margin-top: 2px; overflow: hidden; color: #789bb9; font-size: 10px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.selected .nav-text small { color: #bdd9ef; }
.nav-arrow { color: #4c7496; font-size: 15px; }
.selected .nav-arrow { color: #b8daf5; }
.validation-state {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  margin-top: 17px;
  padding: 10px;
  color: #aac0d4;
  background: rgba(255, 255, 255, .035);
  border: 1px solid rgba(120, 159, 194, .14);
  border-radius: 4px;
}
.validation-state i { width: 7px; height: 7px; flex: 0 0 7px; margin-top: 5px; background: #e0a640; border-radius: 50%; box-shadow: 0 0 0 3px rgba(224,166,64,.12); }
.validation-state b,
.validation-state small { display: block; }
.validation-state b { color: #e4c98f; font-size: 10px; }
.validation-state small { margin-top: 3px; font-size: 9px; line-height: 1.5; }

main {
  width: 100% !important;
  min-width: 0 !important;
  max-width: none !important;
  margin: 0 !important;
  padding: 12px 14px 38px !important;
  color: var(--ink) !important;
}
.terminal-page-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  min-height: 73px;
  margin-bottom: 9px;
  padding: 11px 15px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-left: 4px solid var(--blue);
  box-shadow: 0 2px 7px rgba(20, 45, 72, .045);
}
.terminal-page-head .crumb { display: block; margin-bottom: 1px; color: #66819a; font-size: 9px; font-weight: 700; letter-spacing: .1em; }
.terminal-page-head h2 { margin: 0 !important; color: #123757 !important; font-size: 22px !important; font-weight: 760 !important; line-height: 1.25 !important; letter-spacing: -.035em; }
.terminal-page-head p { margin: 3px 0 0 !important; color: #66798c !important; font-size: 11px !important; }
.head-status { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }
.head-status span { padding: 4px 7px; color: #48637c; background: #f0f4f8; border: 1px solid #d3dde7; border-radius: 3px; font-size: 9px; font-weight: 650; white-space: nowrap; }
.head-status .data-mode { color: #9c3027; background: #fff0ee; border-color: #efc9c4; }
.read-order {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 9px;
}
.read-order span { display: flex; align-items: center; min-height: 34px; padding: 6px 9px; color: #53687d; background: #f8fafc; border: 1px solid var(--line); font-size: 10px; }
.read-order b { display: grid; place-items: center; width: 19px; height: 19px; margin-right: 7px; color: #fff; background: var(--blue); border-radius: 2px; font-size: 9px; }

.grid {
  display: grid !important;
  grid-template-columns: repeat(12, minmax(0, 1fr)) !important;
  gap: 8px !important;
}
.grid > .card { grid-column: span 3 !important; }
.grid > .card.w4 { grid-column: 1 / -1 !important; }
.card {
  min-width: 0;
  padding: 12px 13px !important;
  color: var(--text) !important;
  background: var(--paper) !important;
  border: 1px solid var(--line) !important;
  border-radius: 4px !important;
  box-shadow: 0 2px 7px rgba(20, 45, 72, .04) !important;
}
.grid > .card:nth-child(-n+4) { position: relative; min-height: 96px; overflow: hidden; border-top: 2px solid #3a82c8 !important; }
.grid > .card:nth-child(-n+4)::after { content: ""; position: absolute; right: -19px; bottom: -25px; width: 76px; height: 76px; border: 12px solid rgba(36,117,199,.045); border-radius: 50%; }
.panel-title { margin: 0 0 4px !important; color: #546b81 !important; font-size: 10px !important; font-weight: 700 !important; letter-spacing: .03em; }
.kpi { position: relative; z-index: 1; margin: 0 0 1px !important; color: #183650 !important; font-family: Bahnschrift, "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif !important; font-size: clamp(25px, 2.4vw, 34px) !important; font-weight: 600 !important; line-height: 1.2 !important; letter-spacing: -.04em; }
.card p { color: var(--text) !important; font-size: 11px; }
.card ul { margin: 4px 0 0; padding-left: 19px; }
.card li { margin: 2px 0; font-size: 11px; }
.card:has(li) { background: #fffbf3 !important; border-color: #e7d5ae !important; }
.warn { margin: 5px 0 8px !important; padding: 6px 8px !important; color: #785114 !important; background: #fff6e4 !important; border-left: 3px solid #c18a30 !important; border-radius: 1px !important; font-size: 10px !important; font-weight: 650 !important; }
.ok { color: var(--blue) !important; }
.bad, .err { color: var(--up) !important; }

.table-scroll,
.terminal-table-shell { width: 100%; overflow-x: auto !important; border: 1px solid var(--line-2) !important; border-radius: 2px !important; scrollbar-color: #8da5ba #edf1f5; }
table { width: 100% !important; min-width: 1060px !important; border-collapse: separate !important; border-spacing: 0 !important; color: #344a5f !important; background: #fff !important; font-size: 11px !important; }
thead th, table th { position: sticky; top: 0; z-index: 3; padding: 7px 7px !important; color: #deebf6 !important; background: #12395d !important; border-bottom: 1px solid #315b7e !important; font-size: 10px !important; font-weight: 700 !important; line-height: 1.35 !important; text-align: left; white-space: normal; }
tbody td, table td { padding: 7px !important; color: #344a5f !important; background: #fff !important; border-bottom: 1px solid #e2e8ee !important; line-height: 1.4 !important; vertical-align: middle !important; }
tbody tr:nth-child(even) td { background: #f8fafc !important; }
tbody tr:hover td { background: #eaf3fc !important; }
table th:first-child, table td:first-child { position: sticky; left: 0; z-index: 2; min-width: 84px; box-shadow: 5px 0 8px rgba(20,45,72,.035); }
table th:first-child { z-index: 4; background: #12395d !important; }
table a { color: #145fa6 !important; font-weight: 700 !important; text-decoration: none !important; }
table a:hover { color: #0d477d !important; text-decoration: underline !important; }
.market-up { color: var(--up) !important; font-weight: 700 !important; }
.market-down { color: var(--down) !important; font-weight: 700 !important; }
code, pre { color: #253c51 !important; background: #edf2f7 !important; border-color: var(--line) !important; }

@media (max-width: 1250px) {
  .grid > .card { grid-column: span 6 !important; }
  .grid > .card.w4 { grid-column: 1 / -1 !important; }
  .source-line { max-width: 270px; }
}
@media (max-width: 900px) and (min-width: 701px) {
  header { grid-template-columns: 195px minmax(0, 1fr) auto !important; padding-inline: 12px !important; }
  header .source-line { display: none; }
  .wrap { grid-template-columns: 218px minmax(0, 1fr) !important; }
  .terminal-nav { width: 218px !important; }
  .nav-text b { font-size: 12px; }
  .nav-text small { font-size: 9px; }
  main { padding: 9px 9px 34px !important; }
}
@media (max-width: 700px) {
  header { position: relative !important; display: flex !important; flex-wrap: wrap; gap: 4px 10px !important; min-height: 92px !important; padding: 10px 12px !important; }
  header h1 { width: calc(100% - 95px); }
  header .sub { width: calc(100% - 95px); }
  header .ok { position: absolute; right: 11px; top: 12px; }
  header .mode-zone { width: 100%; justify-content: flex-start !important; }
  header .source-line { display: none; }
  .wrap { display: block !important; }
  .terminal-nav { position: sticky !important; top: 0 !important; z-index: 70; display: flex !important; gap: 5px; width: 100% !important; height: auto !important; padding: 7px !important; overflow-x: auto !important; overflow-y: hidden !important; }
  .workspace-title, .nav-group, .validation-state { display: none; }
  .terminal-nav-item { display: flex !important; flex: 0 0 auto; min-height: 37px; padding: 4px 8px !important; }
  .nav-icon, .nav-text small, .nav-arrow { display: none; }
  .nav-text b { font-size: 12px; white-space: nowrap; }
  main { padding: 8px 7px 30px !important; }
  .terminal-page-head { grid-template-columns: 1fr; padding: 10px 12px; }
  .head-status { justify-content: flex-start; }
  .read-order { grid-template-columns: 1fr; gap: 4px; }
  .grid > .card { grid-column: 1 / -1 !important; }
}
"""


SCRIPT = r"""
<script>
(() => {
  document.documentElement.lang = 'zh-CN';
  document.body.classList.add('market-terminal');
  const exact = new Map([
    ['🎯 A股机会雷达 V8.6.6', 'A股机会雷达 V8.6.6'],
    ['Rich Workbench / Server Navigation', 'REAL DATA RESEARCH TERMINAL'],
    ['DEMO', '演示'], ['LIVE', '实时'], ['PIT', '历史'],
    ['● READY', '数据服务正常'],
    ['Meta-Label', '样本表现'], ['Conformal', '置信边界'],
    ['Monte Carlo', '风险压力测试'], ['Alpha 排名', '综合排名'], ['Alpha排名', '综合排名']
  ]);
  const phrases = [
    ['auction_tick: ERROR', '竞价过程数据：暂不可用'],
    ['EOD_CROSS_SECTION', '收盘横截面'], ['INTRADAY_PIT', '盘中真实快照'],
    ['REALTIME_WINNERS', '强势候选'], ['EARLY_STARTUP', '早期启动'],
    ['REVERSAL_REPAIR', '修复转强'], ['Meta-Label', '样本表现'],
    ['Monte Carlo', '风险压力测试'], ['CPCV', '样本外检验'],
    ['Conformal', '置信边界'], ['DSR', '稳健性检验'],
    ['NO_PERMISSION', '未授权'], ['ERROR', '异常'], ['PASS', '通过']
  ];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((node) => {
    if (!node.parentElement || ['SCRIPT', 'STYLE', 'CODE', 'PRE'].includes(node.parentElement.tagName)) return;
    const raw = node.nodeValue;
    const clean = raw.trim();
    if (!clean) return;
    if (exact.has(clean)) { node.nodeValue = raw.replace(clean, exact.get(clean)); return; }
    let value = raw;
    phrases.forEach(([from, to]) => { value = value.split(from).join(to); });
    node.nodeValue = value;
  });

  const pages = {
    overview: ['今日总览', '先看数据状态，再看机会覆盖，最后进入执行页面核对风险。'],
    a_exec: ['A级执行', '查看通过执行门槛的信号及资金、竞价、筹码、支撑压力和风险证据。'],
    sniper: ['A+狙击', '所有更高等级门槛必须逐项通过；未通过时保持观察。'],
    meta: ['样本表现', '只使用真实结果评估信号；样本不足时不生成真实胜率。'],
    conformal: ['置信边界', '查看预测区间和不确定性，不把单一概率当成确定结论。'],
    cpcv: ['样本外检验', '检验不同历史阶段的稳定性，防止未来泄漏和回测过拟合。'],
    factor: ['因子分析', '拆解资金、竞价、筹码和趋势证据的实际贡献。'],
    limitup: ['连板观察', '观察涨停生态和板块扩散，不等同于追涨建议。'],
    sim: ['执行模拟', '估算滑点、成交概率和执行成本，当前不连接自动交易。'],
    mc: ['风险压力', '观察不利情景中的回撤与承受能力。'],
    drift: ['稳定性监控', '检查数据与历史表现是否发生漂移。'],
    health: ['数据健康', '检查完整度、时效、异常字段与PIT安全状态。'],
    sources: ['数据源与权限', '查看接口配置、权限、延迟、数据量和最近错误。']
  };
  const query = new URLSearchParams(location.search);
  const page = query.get('page') || 'overview';
  const mode = (query.get('mode') || 'DEMO').toUpperCase();
  const modeLabel = { DEMO: '演示数据', LIVE: '实时数据', PIT: '历史实况' }[mode] || '演示数据';
  const [title, description] = pages[page] || pages.overview;
  document.title = `${title}｜A股机会雷达`;

  const main = document.querySelector('main');
  if (main) {
    const head = document.createElement('section');
    head.className = 'terminal-page-head';
    head.innerHTML = `<div><span class="crumb">机会雷达 / 功能工作台</span><h2>${title}</h2><p>${description}</p></div><div class="head-status"><span class="data-mode">${modeLabel}</span><span>仅验证展示</span><span>不自动交易</span></div>`;
    main.prepend(head);
    if (page === 'overview') {
      const order = document.createElement('div');
      order.className = 'read-order';
      order.innerHTML = '<span><b>1</b>检查数据覆盖与异常</span><span><b>2</b>查看候选及入选原因</span><span><b>3</b>进入A级执行核对风险</span>';
      head.insertAdjacentElement('afterend', order);
    }
  }

  document.querySelectorAll('table').forEach((table) => {
    if (!table.parentElement.classList.contains('table-scroll')) {
      const shell = document.createElement('div');
      shell.className = 'terminal-table-shell';
      table.parentNode.insertBefore(shell, table);
      shell.appendChild(table);
    }
    const headers = Array.from(table.querySelectorAll('thead th, tr:first-child th'));
    const changeIndex = headers.findIndex((th) => th.textContent.includes('涨跌'));
    if (changeIndex >= 0) {
      table.querySelectorAll('tbody tr').forEach((row) => {
        const cell = row.children[changeIndex];
        if (!cell) return;
        const value = Number.parseFloat(cell.textContent.replace('%', ''));
        if (Number.isFinite(value)) cell.classList.add(value > 0 ? 'market-up' : value < 0 ? 'market-down' : 'market-flat');
      });
    }
  });
})();
</script>
"""


def render_page(*args, **kwargs):
    html = _render_original(*args, **kwargs)
    if "</style>" in html:
        html = html.replace("</style>", CSS + "</style>", 1)
    else:
        html = html.replace("</head>", "<style>" + CSS + "</style></head>", 1)
    html = html.replace("</body>", SCRIPT + "</body>", 1)
    return html


core._render_nav = render_nav
core.render_page = render_page


def main():
    host = os.environ.get("RADAR_HOST", "127.0.0.1")
    port = int(os.environ.get("RADAR_PORT", "8791"))
    server = ThreadingHTTPServer((host, port), core.H)
    print("A股机会雷达 V8.6.6 行情工作台")
    print("http://%s:%s/?page=overview&mode=LIVE" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
