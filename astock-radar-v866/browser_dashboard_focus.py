"""Task-first Chinese interface for AStock Radar V8.6.6.

The original dashboard remains untouched and continues to provide all data,
routes and calculations. This module changes presentation only.
"""

from html import escape
from http.server import ThreadingHTTPServer
import os
import re

import browser_dashboard as core
from operating_workbench import render_review_page, render_ticket_page
from portfolio_monitor import PortfolioMonitor
from strategy_governance import render_upgrade_page


_render_original = core.render_page


NAV_GROUPS = (
    ("今日作战", "今", "每天按这个顺序看", (
        ("overview", "市场总览"),
        ("a_exec", "执行候选"),
        ("portfolio", "我的持仓"),
        ("review", "收盘复盘"),
    )),
    ("机会发现", "发", "发现可以宽，执行必须严", (
        ("tickets", "候选清单"),
        ("limitup", "涨停与题材"),
        ("sniper", "A+准入检查"),
        ("sim", "今日发现时间线"),
    )),
    ("验证研究", "验", "只看真实样本，不包装胜率", (
        ("meta", "真实样本"),
        ("cpcv", "样本外检验"),
        ("conformal", "不确定性边界"),
        ("factor", "发现因子"),
        ("mc", "风险压力"),
        ("drift", "稳定性"),
    )),
    ("数据与账户", "数", "数据、权限与运行状态", (
        ("sources", "数据源状态"),
        ("health", "数据健康"),
        ("upgrade", "系统状态"),
    )),
)

PAGE_GUIDANCE = {
    "overview": ("先看市场是否有机会", "确认盘中快照已更新，再看最靠前的观察候选；没有A级信号时只观察。"),
    "a_exec": ("只处理已通过门槛的信号", "只有明确标为“A级可执行”的信号才进入人工复核；观察候选不是买入指令。"),
    "portfolio": ("管理已经持有的股票", "录入代码、成本、数量和日期后，只看真实行情、持仓风险与复盘提示。"),
    "review": ("收盘后核对当天过程", "核对候选、信号和数据缺口，为下一交易日留下可追溯记录。"),
    "tickets": ("从候选清单开始研究", "逐只核对板块、资金、竞价、风险和数据质量；本页不代表可以买。"),
    "limitup": ("观察题材强度与扩散", "用于判断市场情绪和题材持续性，不把涨停名单直接当买入名单。"),
    "sniper": ("检查A+是否有资格出现", "任何一项真实样本、风险或人工门槛不通过，就保持关闭。"),
    "sim": ("核对今天何时发现强势股", "查看首次PIT发现、当时涨幅和后续走势；它用于验证发现过程，不等同买入指令。"),
    "meta": ("确认真实交易信号样本是否足够", "这里只统计系统当时真实产生的信号；样本不足时不输出胜率。"),
    "cpcv": ("验证样本外是否仍成立", "只有PIT安全、时间切分正确的真实结果才可以进入此处。"),
    "conformal": ("查看预测的不确定性", "置信范围不是收益承诺；样本不足时应保持空白。"),
    "factor": ("研究发现层是否有增量", "全市场PIT可用于发现研究，但不能冒充A/A+交易胜率样本。"),
    "mc": ("查看极端情形下的风险", "没有经过真实样本校验的模拟，只能用于压力提示。"),
    "drift": ("监控数据和规律是否变化", "发生漂移时先停用结论、排查数据，不自动改参数。"),
    "sources": ("确认真实数据是否可用", "先检查Token、接口权限、最新调用时间和错误；失败不能静默回退为演示数据。"),
    "health": ("确认工作台是否正常运行", "重点看最新快照、覆盖股票数、字段质量与异常提示。"),
    "upgrade": ("查看系统边界与更新状态", "本页记录旧版经验、新版验证范围和未完成事项，不作为交易依据。"),
}

PAGE_TITLES = {
    "overview": "市场总览", "a_exec": "执行候选", "portfolio": "我的持仓", "review": "收盘复盘",
    "tickets": "候选清单", "limitup": "涨停与题材", "sniper": "A+准入检查", "sim": "今日发现时间线",
    "meta": "真实样本", "cpcv": "样本外检验", "conformal": "不确定性边界", "factor": "发现因子",
    "mc": "风险压力", "drift": "稳定性", "sources": "数据源状态", "health": "数据健康", "upgrade": "系统状态",
}


def _decision_brief(page, mode, status):
    title, action = PAGE_GUIDANCE.get(page, ("工作台说明", "先确认数据来源和更新时间，再做人工判断。"))
    intraday = status.get("intraday") or {}
    snapshot = intraday.get("snapshot") or {}
    observed = snapshot.get("observed_at") or "尚未采集"
    coverage = intraday.get("coverage") or 0
    if isinstance(coverage, dict):
        coverage = coverage.get("stocks") or 0
    if mode == "DEMO":
        source, state = "演示模式：所有内容仅用于界面演示，不能作为交易依据。", "演示数据"
    elif status.get("intraday_ready"):
        source, state = "盘中PIT快照：%s；覆盖 %s 只股票。" % (observed, coverage), "真实数据"
    else:
        source, state = "真实数据暂未就绪：请先到“数据源状态”和“数据健康”排查。", "数据待核对"
    return ('<section class="decision-brief"><div><span class="decision-eyebrow">本页用途</span><strong>%s</strong><p>%s</p></div><div class="decision-data"><span class="decision-state">%s</span><p>%s</p></div></section>' % (escape(title), escape(action), escape(state), escape(source)))


def _operator_summary(mode, status):
    """One daily order of work for an executor, not another score board."""
    intraday = status.get("intraday") or {}
    snapshot = intraday.get("snapshot") or {}
    observed = str(snapshot.get("observed_at") or "尚未采集").replace("T", " ")
    coverage = intraday.get("coverage") or 0
    if isinstance(coverage, dict):
        coverage = coverage.get("stocks") or 0
    portfolio = PortfolioMonitor().snapshot()
    positions = portfolio.get("positions") or []
    critical = sum(1 for item in positions if item.get("defense_state") == "TRUE_WEAKNESS" or item.get("defense_action") in {"DYNAMIC_DEFENSE", "DEFENSE_WARNING", "RISK_REVIEW"})
    signal = (status.get("sample_domains") or {}).get("trade_signal") or {}
    signals = int(signal.get("meta_eligible") or signal.get("eligible_signals") or 0)
    fresh = bool(status.get("intraday_ready")) and mode != "DEMO"
    headline = "先进入执行候选：只有明确显示 A级可执行 的股票才进入人工复核；没有A级就不操作。" if fresh else "当前实时数据未确认就绪：暂停看候选，先检查数据健康。"
    data_state = "盘中数据已就绪" if fresh else "数据待核对"
    return (
        "<section class='operator-summary'><div class='operator-heading'><span>今日执行顺序</span><strong>%s</strong></div>"
        "<div class='operator-steps'>"
        "<a href='/?page=health&amp;mode=%s'><b>1. 数据</b><strong>%s</strong><small>最新快照：%s｜覆盖 %s 只</small></a>"
        "<a href='/?page=a_exec&amp;mode=%s'><b>2. 执行</b><strong>只看A级可执行</strong><small>观察候选、涨停候选都不等于可以买</small></a>"
        "<a href='/?page=portfolio&amp;mode=%s'><b>3. 持仓</b><strong>%s 只需复核</strong><small>共 %s 只｜动态防守与状态已接入</small></a>"
        "<a href='/?page=meta&amp;mode=%s'><b>4. 验证</b><strong>%s 条真实样本</strong><small>只统计真实交易信号，不用全市场样本冒充</small></a>"
        "</div></section>"
    ) % (escape(headline), escape(mode), escape(data_state), escape(observed), escape(str(coverage or "--")), escape(mode), escape(mode), escape(str(critical)), escape(str(len(positions))), escape(mode), escape(str(signals)))


def render_nav(page_key, mode="DEMO"):
    safe_mode = mode if mode in {"DEMO", "LIVE", "PIT"} else "DEMO"
    out = [
        "<nav class='focus-nav' aria-label='工作台导航'>",
        "<div class='focus-nav-brand'><b>交易工作台</b><span>发现 · 验证 · 风险</span></div>",
    ]
    for title, mark, helper, items in NAV_GROUPS:
        active = any(key == page_key for key, _ in items)
        open_attr = " open" if active else ""
        current_class = " current-group" if active else ""
        out.append(
            "<details class='focus-nav-section%s'%s><summary>"
            "<span class='focus-group-mark'>%s</span>"
            "<span class='focus-group-copy'><b>%s</b><small>%s</small></span>"
            "<span class='focus-chevron'></span></summary><div class='focus-links'>"
            % (current_class, open_attr, escape(mark), escape(title), escape(helper))
        )
        for key, label in items:
            selected = " selected" if key == page_key else ""
            out.append(
                "<a class='focus-link%s' href='/?page=%s&amp;mode=%s'>%s</a>"
                % (selected, escape(key), escape(safe_mode), escape(label))
            )
        out.append("</div></details>")
    out.extend((
        "<div class='focus-safety'><span></span><div><b>当前仅用于验证</b>"
        "<small>不自动交易，不自动调参</small></div></div>",
        "</nav>",
    ))
    return "".join(out)


CSS = r"""
:root {
  --navy: #0b2946;
  --navy-2: #103a62;
  --blue: #2479c9;
  --blue-dark: #155b9b;
  --blue-soft: #eaf4ff;
  --bg: #f3f6fa;
  --surface: #ffffff;
  --ink: #192d43;
  --text: #465b70;
  --muted: #7d8d9e;
  --line: #dbe4ed;
  --line-2: #cbd8e4;
  --red: #d83c33;
  --green: #16865f;
  --amber: #ad741b;
}
* { box-sizing: border-box; }
html { background: var(--bg); }
body {
  margin: 0 !important;
  color: var(--ink) !important;
  background: var(--bg) !important;
  font-family: "Microsoft YaHei UI", "Source Han Sans SC", "PingFang SC", sans-serif !important;
  font-size: 14px !important;
  line-height: 1.6 !important;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}

header {
  position: sticky !important;
  top: 0 !important;
  z-index: 80 !important;
  min-height: 64px !important;
  padding: 10px 18px !important;
  display: grid !important;
  grid-template-columns: 218px minmax(0, 1fr) auto !important;
  grid-template-rows: 26px 16px !important;
  align-items: center !important;
  column-gap: 18px !important;
  color: #fff !important;
  background: linear-gradient(90deg, #09243e, #0d365b) !important;
  border: 0 !important;
  border-bottom: 1px solid #2e5a80 !important;
  box-shadow: 0 4px 15px rgba(7, 30, 51, .16) !important;
}
header h1 {
  grid-column: 1 !important;
  grid-row: 1 !important;
  margin: 0 !important;
  color: #fff !important;
  font-size: 19px !important;
  font-weight: 780 !important;
  line-height: 1.2 !important;
  letter-spacing: -.035em !important;
}
header .sub {
  grid-column: 1 !important;
  grid-row: 2 !important;
  color: #91b4d2 !important;
  font-size: 9px !important;
  font-weight: 600 !important;
  letter-spacing: .07em !important;
}
header .mode-zone {
  grid-column: 2 !important;
  grid-row: 1 / 3 !important;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-end !important;
  gap: 10px !important;
  min-width: 0;
}
header .ok {
  grid-column: 3 !important;
  grid-row: 1 / 3 !important;
  padding: 5px 9px !important;
  color: #d7eafa !important;
  background: rgba(255, 255, 255, .08) !important;
  border: 1px solid rgba(181, 214, 243, .25) !important;
  border-radius: 5px !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  white-space: nowrap;
}
.mode-controls {
  display: inline-flex !important;
  gap: 2px !important;
  padding: 3px !important;
  background: rgba(0, 10, 21, .3) !important;
  border: 1px solid rgba(154, 197, 235, .25) !important;
  border-radius: 7px !important;
}
.mode-link {
  min-width: 50px;
  padding: 5px 9px !important;
  color: #a9c3da !important;
  background: transparent !important;
  border: 0 !important;
  border-radius: 5px !important;
  font-size: 10px !important;
  font-weight: 720 !important;
  text-align: center;
  text-decoration: none !important;
}
.mode-link.active { color: #fff !important; background: #2e82d0 !important; }
.mode-pill, .source-line { display: none !important; }
.decision-brief { display:flex; justify-content:space-between; gap:24px; align-items:center; margin:0 0 14px; padding:14px 18px; background:linear-gradient(100deg,#f7fbff,#edf5ff); border:1px solid #cbdff4; border-left:4px solid #1674d1; border-radius:10px; box-shadow:0 4px 14px rgba(34,74,119,.05); }
.decision-brief strong { display:block; color:#123c67; font-size:16px; margin-top:2px; }.decision-brief p { margin:5px 0 0; color:#58708b; font-size:13px; line-height:1.55; }.decision-eyebrow { color:#247bd1; font-size:11px; font-weight:900; letter-spacing:.08em; }.decision-data { text-align:right; max-width:48%; }.decision-state { display:inline-block; background:#e5f1ff; color:#1066b3; border-radius:999px; padding:4px 9px; font-size:12px; font-weight:900; }
.card.card-clickable { position:relative; cursor:pointer; transition:transform .16s ease, box-shadow .16s ease, border-color .16s ease; }.card.card-clickable:hover { transform:translateY(-2px); border-color:#2e82d0; box-shadow:0 9px 20px rgba(18,73,126,.13); }.card-action { display:inline-flex; align-items:center; margin-top:10px; color:#166fc0; font-size:12px; font-weight:800; }.card-action::after { content:' →'; margin-left:4px; }

.wrap {
  display: grid !important;
  grid-template-columns: 226px minmax(0, 1fr) !important;
  align-items: start !important;
  min-height: calc(100vh - 64px);
}
.focus-nav {
  position: sticky !important;
  top: 64px !important;
  width: 226px !important;
  height: calc(100vh - 64px) !important;
  padding: 14px 12px 28px !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
  color: var(--ink) !important;
  background: #fff !important;
  border-right: 1px solid var(--line-2) !important;
  scrollbar-width: thin;
  scrollbar-color: #a1b2c2 transparent;
}
.focus-nav-brand {
  margin-bottom: 18px;
  padding: 15px 14px;
  color: #fff;
  background: linear-gradient(135deg, var(--navy), #1763a6);
  border-radius: 9px;
  box-shadow: 0 9px 22px rgba(16, 64, 105, .16);
}
.focus-nav-brand b,
.focus-nav-brand span { display: block; }
.focus-nav-brand b { font-size: 16px; font-weight: 760; }
.focus-nav-brand span { margin-top: 4px; color: #b9d6ee; font-size: 10px; letter-spacing: .04em; }
.focus-nav-section { margin: 0 0 8px; border-bottom: 1px solid #e8edf2; }
.focus-nav-section summary {
  display: grid;
  grid-template-columns: 33px minmax(0, 1fr) 12px;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  padding: 7px 8px;
  color: #354c62;
  cursor: pointer;
  list-style: none;
  border-radius: 7px;
  user-select: none;
}
.focus-nav-section summary::-webkit-details-marker { display: none; }
.focus-nav-section summary:hover { background: #f4f7fa; }
.focus-nav-section.current-group summary { color: #145990; }
.focus-group-mark {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  color: #236da9;
  background: #edf5fc;
  border: 1px solid #d5e5f2;
  border-radius: 7px;
  font-size: 11px;
  font-weight: 760;
}
.current-group .focus-group-mark { color: #fff; background: var(--blue); border-color: var(--blue); }
.focus-group-copy b,
.focus-group-copy small { display: block; }
.focus-group-copy b { font-size: 14px; font-weight: 720; line-height: 1.3; }
.focus-group-copy small { margin-top: 3px; color: #8b99a7; font-size: 10px; line-height: 1.3; }
.focus-chevron { width: 8px; height: 8px; border-right: 1.5px solid #8796a5; border-bottom: 1.5px solid #8796a5; transform: rotate(45deg) translateY(-2px); transition: transform .15s ease; }
.focus-nav-section[open] .focus-chevron { transform: rotate(225deg) translate(-2px, -2px); }
.focus-links { padding: 2px 0 9px 43px; }
.focus-link {
  display: block !important;
  margin: 2px 0 !important;
  padding: 8px 10px !important;
  color: #52677b !important;
  background: transparent !important;
  border: 0 !important;
  border-left: 2px solid #dfe7ee !important;
  border-radius: 0 5px 5px 0 !important;
  font-size: 13px !important;
  font-weight: 560 !important;
  text-decoration: none !important;
}
.focus-link:hover { color: #15598f !important; background: #f2f7fb !important; }
.focus-link.selected { color: #104f83 !important; background: var(--blue-soft) !important; border-left-color: var(--blue) !important; font-weight: 720 !important; }
.focus-safety {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  margin-top: 18px;
  padding: 11px;
  color: #687b8e;
  background: #f8fafc;
  border: 1px solid #e2e9ef;
  border-radius: 7px;
}
.focus-safety > span { width: 8px; height: 8px; flex: 0 0 8px; margin-top: 5px; background: #e0a43d; border-radius: 50%; box-shadow: 0 0 0 3px #fbf1dc; }
.focus-safety b,
.focus-safety small { display: block; }
.focus-safety b { font-size: 11px; }
.focus-safety small { margin-top: 2px; color: #939faa; font-size: 9px; }

main {
  width: 100% !important;
  min-width: 0 !important;
  max-width: none !important;
  margin: 0 !important;
  padding: 20px 24px 46px !important;
  color: var(--ink) !important;
}
.focus-page-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  min-height: 80px;
  margin-bottom: 14px;
  padding: 2px 0 14px;
  border-bottom: 1px solid var(--line-2);
}
.focus-eyebrow { display: block; margin-bottom: 3px; color: var(--blue-dark); font-size: 10px; font-weight: 760; letter-spacing: .08em; }
.focus-page-head h2 { margin: 0 !important; color: #173650 !important; font-size: 27px !important; font-weight: 780 !important; line-height: 1.25 !important; letter-spacing: -.045em; }
.focus-page-head p { margin: 4px 0 0 !important; max-width: 680px; color: #64788c !important; font-size: 12px !important; }
.focus-page-actions { display: flex; align-items: center; gap: 7px; }
.focus-mode { padding: 5px 8px; color: #a2352b; background: #fff0ee; border: 1px solid #efc9c5; border-radius: 5px; font-size: 10px; font-weight: 700; white-space: nowrap; }
.focus-primary, .focus-back { padding: 7px 11px; border-radius: 5px; font-size: 10px; font-weight: 720; text-decoration: none !important; white-space: nowrap; }
.focus-primary { color: #fff !important; background: linear-gradient(135deg, #1766aa, #2a7fca); box-shadow: 0 4px 11px rgba(24, 102, 169, .15); }
.focus-back { color: #476176 !important; background: #fff; border: 1px solid var(--line); }

.grid {
  display: grid !important;
  grid-template-columns: repeat(12, minmax(0, 1fr)) !important;
  gap: 12px !important;
}
.grid > .card { grid-column: span 6 !important; }
.grid > .card.w1 { grid-column: span 3 !important; }
.grid > .card.w2 { grid-column: span 6 !important; }
.grid > .card.w3 { grid-column: span 9 !important; }
.grid > .card.w4 { grid-column: 1 / -1 !important; }
.grid > .card.focus-metric { grid-column: span 3 !important; }
.card {
  min-width: 0;
  padding: 16px 17px !important;
  color: var(--text) !important;
  background: #fff !important;
  border: 1px solid var(--line) !important;
  border-radius: 8px !important;
  box-shadow: 0 4px 15px rgba(25, 51, 78, .04) !important;
}
.card.focus-metric { min-height: 104px; border-top: 3px solid #4b8fcb !important; }
.panel-title { margin: 0 0 7px !important; color: #607488 !important; font-size: 11px !important; font-weight: 720 !important; letter-spacing: .025em; }
.kpi { margin: 0 0 2px !important; color: #193a55 !important; font-family: Bahnschrift, "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif !important; font-size: clamp(29px, 3vw, 39px) !important; font-weight: 610 !important; line-height: 1.16 !important; letter-spacing: -.04em; }
.card p { color: var(--text) !important; font-size: 12px; }
.card ul { margin: 5px 0 0; padding-left: 19px; }
.card li { margin: 3px 0; font-size: 12px; }
.card.focus-alert, .card:has(.panel-title:first-child):has(li) { background: #fffaf0 !important; border-color: #ead8b3 !important; }
.warn { margin: 6px 0 10px !important; padding: 8px 10px !important; color: #785015 !important; background: #fff5df !important; border-left: 3px solid #bc8428 !important; border-radius: 3px !important; font-size: 11px !important; font-weight: 650 !important; }
.ok { color: var(--blue-dark) !important; }
.bad, .err { color: var(--red) !important; }

.focus-technical {
  grid-column: 1 / -1;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 8px;
}
.focus-technical summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 49px;
  padding: 11px 15px;
  color: #4b6277;
  background: #f9fbfd;
  cursor: pointer;
  list-style: none;
}
.focus-technical summary::-webkit-details-marker { display: none; }
.focus-technical summary::after { content: "查看"; padding: 3px 7px; color: #1764a7; background: #e9f3fc; border-radius: 4px; font-size: 9px; }
.focus-technical[open] summary::after { content: "收起"; }
.focus-technical summary b,
.focus-technical summary small { display: block; }
.focus-technical summary b { font-size: 12px; }
.focus-technical summary small { margin-top: 2px; color: #8b99a7; font-size: 10px; }
.focus-technical-body { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 12px; border-top: 1px solid #e3e9ef; }
.focus-technical-body > .card { min-height: auto !important; box-shadow: none !important; }
.focus-technical-body > .card:last-child:nth-child(odd) { grid-column: 1 / -1; }

.table-scroll, .focus-table-shell { width: 100%; overflow-x: auto !important; border: 1px solid var(--line-2) !important; border-radius: 5px !important; scrollbar-color: #93a8bb #eef2f6; }
table { width: 100% !important; border-collapse: separate !important; border-spacing: 0 !important; color: #354b60 !important; background: #fff !important; font-size: 12px !important; }
table.focus-compact { min-width: 100% !important; table-layout: auto !important; }
table.focus-wide { min-width: 1080px !important; }
thead th, table th { position: sticky; top: 0; z-index: 3; padding: 9px 8px !important; color: #e3eef8 !important; background: #173f64 !important; border-bottom: 1px solid #315f85 !important; font-size: 10.5px !important; font-weight: 720 !important; line-height: 1.35 !important; text-align: left; }
tbody td, table td { padding: 9px 8px !important; color: #354b60 !important; background: #fff !important; border-bottom: 1px solid #e3e9ef !important; line-height: 1.45 !important; vertical-align: middle !important; }
tbody tr:nth-child(even) td { background: #f8fafc !important; }
tbody tr:hover td { background: #ebf4fd !important; }
table th:first-child, table td:first-child { position: sticky; left: 0; z-index: 2; min-width: 88px; box-shadow: 5px 0 9px rgba(26,50,75,.035); }
table th:first-child { z-index: 4; background: #173f64 !important; }
table a { color: #1764ae !important; font-weight: 720 !important; text-decoration: none !important; }
.market-up { color: var(--red) !important; font-weight: 720 !important; }
.market-down { color: var(--green) !important; font-weight: 720 !important; }
.status-insufficient { color: #8b5a10 !important; font-weight: 720 !important; }
.status-granted { color: #147052 !important; font-weight: 720 !important; }
code, pre { color: #2d455b !important; background: #edf2f7 !important; border-color: var(--line) !important; }

.grid > .card.w4.focus-signal-card {
  grid-column: span 6 !important;
  padding: 0 !important;
  overflow: hidden;
  border-top: 3px solid #3985c8 !important;
}
.focus-signal-card > .panel-title {
  margin: 0 !important;
  padding: 14px 16px 12px;
  color: #173a59 !important;
  background: #fbfdff;
  border-bottom: 1px solid #dfe7ef;
  font-size: 14px !important;
  font-weight: 740 !important;
  line-height: 1.5;
}
.focus-signal-card > .panel-title code {
  color: #1763a7 !important;
  background: #eaf3fc !important;
  border: 0 !important;
  border-radius: 4px;
  padding: 2px 5px;
  font-family: "Microsoft YaHei UI", sans-serif;
  font-size: 11px;
}
.focus-signal-card > .grid {
  display: grid !important;
  grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
  gap: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  background: #f3f7fb;
  border-bottom: 1px solid #dfe7ef;
}
.focus-signal-card > .grid > div {
  min-width: 0;
  padding: 12px 13px 10px;
  border-right: 1px solid #dce6ef;
}
.focus-signal-card > .grid > div:last-child { border-right: 0; }
.focus-signal-card > .grid .kpi {
  margin: 0 0 2px !important;
  color: #173c5d !important;
  font-size: 24px !important;
  line-height: 1.15 !important;
}
.focus-signal-card > .grid p:not(.kpi) {
  margin: 0 !important;
  color: #718396 !important;
  font-size: 10px !important;
  white-space: nowrap;
}
.focus-signal-card > p:not(.panel-title):not(.muted) {
  margin: 0 !important;
  padding: 7px 16px;
  color: #42596e !important;
  border-bottom: 1px solid #edf1f5;
  font-size: 12px !important;
  line-height: 1.55;
}
.focus-signal-card > p .warn {
  display: inline-block;
  margin: 0 0 0 5px !important;
  padding: 3px 7px !important;
  border-left: 0 !important;
  border-radius: 4px !important;
}
.portfolio-card { background: #fff !important; }
.portfolio-note { margin: 10px 0 0 !important; color: #65798c !important; font-size: 11px !important; }
.portfolio-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.portfolio-form.compact { grid-template-columns: 1fr; }
.portfolio-form label { display: grid; gap: 4px; color: #536a7e; font-size: 11px; font-weight: 680; }
.portfolio-form input, .portfolio-form textarea { width: 100%; min-width: 0; padding: 8px 9px; color: #243d55; background: #fbfdff; border: 1px solid #cfdae4; border-radius: 5px; font: inherit; font-size: 12px; outline: 0; }
.portfolio-form input:focus, .portfolio-form textarea:focus { border-color: #287dc7; box-shadow: 0 0 0 3px rgba(40,125,199,.12); }
.portfolio-form textarea { min-height: 68px; resize: vertical; }
.portfolio-wide { grid-column: 1 / -1; }
.portfolio-actions { display: flex; align-items: center; gap: 10px; }
.portfolio-button { padding: 8px 12px; color: #fff; background: #1767ab; border: 0; border-radius: 5px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 720; }
.portfolio-button:hover { background: #0f5793; }
.portfolio-link { color: #1764ae !important; font-size: 11px; font-weight: 720; text-decoration: none !important; }
.portfolio-inline { display: inline; margin-left: 7px; }
.portfolio-delete { padding: 0; color: #b33b35; background: transparent; border: 0; cursor: pointer; font: inherit; font-size: 11px; }
.portfolio-feedback { grid-column: 1 / -1; padding: 10px 12px; border-radius: 6px; font-size: 12px; font-weight: 650; }
.portfolio-feedback.success { color: #176448; background: #eaf7f0; border: 1px solid #bfdfcf; }
.portfolio-feedback.error { color: #9a352e; background: #fff0ee; border: 1px solid #edc9c4; }
.portfolio-risk-list { margin: 0 !important; color: #674d1f; }
.signal-evidence {
  margin: 9px 12px 12px;
  overflow: hidden;
  border: 1px solid #dfe7ee;
  border-radius: 6px;
}
.signal-evidence summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 38px;
  padding: 8px 11px;
  color: #587086;
  background: #f8fafc;
  cursor: pointer;
  list-style: none;
  font-size: 11px;
  font-weight: 680;
}
.signal-evidence summary::-webkit-details-marker { display: none; }
.signal-evidence summary::after { content: "展开"; color: #1766aa; font-size: 9px; }
.signal-evidence[open] summary::after { content: "收起"; }
.signal-evidence-body { padding: 9px 11px; border-top: 1px solid #e4ebf1; }
.signal-evidence-body .muted {
  margin: 0 0 7px !important;
  color: #6d7f90 !important;
  font-size: 10.5px !important;
  line-height: 1.65 !important;
}
.signal-evidence-body .muted:last-child { margin-bottom: 0 !important; }

@media (max-width: 1300px) {
  .grid > .card.w4.focus-signal-card { grid-column: 1 / -1 !important; }
}
@media (max-width: 560px) {
  .focus-signal-card > .grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
  .focus-signal-card > .grid > div:nth-child(2) { border-right: 0; }
  .focus-signal-card > .grid > div:nth-child(-n+2) { border-bottom: 1px solid #dce6ef; }
}

@media (max-width: 900px) and (min-width: 701px) {
  header { grid-template-columns: 190px minmax(0,1fr) auto !important; }
  .wrap { grid-template-columns: 198px minmax(0, 1fr) !important; }
  .focus-nav { width: 198px !important; padding-inline: 9px !important; }
  .focus-nav-brand { padding: 13px 11px; }
  .focus-links { padding-left: 36px; }
  main { padding: 16px 14px 40px !important; }
  .grid > .card.w1, .grid > .card.focus-metric { grid-column: span 6 !important; }
}
@media (max-width: 700px) {
  header { position: relative !important; display: flex !important; flex-wrap: wrap; gap: 5px 10px !important; min-height: 94px !important; padding: 10px 12px !important; }
  header h1, header .sub { width: calc(100% - 105px); }
  header .ok { position: absolute; right: 11px; top: 12px; }
  header .mode-zone { width: 100%; justify-content: flex-start !important; }
  .wrap { display: block !important; }
  .focus-nav { position: sticky !important; top: 0 !important; z-index: 70; display: flex !important; gap: 5px; width: 100% !important; height: auto !important; padding: 7px !important; overflow-x: auto !important; overflow-y: hidden !important; background: var(--navy) !important; }
  .focus-nav-brand, .focus-nav-section summary, .focus-safety { display: none !important; }
  .focus-nav-section { display: contents; }
  .focus-links { display: flex !important; gap: 5px; padding: 0 !important; }
  .focus-link { flex: 0 0 auto; padding: 7px 10px !important; color: #bed5e8 !important; border: 0 !important; border-radius: 5px !important; }
  .focus-link.selected { color: #fff !important; background: var(--blue) !important; }
  main { padding: 13px 10px 34px !important; }
  .focus-page-head { grid-template-columns: 1fr; gap: 9px; }
  .focus-page-actions { justify-content: flex-start; }
  .grid > .card, .grid > .card.w1, .grid > .card.w2, .grid > .card.w3, .grid > .card.w4, .grid > .card.focus-metric { grid-column: 1 / -1 !important; }
  .focus-technical-body { grid-template-columns: 1fr; }
  .portfolio-form { grid-template-columns: 1fr; }
  .focus-technical-body > .card { grid-column: 1 !important; }
}
"""


SCRIPT = r"""
<script>
(() => {
  document.documentElement.lang = 'zh-CN';
  const params = new URLSearchParams(location.search);
  const page = params.get('page') || 'overview';
  const mode = (params.get('mode') || 'DEMO').toUpperCase();
  const exact = new Map([
    ['🎯 A股机会雷达 V8.6.6', 'A股机会雷达 V8.6.6'], ['Rich Workbench / Server Navigation', '真实数据研究工作台'],
    ['DEMO', '演示'], ['LIVE', '实时'], ['PIT', '历史'], ['● READY', '数据服务正常'],
    ['SAMPLE_INSUFFICIENT', '样本不足'], ['GRANTED', '已授权'], ['EMPTY', '暂无数据'], ['READY', '就绪'], ['OK', '正常'], ['NULL', '暂无'], ['PASS', '通过'], ['C', '观察级'],
    ['V8_LEGACY', 'V8历史数据'], ['V8_INDEPENDENT_DISCOVERY', 'V8独立发现'], ['V8_ACCEPTANCE', 'V8验收数据'],
    ['Meta Label', '二次筛选标签'], ['Meta-Label', '样本表现'], ['Alpha Rank', '综合强度排名'], ['Bootstrap', '重复抽样检验'], ['CPCV', '组合式样本外检验'], ['Conformal', '置信边界'], ['Purged CV', '去重交叉验证'], ['Factor Lab', '因子实验室'], ['Monte Carlo', '风险压力测试'], ['D-SR', '修正夏普检验'], ['rank_ic', '排名相关性'],
    ['Tushare Token', '付费数据访问凭证'], ['REFRESH_LIVE_ANALYSIS.bat', '刷新实时分析启动文件'],
    ['trade_cal', '交易日历'], ['stock_basic', '股票基础资料'], ['daily', '日线行情'], ['daily_basic', '每日基础指标'], ['pro_bar', '复权行情'], ['stk_limit', '每日涨跌停价格'], ['limit_list_ths', '涨跌停榜单'], ['stk_auction_tick', '竞价逐笔数据'], ['stk_auction_o', '开盘竞价数据'], ['stk_auction_c', '收盘竞价数据'], ['moneyflow', '个股资金流'], ['moneyflow_ths', '个股资金流增强版'], ['moneyflow_ind_ths', '行业资金流'], ['share_float', '限售股解禁'], ['top_list', '龙虎榜'], ['block_trade', '大宗交易'], ['margin_detail', '融资融券明细'], ['factor_list', '因子目录'], ['factor_value', '因子数据'], ['rt_min', '实时分钟行情'], ['rt_min_daily', '当日分钟行情'], ['https://tt.dailyfetch.top/', '付费数据镜像地址']
  ]);
  const phrases = [
    ['REALTIME_WINNERS / EARLY_STARTUP / REVERSAL_REPAIR', '强势候选 / 早期启动 / 修复转强'], ['REALTIME_WINNERS / REVERSAL_REPAIR', '强势候选 / 修复转强'], ['REALTIME_WINNERS / EARLY_STARTUP', '强势候选 / 早期启动'],
    ['Tushare/dailyfetch standardized + V8 rule engine', '付费数据标准化结果 + V8规则引擎'], ['Tushare/dailyfetch standardized PIT snapshot', '付费数据标准化历史快照'],
    ['CPCV Precision >= 0.70', '组合式样本外检验准确率 ≥ 70%'], ['Meta概率 >= 0.85', '二次筛选概率 ≥ 85%'], ['T+1上涨概率 >= 0.62', '次日上涨概率 ≥ 62%'],
    ['Deflated Sharpe / DSR', '修正夏普比率检验'], ['Primary Signal', '主信号'], ['Top Winner Recall', '强势股覆盖率'], ['Singleton Positive', '单一正例'],
    ['amount_acceleration_rank, vwap_strength_rank, event_surprise_rank, persistence_rank', '成交额加速度排名、成交量加权均价强度排名、事件超预期排名、持续性排名'], ['data_provenance + tushare_client', '数据来源记录与付费数据客户端'],
    ['auction_tick: ERROR', '竞价过程数据：暂不可用'], ['EOD_CROSS_SECTION', '收盘横截面'], ['INTRADAY_PIT', '盘中真实快照'], ['REALTIME_WINNERS', '强势候选'], ['EARLY_STARTUP', '早期启动'], ['REVERSAL_REPAIR', '修复转强'],
    ['Meta有效样本', '有效训练样本'], ['Meta有效', '训练有效'], ['Meta 样本达标', '二次筛选样本达标'], ['Meta概率', '二次筛选概率'], ['Meta Label', '二次筛选标签'], ['Meta-Label', '样本表现'],
    ['CPCV', '组合式样本外检验'], ['Conformal', '置信边界'], ['Bootstrap', '重复抽样检验'], ['Monte Carlo', '风险压力测试'], ['Deflated Sharpe', '修正夏普比率'], ['DSR', '稳健性检验'], ['Purged CV', '去重交叉验证'], ['Factor Lab', '因子实验室'],
    ['Precision', '准确率'], ['Recall', '覆盖率'], ['Brier', '概率误差'], ['PSI', '分布漂移指数'], ['FDR', '错误发现率'], ['Sniper 评分', '狙击评分'], ['Alpha Rank', '综合强度排名'], ['Alpha排名', '综合排名'],
    ['Challenger', '候选模型'], ['Champion', '主模型'], ['LightGBM优先', '优先使用梯度提升树模型'], ['abstain', '暂缓判断'], ['AI应用', '人工智能应用'], ['CPO/光模块', '共封装光学/光模块'],
    ['LIVE：READY', '实时数据：就绪'], ['DEMO百分比', '演示数据百分比'], ['PIT安全且带T+1', '决策时点安全且带次日结果'], ['PIT安全', '决策时点安全'], ['PIT记录', '历史实况记录'], ['PIT', '历史实况'],
    ['T+1标签', '次日结果标签'], ['带T+1', '带次日结果'], ['T+1收益', '次日收益'], ['T+1规则估计', '次日规则估计'], ['T+1', '次日'], ['DEMO', '演示数据'], ['LIVE', '实时数据'],
    ['source：', '数据来源：'], ['source:', '数据来源：'], ['observed_at：', '观测时间：'], ['observed_at:', '观测时间：'], ['data_quality：', '数据质量：'], ['Token：', '访问凭证：'], ['Token:', '访问凭证：'], ['镜像：OK', '镜像：正常'], ['standardized', '标准化'], ['rule engine', '规则引擎'], ['NO_PERMISSION', '未授权'], ['SAMPLE_INSUFFICIENT', '样本不足'], ['GRANTED', '已授权'], ['EMPTY', '暂无数据'], ['ERROR', '异常'], ['PASS', '通过'], ['READY', '就绪'], ['NULL', '暂无'], ['OFF', '关闭'], ['Top ', '前 ']
  ];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    if (!node.parentElement || ['SCRIPT', 'STYLE', 'PRE'].includes(node.parentElement.tagName)) return;
    const raw = node.nodeValue;
    const clean = raw.trim();
    if (!clean) return;
    if (exact.has(clean)) { node.nodeValue = raw.replace(clean, exact.get(clean)); return; }
    let value = raw;
    phrases.forEach(([from, to]) => { value = value.split(from).join(to); });
    value = value.replace(/(\d+(?:\.\d+)?)\s*ms\b/g, '$1 毫秒');
    value = value.replace(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/g, '$1 $2');
    value = value.replace(/(\d{6})\.SZ/g, '$1 · 深市').replace(/(\d{6})\.SH/g, '$1 · 沪市').replace(/(\d{6})\.BJ/g, '$1 · 北交所');
    node.nodeValue = value;
  });

  const copy = {
    overview: ['今日作战台', '先确认数据是否可靠，再查看今日机会和执行等级。'], tickets: ['执行票据台', '每一条候选都要核对来源、时点、证据和风险；通过前只观察。'], portfolio: ['持仓防守台', '只管理你已经手工建仓的股票，系统只做风险提示。'], review: ['收盘复盘台', '把当天的发现、信号、持仓和数据完整性收敛为可追溯复盘。'], a_exec: ['A级规则明细', '查看通过执行门槛的信号，并逐项核对风险。'], sniper: ['A+门槛观察', '查看最高等级候选及每项门槛，未全部通过时不执行。'], limitup: ['涨停题材', '观察涨停生态和板块扩散，只用于发现机会。'], sim: ['执行模拟', '评估成交概率、滑点和执行成本，不连接自动交易。'],
    meta: ['样本表现', '使用真实结果评估历史信号，样本不足时不生成胜率。'], conformal: ['置信边界', '查看预测可能波动的范围，避免把概率当成确定结果。'], cpcv: ['样本外检验', '检验不同历史阶段的稳定性并防止过度拟合。'], factor: ['因子分析', '拆解资金、竞价、筹码和趋势证据的贡献。'], mc: ['风险压力', '观察不利情景中的回撤和承受能力。'], drift: ['稳定性监控', '检查数据质量与历史效果是否发生变化。'],
    health: ['数据健康', '查看完整度、时效、异常字段和决策时点安全状态。'], sources: ['数据源与权限', '查看真实接口的配置、权限、延迟、数据量和错误。'], portfolio: ['持仓监控', '手工记录已建仓股票，查看仓位、浮动盈亏和风险提示。'], upgrade: ['升级路线', '查看旧V8经验如何被独立验证，以及新版当前的数据积累进度。']
  };
  const modeName = { DEMO: '演示数据', LIVE: '实时数据', PIT: '历史实况' }[mode] || '演示数据';
  const [title, description] = copy[page] || copy.overview;
  document.title = `${title}｜A股机会雷达`;
  const main = document.querySelector('main');
  if (main) {
    const head = document.createElement('section');
    head.className = 'focus-page-head';
    const link = page === 'overview' ? `<a class="focus-primary" href="/?page=a_exec&mode=${mode}">查看A级执行</a>` : `<a class="focus-back" href="/?page=overview&mode=${mode}">返回决策总览</a>`;
    head.innerHTML = `<div><span class="focus-eyebrow">${modeName} / ${page === 'overview' ? '今天先看' : '功能工作台'}</span><h2>${title}</h2><p>${description}</p></div><div class="focus-page-actions"><span class="focus-mode">${modeName}</span>${link}</div>`;
    main.prepend(head);
  }

  document.querySelectorAll('.grid').forEach((grid) => {
    Array.from(grid.children).filter((item) => item.classList.contains('card')).forEach((card) => {
      if (card.querySelector(':scope > .kpi')) card.classList.add('focus-metric');
      if (page === 'a_exec' && card.querySelector(':scope > .grid .kpi')) {
        card.classList.add('focus-signal-card');
        const evidence = Array.from(card.querySelectorAll(':scope > p.muted'));
        if (evidence.length) {
          const details = document.createElement('details');
          details.className = 'signal-evidence';
          details.innerHTML = '<summary>查看完整数据证据与来源</summary><div class="signal-evidence-body"></div>';
          card.appendChild(details);
          const evidenceBody = details.querySelector('.signal-evidence-body');
          evidence.forEach((item) => evidenceBody.appendChild(item));
        }
      }
      if ((card.textContent || '').includes('警示')) card.classList.add('focus-alert');
    });
  });
  if (!['health', 'sources'].includes(page)) {
    const names = ['数据血缘', '安全状态', '数据边界'];
    const cards = Array.from(document.querySelectorAll('.grid > .card')).filter((card) => {
      const titleNode = card.querySelector('.panel-title');
      return titleNode && names.some((name) => titleNode.textContent.includes(name));
    });
    if (cards.length) {
      const first = cards[0];
      const parent = first.parentElement;
      const details = document.createElement('details');
      details.className = 'focus-technical';
      details.innerHTML = '<summary><span><b>数据来源与安全说明</b><small>需要核对来源、时间和限制时再查看</small></span></summary><div class="focus-technical-body"></div>';
      parent.insertBefore(details, first);
      const body = details.querySelector('.focus-technical-body');
      cards.forEach((card) => body.appendChild(card));
    }
  }
  document.querySelectorAll('table').forEach((table) => {
    if (!table.parentElement.classList.contains('table-scroll')) {
      const shell = document.createElement('div');
      shell.className = 'focus-table-shell';
      table.parentNode.insertBefore(shell, table);
      shell.appendChild(table);
    }
    const headers = Array.from(table.querySelectorAll('thead th, tr:first-child th'));
    table.classList.add(headers.length > 6 ? 'focus-wide' : 'focus-compact');
    const changeIndex = headers.findIndex((header) => header.textContent.includes('涨跌'));
    if (changeIndex >= 0) table.querySelectorAll('tbody tr').forEach((row) => {
      const cell = row.children[changeIndex];
      const value = cell ? Number.parseFloat(cell.textContent.replace('%', '')) : NaN;
      if (Number.isFinite(value)) cell.classList.add(value > 0 ? 'market-up' : value < 0 ? 'market-down' : 'market-flat');
    });
  });
  document.querySelectorAll('tr').forEach((row) => {
    const cells = Array.from(row.querySelectorAll('td'));
    const status = cells.find((cell) => cell.textContent.trim() === '样本不足');
    if (!status) return;
    const ratio = cells.map((cell) => cell.textContent).join(' ').match(/(\d+)\s*\/\s*(\d+)/);
    status.textContent = ratio ? `样本不足（还差 ${Math.max(Number(ratio[2]) - Number(ratio[1]), 0)} 条）` : '样本不足（暂不生成结果）';
    status.classList.add('status-insufficient');
  });
})();
const cardDestinations = {
  '今日发现候选': ['tickets', '查看候选清单'],
  '真实交易信号': ['meta', '查看真实样本'],
  '人工持仓': ['portfolio', '查看我的持仓'],
  '决策时点安全记录': ['health', '查看数据健康'],
  '盘中快照进度': ['overview', '查看市场总览'],
  '持仓防守状态': ['portfolio', '查看我的持仓'],
  '最新盘中快照': ['overview', '查看市场总览'],
  '覆盖股票': ['overview', '查看市场总览'],
  '发现候选': ['tickets', '查看候选清单'],
  '交易信号有效样本': ['meta', '查看真实样本'],
  '统计资格': ['meta', '查看真实样本'],
  '异常接口': ['sources', '查看数据源状态']
};
document.querySelectorAll('.card').forEach(card => {
  const title = (card.querySelector('.panel-title') || {}).textContent;
  const target = cardDestinations[(title || '').trim()];
  if (!target || card.querySelector('.card-action')) return;
  card.classList.add('card-clickable');
  card.setAttribute('role', 'link');
  card.setAttribute('tabindex', '0');
  const action = document.createElement('button');
  action.type = 'button';
  action.className = 'card-action';
  action.textContent = target[1];
  card.appendChild(action);
  const openTarget = () => {
    const currentMode = new URLSearchParams(window.location.search).get('mode') || 'LIVE';
    window.location.href = '?page=' + target[0] + '&mode=' + currentMode;
  };
  action.addEventListener('click', event => { event.stopPropagation(); openTarget(); });
  card.addEventListener('click', event => { if (!event.target.closest('a,button,input,select,textarea')) openTarget(); });
  card.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openTarget(); } });
});
const liveMode = new URLSearchParams(window.location.search).get('mode') === 'LIVE';
const livePage = new URLSearchParams(window.location.search).get('page') || 'overview';
const noAutoRefreshPages = new Set(['portfolio', 'stock_detail']);
if (liveMode && !noAutoRefreshPages.has(livePage)) {
  const state = document.querySelector('.decision-state');
  if (state) state.title = 'LIVE模式每60秒自动刷新一次';
  window.setTimeout(() => window.location.reload(), 60000);
}
</script>
"""


def render_page(*args, **kwargs):
    html = _render_original(*args, **kwargs)
    page = kwargs.get("page", args[0] if args else "overview")
    status = kwargs.get("status") or core.dashboard_status()
    mode = kwargs.get("mode", "DEMO")
    if page in {"upgrade", "tickets", "review"}:
        body = {
            "upgrade": render_upgrade_page,
            "tickets": render_ticket_page,
            "review": render_review_page,
        }[page](status, mode)
        html = re.sub(r"(<main>).*?(</main>)", r"\1" + body + r"\2", html, count=1, flags=re.S)
    title = PAGE_TITLES.get(page)
    if title:
        html = re.sub(r"(<h1>).*?(</h1>)", r"\1" + title + r"\2", html, count=1, flags=re.S)
    intro = _decision_brief(page, mode, status)
    if page == "overview":
        intro += _operator_summary(mode, status)
    html = html.replace("<main>", "<main>" + intro, 1)
    if "</style>" in html:
        html = html.replace("</style>", CSS + "</style>", 1)
    else:
        html = html.replace("</head>", "<style>" + CSS + "</style></head>", 1)
    return html.replace("</body>", SCRIPT + "</body>", 1)


CSS += r'''
/* Same visual language as a professional domestic market terminal: dark, dense, decisive. */
:root { --ths-bg:#0b0f16; --ths-panel:#121923; --ths-line:#2a3545; --ths-text:#e7edf5; --ths-muted:#92a0b2; --ths-gold:#d5a64e; --ths-red:#f0525f; --ths-green:#28b88a; }
html, body { background:var(--ths-bg) !important; color:var(--ths-text) !important; }
body, input, select, textarea, button, table { font-family:"Microsoft YaHei UI","PingFang SC",sans-serif !important; }
header, .topbar, .app-header, .header { background:linear-gradient(90deg,#09111c 0%,#101b2a 100%) !important; border-bottom:1px solid #263447 !important; box-shadow:none !important; }
aside, .sidebar, .side-nav, nav { background:#0d141e !important; border-color:#253246 !important; }
main, .main, .main-content, .page-content, .content { background:#0f1621 !important; color:var(--ths-text) !important; }
.workspace-brand, .brand, .logo, h1, h2, h3, h4, strong { color:var(--ths-text) !important; }
.nav-section, .nav-group, .nav-item, .sidebar a, nav a { color:#b9c5d3 !important; border-color:transparent !important; }
.nav-item.active, .nav-item:hover, .sidebar a.active, .sidebar a:hover, nav a.active, nav a:hover { background:linear-gradient(90deg,#213955,#182a3e) !important; color:#fff !important; border-left-color:var(--ths-gold) !important; }
.card, .panel, .content-card, .module, .stat-card, .notice, .section, article { background:var(--ths-panel) !important; border:1px solid var(--ths-line) !important; box-shadow:none !important; color:var(--ths-text) !important; }
.card:hover, .card-clickable:hover { border-color:#566d89 !important; background:#162131 !important; }
.card-action { width:auto !important; min-height:28px !important; padding:4px 10px !important; margin-top:10px !important; border:1px solid #4e8ed0 !important; border-radius:5px !important; background:#1d5f9f !important; color:#fff !important; cursor:pointer !important; font-size:12px !important; font-weight:800 !important; }
.card-action::after { content:none !important; }
.decision-brief, .page-guide, .hero, .banner, .info-banner { background:linear-gradient(100deg,#111e2d,#13253a) !important; border-color:#31577e !important; color:var(--ths-text) !important; }
.warning, .alert-warning, .notice-warning { background:#2a2114 !important; border-color:#775a27 !important; color:#f5d790 !important; }
.success, .alert-success { background:#11271f !important; border-color:#28785b !important; color:#93dfbd !important; }
table { background:var(--ths-panel) !important; color:var(--ths-text) !important; border-color:var(--ths-line) !important; }
thead th, table th { background:#193e63 !important; color:#f4f7fb !important; border-color:#355574 !important; }
tbody tr, table td { background:var(--ths-panel) !important; color:#dce5ef !important; border-color:#273346 !important; }
tbody tr:nth-child(even) td { background:#151e2a !important; }
tbody tr:hover td { background:#1b2b3d !important; }
a { color:#69aef8 !important; }
button, .button, .btn { background:#1d5f9f !important; color:#fff !important; border-color:#3178ba !important; box-shadow:none !important; }
button:hover, .button:hover, .btn:hover { background:#2676bd !important; }
input, select, textarea { background:#0d141e !important; color:#eef4fb !important; border-color:#34465d !important; }
input:focus, select:focus, textarea:focus { border-color:#d5a64e !important; outline:none !important; box-shadow:0 0 0 2px rgba(213,166,78,.16) !important; }
.badge, .tag, .pill { background:#20324a !important; color:#c9dcf5 !important; border-color:#3b5778 !important; }
.positive, .up, .rise, .red, .text-red { color:var(--ths-red) !important; }
.negative, .down, .fall, .green, .text-green { color:var(--ths-green) !important; }
.stat-value, .metric-value, .key-number { color:#f1d18b !important; }
.muted, .secondary, small, .subtitle, .hint { color:var(--ths-muted) !important; }
/* Override the legacy per-page light styles, keeping one readable terminal hierarchy. */
.focus-nav { background:#0d141e !important; color:#dce5ef !important; border-right-color:#2a3545 !important; }
.focus-nav .nav-group, .focus-nav .nav-item, .focus-nav a, .focus-nav p, .focus-nav span { color:#c7d2df !important; }
.focus-nav .nav-item.active, .focus-nav .nav-item:hover, .focus-nav a.active, .focus-nav a:hover { background:linear-gradient(90deg,#224b75,#172b42) !important; color:#fff !important; }
main h1, main h2, main h3 { color:#f1f5fa !important; }
.focus-eyebrow, .decision-eyebrow { color:#68b1ff !important; font-size:12px !important; }
.focus-mode { color:#ffd18a !important; background:#342314 !important; border-color:#8f612c !important; }
.focus-back { color:#a8c9ed !important; }
.decision-state { color:#e9f3ff !important; background:#1c5b98 !important; }
.decision-brief p, .focus-safety, .card p, .card .muted, .card .hint { color:#aebdcd !important; }
.panel-title, .focus-signal-card .panel-title { background:#172332 !important; color:#f1f5fa !important; border-color:#30455e !important; font-size:14px !important; line-height:1.55 !important; }
.focus-signal-card .grid { background:#142131 !important; border-color:#30455e !important; }
.focus-signal-card .grid > * { background:#142131 !important; color:#eaf1f8 !important; border-color:#30455e !important; }
.focus-signal-card code { background:#203c5b !important; color:#8fc7ff !important; }
.focus-signal-card summary { background:#172332 !important; color:#b9d8f8 !important; border-color:#30455e !important; }
.focus-signal-card .kpi, .focus-metric .kpi, .card .kpi { color:#f2c56b !important; }
.focus-signal-card > .grid .kpi { color:#f2c56b !important; }
.focus-signal-card .warn { background:#3a2b16 !important; color:#ffd990 !important; border-color:#80602a !important; }
.focus-signal-card hr, .focus-signal-card .line { border-color:#30455e !important; }
.card { border-radius:8px !important; }

/* Discovery timeline: separate what was known at discovery from the performance after discovery. */
.timeline-legend { display:flex; flex-wrap:wrap; gap:7px; align-items:center; margin:0 0 9px; font-size:11px; font-weight:700; }
.timeline-legend span { padding:4px 8px; border:1px solid; border-radius:4px; }
.legend-baseline { color:#b8d5f2; background:#172b41; border-color:#355f87 !important; }
.legend-up { color:#ff9da4; background:#3a1e26; border-color:#87414e !important; }
.legend-flat { color:#c3cfdb; background:#202b38; border-color:#465566 !important; }
.legend-down { color:#82dbb4; background:#132c25; border-color:#357b63 !important; }
.discovery-timeline td { white-space:normal; }
.discovery-timeline time { color:#d9e5f1; font-weight:700; white-space:nowrap; }
.discovery-timeline td:nth-child(3) { background:#13263a !important; }
.discovery-timeline td:nth-child(4) { background:#17202d !important; }
.discovery-baseline, .discovery-current, .post-move { display:grid; gap:2px; min-width:104px; padding:7px 8px; border-radius:5px; }
.discovery-baseline { background:#19334b; border:1px solid #315e86; }
.discovery-current { background:#1b2634; border:1px solid #3a4e64; }
.discovery-baseline small, .discovery-current small, .post-move small { color:#9eb5ca !important; font-size:10px; font-weight:700; }
.discovery-baseline b, .discovery-current b, .post-move b { font-family:Bahnschrift,"Microsoft YaHei UI",sans-serif; font-size:16px; line-height:1.15; }
.discovery-baseline b { color:#c5e0fa !important; }
.discovery-current b { color:#edf4fb !important; }
.discovery-baseline span, .discovery-current span, .post-move span { color:#aebdcd !important; font-size:10.5px; white-space:nowrap; }
.post-move { border:1px solid; }
.post-move.move-strong, .post-move.move-positive { background:#3a1d25; border-color:#8a4050; }
.post-move.move-strong b, .post-move.move-positive b { color:#ff737e !important; }
.post-move.move-strong small, .post-move.move-positive small { color:#ffb5bb !important; }
.post-move.move-limit { background:#482815; border-color:#a7652b; }
.post-move.move-limit b { color:#ffc06d !important; }
.post-move.move-limit small { color:#ffd49a !important; }
.post-move.move-flat, .post-move.move-unavailable { background:#202a36; border-color:#48586a; }
.post-move.move-flat b, .post-move.move-unavailable b { color:#cbd5df !important; }
.post-move.move-negative { background:#132d26; border-color:#377e65; }
.post-move.move-negative b { color:#62d5a6 !important; }
.post-move.move-negative small { color:#a3e9cf !important; }
.timeline-state { display:inline-block; max-width:255px; padding:5px 8px; border-left:3px solid; font-size:11px; font-weight:720; line-height:1.45; }
.timeline-state.state-strong, .timeline-state.state-positive { color:#ffb0b6 !important; background:#301d25; border-color:#ed5a65; }
.timeline-state.state-limit { color:#ffd18a !important; background:#3c2717; border-color:#e19440; }
.timeline-state.state-negative { color:#9ce3c4 !important; background:#142c25; border-color:#39a97b; }
.timeline-state.state-neutral { color:#c6d2df !important; background:#202b38; border-color:#607286; }
@media (max-width: 900px) { .timeline-legend { gap:5px; } .timeline-legend span { font-size:10px; padding:3px 6px; } .discovery-baseline, .discovery-current, .post-move { min-width:94px; } }
'''

CSS += r'''
.operator-summary { margin:0 0 14px; overflow:hidden; background:#121d2b; border:1px solid #31506f; border-radius:9px; }
.operator-heading { display:flex; align-items:center; gap:14px; padding:11px 15px; background:linear-gradient(90deg,#17324d,#14263a); border-bottom:1px solid #2f4b68; }
.operator-heading span { color:#79b8f7; font-size:12px; font-weight:800; }
.operator-heading strong { color:#f4f8fc; font-size:13px; font-weight:700; }
.operator-steps { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); }
.operator-steps a { min-height:88px; padding:12px 14px; color:#e5edf6 !important; text-decoration:none !important; border-right:1px solid #2b435d; }
.operator-steps a:last-child { border-right:0; }
.operator-steps a:hover { background:#1a3048; }
.operator-steps b,.operator-steps strong,.operator-steps small { display:block; }
.operator-steps b { color:#78b6f2; font-size:10px; letter-spacing:.08em; }
.operator-steps strong { margin-top:4px; color:#f2c56b; font-size:14px; }
.operator-steps small { margin-top:5px; color:#9db0c4; font-size:11px; line-height:1.45; }
@media (max-width: 900px) { .operator-steps { grid-template-columns:repeat(2,minmax(0,1fr)); } .operator-steps a:nth-child(2) { border-right:0; } .operator-steps a:nth-child(-n+2) { border-bottom:1px solid #2b435d; } }
'''

core._render_nav = render_nav
core.render_page = render_page


def main():
    host = os.environ.get("RADAR_HOST", "127.0.0.1")
    port = int(os.environ.get("RADAR_PORT", "8791"))
    server = ThreadingHTTPServer((host, port), core.H)
    print("A股机会雷达 V8.6.6 任务工作台")
    print("http://%s:%s/?page=overview&mode=LIVE" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
