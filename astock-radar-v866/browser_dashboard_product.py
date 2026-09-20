"""Product-focused Chinese UI for AStock Radar V8.6.6.

The original dashboard remains the single source of routes, calculations and
data. This module supplies a task-oriented presentation layer only.
"""

from html import escape
from http.server import ThreadingHTTPServer
import os

import browser_dashboard as core


_original_render_page = core.render_page


NAVIGATION = (
    ("今日决策", (
        ("overview", "市", "今日总览", "市场与机会"),
        ("a_exec", "A", "A级执行", "可执行信号"),
        ("sniper", "A+", "A+狙击", "严格门槛观察"),
        ("limitup", "板", "连板观察", "板块与涨停生态"),
    )),
    ("效果验证", (
        ("meta", "样", "样本表现", "真实结果统计"),
        ("conformal", "界", "置信边界", "结果可信范围"),
        ("cpcv", "验", "样本外检验", "防止过度拟合"),
        ("factor", "因", "因子分析", "证据贡献拆解"),
        ("sim", "执", "执行模拟", "成交与滑点"),
        ("mc", "压", "风险压力", "极端情景测试"),
        ("drift", "稳", "稳定性监控", "数据与效果漂移"),
    )),
    ("数据管理", (
        ("health", "健", "数据健康", "完整度与时效"),
        ("sources", "源", "数据源与权限", "付费接口状态"),
    )),
)


def render_navigation(page_key, mode="DEMO"):
    safe_mode = mode if mode in {"DEMO", "LIVE", "PIT"} else "DEMO"
    html = [
        "<nav class='product-nav' aria-label='工作台导航'>",
        "<div class='nav-heading'><span class='nav-logo'>V8</span>"
        "<span><b>机会雷达工作台</b><small>决策 · 验证 · 数据</small></span></div>",
    ]
    for group_name, items in NAVIGATION:
        html.append("<p class='product-nav-group'>%s</p>" % escape(group_name))
        for key, glyph, label, helper in items:
            selected = " selected" if key == page_key else ""
            html.append(
                "<a class='product-nav-item%s' href='/?page=%s&amp;mode=%s' title='%s'>"
                "<span class='product-nav-glyph'>%s</span>"
                "<span class='product-nav-copy'><b>%s</b><small>%s</small></span>"
                "</a>"
                % (
                    selected,
                    escape(key),
                    escape(safe_mode),
                    escape(helper),
                    escape(glyph),
                    escape(label),
                    escape(helper),
                )
            )
    html.extend((
        "<div class='nav-foot'><span></span><div><b>安全验证模式</b>"
        "<small>关闭自动交易与自动调参</small></div></div>",
        "</nav>",
    ))
    return "".join(html)


CSS = r"""
:root {
  --blue-950: #071b30;
  --blue-900: #0a2949;
  --blue-800: #104978;
  --blue-700: #1768b2;
  --blue-600: #287dcc;
  --blue-100: #e9f3ff;
  --blue-050: #f4f8fd;
  --canvas: #f3f6fa;
  --surface: #ffffff;
  --ink: #172b42;
  --text: #45596e;
  --muted: #7b8a9c;
  --line: #dce4ed;
  --line-strong: #cbd7e3;
  --red: #d73b32;
  --green: #15875e;
  --amber: #a96f16;
}

* { box-sizing: border-box; }
html { background: var(--canvas); }
body {
  margin: 0 !important;
  color: var(--ink) !important;
  background: var(--canvas) !important;
  font-family: "Microsoft YaHei UI", "Source Han Sans SC", "PingFang SC", sans-serif !important;
  font-size: 14px !important;
  line-height: 1.58 !important;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}

header {
  position: sticky !important;
  top: 0 !important;
  z-index: 80 !important;
  min-height: 66px !important;
  padding: 10px 18px !important;
  display: grid !important;
  grid-template-columns: 230px minmax(0, 1fr) auto !important;
  grid-template-rows: 25px 17px !important;
  align-items: center !important;
  column-gap: 18px !important;
  color: var(--ink) !important;
  background: rgba(255, 255, 255, .96) !important;
  border: 0 !important;
  border-bottom: 1px solid var(--line-strong) !important;
  box-shadow: 0 3px 14px rgba(25, 52, 80, .065) !important;
  backdrop-filter: blur(14px);
}
header h1 {
  grid-column: 1 !important;
  grid-row: 1 !important;
  margin: 0 !important;
  color: var(--blue-900) !important;
  font-size: 19px !important;
  font-weight: 780 !important;
  line-height: 1.2 !important;
  letter-spacing: -.035em !important;
}
header .sub {
  grid-column: 1 !important;
  grid-row: 2 !important;
  color: #7990a7 !important;
  font-size: 9px !important;
  font-weight: 650 !important;
  letter-spacing: .08em !important;
}
header .mode-zone {
  grid-column: 2 !important;
  grid-row: 1 / 3 !important;
  min-width: 0;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-end !important;
  gap: 10px !important;
}
header .ok {
  grid-column: 3 !important;
  grid-row: 1 / 3 !important;
  padding: 5px 9px !important;
  color: #1764a7 !important;
  background: #edf6ff !important;
  border: 1px solid #c6dcf0 !important;
  border-radius: 5px !important;
  font-size: 10px !important;
  font-weight: 700 !important;
  white-space: nowrap;
}
.mode-controls {
  display: inline-flex !important;
  gap: 2px !important;
  padding: 3px !important;
  background: #edf1f5 !important;
  border: 1px solid #d9e1ea !important;
  border-radius: 7px !important;
}
.mode-link {
  min-width: 50px;
  padding: 5px 9px !important;
  color: #687b90 !important;
  background: transparent !important;
  border: 0 !important;
  border-radius: 5px !important;
  font-size: 10px !important;
  font-weight: 720 !important;
  text-align: center;
  text-decoration: none !important;
}
.mode-link.active {
  color: #fff !important;
  background: linear-gradient(135deg, var(--blue-700), var(--blue-600)) !important;
  box-shadow: 0 3px 8px rgba(23, 104, 178, .2) !important;
}
.mode-pill { display: none !important; }
.source-line {
  max-width: 470px;
  overflow: hidden;
  color: #718397 !important;
  font-size: 10px !important;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wrap {
  display: grid !important;
  grid-template-columns: 244px minmax(0, 1fr) !important;
  align-items: start !important;
  min-height: calc(100vh - 66px);
}
.product-nav {
  position: sticky !important;
  top: 66px !important;
  width: 244px !important;
  height: calc(100vh - 66px) !important;
  padding: 13px 9px 30px !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
  color: var(--ink) !important;
  background: linear-gradient(90deg, #0b3158 0, #0b3158 57px, #fff 57px, #fff 100%) !important;
  border-right: 1px solid var(--line-strong) !important;
  box-shadow: 5px 0 18px rgba(25, 50, 76, .035);
  scrollbar-width: thin;
  scrollbar-color: #9badbf transparent;
}
.nav-heading {
  display: grid;
  grid-template-columns: 39px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-height: 56px;
  margin-bottom: 17px;
  padding: 8px 9px;
  background: linear-gradient(90deg, rgba(255,255,255,.05) 0 47px, #f4f8fc 47px);
  border: 1px solid #d5e1ed;
  border-left-color: rgba(122, 177, 226, .3);
  border-radius: 8px;
}
.nav-logo {
  display: grid;
  place-items: center;
  width: 37px;
  height: 37px;
  color: #fff;
  background: linear-gradient(145deg, #3b92dd, #1768ae);
  border: 1px solid rgba(255,255,255,.2);
  border-radius: 7px;
  font-family: Bahnschrift, "Segoe UI Variable Display", sans-serif;
  font-size: 13px;
  font-weight: 760;
  box-shadow: 0 6px 14px rgba(6, 38, 70, .22);
}
.nav-heading b,
.nav-heading small { display: block; }
.nav-heading b { color: #183752; font-size: 14px; font-weight: 760; line-height: 1.35; }
.nav-heading small { margin-top: 3px; color: #7890a5; font-size: 9px; font-weight: 550; }
.product-nav-group {
  margin: 17px 8px 6px 59px !important;
  padding-bottom: 5px;
  color: #8797a8 !important;
  border-bottom: 1px solid #e6ebf0;
  font-size: 10px !important;
  font-weight: 720 !important;
  letter-spacing: .08em;
}
.product-nav-item {
  display: grid !important;
  grid-template-columns: 38px minmax(0, 1fr);
  align-items: center !important;
  gap: 12px !important;
  min-height: 49px;
  margin: 2px 0 !important;
  padding: 6px 8px !important;
  color: #344c62 !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 6px !important;
  text-decoration: none !important;
  transition: background .15s ease, color .15s ease, border-color .15s ease;
}
.product-nav-item:hover {
  color: var(--blue-800) !important;
  background: linear-gradient(90deg, rgba(55, 137, 208, .13) 0 48px, #f3f7fb 48px) !important;
  border-color: #dae5ef !important;
}
.product-nav-item.selected {
  color: #114f85 !important;
  background: linear-gradient(90deg, #1769b2 0 48px, #e8f3ff 48px) !important;
  border-color: #c7dcef !important;
  border-right: 3px solid var(--blue-600) !important;
  box-shadow: 0 4px 12px rgba(30, 92, 147, .08);
}
.product-nav-glyph {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: #b8d6ee;
  background: rgba(255, 255, 255, .055);
  border: 1px solid rgba(153, 197, 234, .18);
  border-radius: 5px;
  font-size: 10px;
  font-weight: 760;
}
.selected .product-nav-glyph { color: #fff; background: rgba(255,255,255,.1); border-color: rgba(255,255,255,.25); }
.product-nav-copy { min-width: 0; }
.product-nav-copy b,
.product-nav-copy small { display: block; }
.product-nav-copy b { font-size: 13px; font-weight: 700; line-height: 1.35; }
.product-nav-copy small { margin-top: 3px; overflow: hidden; color: #8595a6; font-size: 10px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; }
.selected .product-nav-copy small { color: #6787a3; }
.nav-foot {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  margin-top: 18px;
  padding: 10px 8px;
}
.nav-foot > span { width: 8px; height: 8px; margin: 5px auto 0; background: #e1a83f; border: 2px solid #0b3158; border-radius: 50%; box-shadow: 0 0 0 3px rgba(225,168,63,.15); }
.nav-foot b,
.nav-foot small { display: block; }
.nav-foot b { color: #5e7183; font-size: 10px; }
.nav-foot small { margin-top: 2px; color: #94a1ae; font-size: 9px; line-height: 1.45; }

main {
  width: 100% !important;
  min-width: 0 !important;
  max-width: none !important;
  margin: 0 !important;
  padding: 18px 20px 44px !important;
  color: var(--ink) !important;
}
.product-page-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  min-height: 78px;
  margin-bottom: 12px;
  padding: 4px 2px 13px;
  border-bottom: 1px solid var(--line-strong);
}
.product-page-head .eyebrow { display: block; margin-bottom: 3px; color: var(--blue-700); font-size: 10px; font-weight: 760; letter-spacing: .08em; }
.product-page-head h2 { margin: 0 !important; color: #15334f !important; font-size: 26px !important; font-weight: 780 !important; line-height: 1.25 !important; letter-spacing: -.045em; }
.product-page-head p { margin: 4px 0 0 !important; color: #64778b !important; font-size: 12px !important; }
.page-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 6px; }
.page-chip { padding: 5px 8px; color: #526a81; background: #fff; border: 1px solid var(--line); border-radius: 5px; font-size: 10px; font-weight: 650; white-space: nowrap; }
.page-chip.mode { color: #a0332a; background: #fff0ee; border-color: #efcac6; }
.primary-action { padding: 6px 11px; color: #fff !important; background: linear-gradient(135deg, var(--blue-700), var(--blue-600)); border: 1px solid var(--blue-700); border-radius: 5px; font-size: 10px; font-weight: 720; text-decoration: none !important; box-shadow: 0 4px 10px rgba(23,104,178,.15); }

.grid {
  display: grid !important;
  grid-template-columns: repeat(12, minmax(0, 1fr)) !important;
  gap: 10px !important;
}
.grid > .card { grid-column: span 6 !important; }
.grid > .card:nth-child(-n+4) { grid-column: span 3 !important; }
.grid > .card.w4 { grid-column: 1 / -1 !important; }
.card {
  min-width: 0;
  padding: 14px 15px !important;
  color: var(--text) !important;
  background: var(--surface) !important;
  border: 1px solid var(--line) !important;
  border-radius: 8px !important;
  box-shadow: 0 5px 17px rgba(25, 53, 82, .045) !important;
}
.grid > .card:nth-child(-n+4) {
  position: relative;
  min-height: 108px;
  overflow: hidden;
  border-top: 3px solid var(--blue-600) !important;
}
.grid > .card:nth-child(-n+4)::before {
  content: "";
  position: absolute;
  right: 13px;
  top: 15px;
  width: 6px;
  height: 6px;
  background: #8cb9e5;
  border-radius: 50%;
  box-shadow: 0 0 0 4px #edf5fd;
}
.grid > .card:nth-child(-n+4)::after { display: none !important; }
.panel-title { margin: 0 0 8px !important; color: #607488 !important; font-size: 11px !important; font-weight: 720 !important; letter-spacing: .025em; }
.kpi { margin: 0 0 2px !important; color: #193953 !important; font-family: Bahnschrift, "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif !important; font-size: clamp(28px, 3vw, 38px) !important; font-weight: 610 !important; line-height: 1.15 !important; letter-spacing: -.04em; }
.card p { color: var(--text) !important; font-size: 12px; }
.card ul { margin: 5px 0 0; padding-left: 19px; }
.card li { margin: 3px 0; font-size: 12px; }
.card.alert-panel,
.card:has(.panel-title:first-child):has(li) { background: #fffaf0 !important; border-color: #ead8b3 !important; }
.warn { margin: 6px 0 10px !important; padding: 8px 10px !important; color: #785015 !important; background: #fff5df !important; border-left: 3px solid #bc8428 !important; border-radius: 3px !important; font-size: 11px !important; font-weight: 650 !important; }
.ok { color: var(--blue-700) !important; }
.bad, .err { color: var(--red) !important; }

.table-scroll,
.product-table-shell { width: 100%; overflow-x: auto !important; border: 1px solid var(--line-strong) !important; border-radius: 5px !important; scrollbar-color: #93a8bb #eef2f6; }
table { width: 100% !important; min-width: 1080px !important; border-collapse: separate !important; border-spacing: 0 !important; color: #354b60 !important; background: #fff !important; font-size: 12px !important; }
thead th, table th { position: sticky; top: 0; z-index: 3; padding: 9px 8px !important; color: #e2edf7 !important; background: #163d62 !important; border-bottom: 1px solid #315e83 !important; font-size: 10.5px !important; font-weight: 720 !important; line-height: 1.35 !important; text-align: left; white-space: normal; }
tbody td, table td { padding: 9px 8px !important; color: #354b60 !important; background: #fff !important; border-bottom: 1px solid #e3e9ef !important; line-height: 1.45 !important; vertical-align: middle !important; }
tbody tr:nth-child(even) td { background: #f8fafc !important; }
tbody tr:hover td { background: #ebf4fd !important; }
table th:first-child, table td:first-child { position: sticky; left: 0; z-index: 2; min-width: 88px; box-shadow: 5px 0 9px rgba(26,50,75,.035); }
table th:first-child { z-index: 4; background: #163d62 !important; }
table a { color: #1764ae !important; font-weight: 720 !important; text-decoration: none !important; }
table a:hover { color: #0d497f !important; text-decoration: underline !important; }
.market-up { color: var(--red) !important; font-weight: 720 !important; }
.market-down { color: var(--green) !important; font-weight: 720 !important; }
code, pre { color: #2d455b !important; background: #edf2f7 !important; border-color: var(--line) !important; }

@media (max-width: 1120px) {
  header { grid-template-columns: 225px minmax(0, 1fr) auto !important; }
  .source-line { display: none !important; }
}
@media (max-width: 850px) and (min-width: 701px) {
  .wrap { grid-template-columns: 218px minmax(0, 1fr) !important; }
  .product-nav { width: 218px !important; background: linear-gradient(90deg, #0b3158 0, #0b3158 52px, #fff 52px, #fff 100%) !important; }
  .nav-heading { grid-template-columns: 34px minmax(0,1fr); gap: 10px; }
  .nav-logo { width: 33px; height: 33px; }
  .product-nav-group { margin-left: 53px !important; }
  .product-nav-item { grid-template-columns: 34px minmax(0,1fr); gap: 9px !important; }
  .product-nav-copy b { font-size: 12px; }
  .product-nav-copy small { font-size: 9px; }
  main { padding: 14px 12px 38px !important; }
  .grid > .card:nth-child(-n+4) { grid-column: span 6 !important; }
}
@media (max-width: 700px) {
  header { position: relative !important; display: flex !important; flex-wrap: wrap; gap: 4px 10px !important; min-height: 96px !important; padding: 10px 12px !important; }
  header h1, header .sub { width: calc(100% - 105px); }
  header .ok { position: absolute; right: 11px; top: 12px; }
  header .mode-zone { width: 100%; justify-content: flex-start !important; }
  .wrap { display: block !important; }
  .product-nav { position: sticky !important; top: 0 !important; z-index: 70; display: flex !important; gap: 5px; width: 100% !important; height: auto !important; padding: 7px !important; overflow-x: auto !important; overflow-y: hidden !important; background: #0b3158 !important; }
  .nav-heading, .product-nav-group, .nav-foot { display: none; }
  .product-nav-item { display: flex !important; flex: 0 0 auto; min-height: 39px; padding: 5px 10px !important; color: #c4d9eb !important; }
  .product-nav-item.selected { color: #fff !important; background: #1769b2 !important; border-right-width: 1px !important; }
  .product-nav-glyph, .product-nav-copy small { display: none; }
  .product-nav-copy b { font-size: 12px; white-space: nowrap; }
  main { padding: 12px 9px 32px !important; }
  .product-page-head { grid-template-columns: 1fr; gap: 9px; }
  .page-actions { justify-content: flex-start; }
  .grid > .card, .grid > .card:nth-child(-n+4) { grid-column: 1 / -1 !important; }
}

/* Focus edition: conclusions first, technical evidence on demand. */
header {
  min-height: 58px !important;
  padding: 9px 16px !important;
  grid-template-columns: 205px minmax(0, 1fr) auto !important;
  grid-template-rows: 1fr !important;
  color: #fff !important;
  background: linear-gradient(90deg, #092846, #0d3a63) !important;
  border-bottom-color: #28577f !important;
  box-shadow: 0 3px 12px rgba(6, 27, 48, .16) !important;
}
header h1 {
  grid-row: 1 !important;
  color: #fff !important;
  font-size: 18px !important;
}
header .sub { display: none !important; }
header .mode-zone { grid-row: 1 !important; }
header .ok {
  grid-row: 1 !important;
  color: #d6e9fa !important;
  background: rgba(255, 255, 255, .08) !important;
  border-color: rgba(181, 214, 243, .25) !important;
}
.mode-controls {
  background: rgba(0, 10, 21, .28) !important;
  border-color: rgba(154, 197, 235, .25) !important;
}
.mode-link { color: #a9c3da !important; }
.mode-link.active { background: #2c7dcc !important; box-shadow: none !important; }
.source-line { color: #a6bfd5 !important; }

.wrap { grid-template-columns: 212px minmax(0, 1fr) !important; min-height: calc(100vh - 58px) !important; }
.product-nav {
  top: 58px !important;
  width: 212px !important;
  height: calc(100vh - 58px) !important;
  padding: 12px 10px 28px !important;
  background: #fff !important;
  border-right-color: #d8e1ea !important;
  box-shadow: 4px 0 16px rgba(26, 50, 74, .035) !important;
}
.nav-heading {
  display: block !important;
  min-height: auto !important;
  margin: 0 0 18px !important;
  padding: 15px 14px !important;
  color: #fff !important;
  background: linear-gradient(135deg, #0d355b, #1764a7) !important;
  border: 0 !important;
  border-radius: 8px !important;
  box-shadow: 0 8px 20px rgba(17, 73, 122, .15) !important;
}
.nav-logo { display: none !important; }
.nav-heading b { color: #fff !important; font-size: 15px !important; font-weight: 760 !important; }
.nav-heading small { margin-top: 4px !important; color: #b9d6ef !important; font-size: 10px !important; }
.product-nav-group {
  margin: 18px 10px 6px !important;
  padding: 0 !important;
  color: #96a2ae !important;
  border: 0 !important;
  font-size: 10px !important;
  font-weight: 720 !important;
  letter-spacing: .1em !important;
}
.product-nav-item {
  display: block !important;
  min-height: auto !important;
  margin: 2px 0 !important;
  padding: 10px 12px !important;
  color: #42566a !important;
  background: transparent !important;
  border: 0 !important;
  border-left: 3px solid transparent !important;
  border-radius: 5px !important;
}
.product-nav-item:hover {
  color: #155a98 !important;
  background: #f3f7fb !important;
  border-left-color: #b4d2ec !important;
}
.product-nav-item.selected {
  color: #114f86 !important;
  background: #eaf3fc !important;
  border: 0 !important;
  border-left: 3px solid #2478c5 !important;
  box-shadow: none !important;
}
.product-nav-glyph,
.product-nav-copy small { display: none !important; }
.product-nav-copy b { font-size: 13px !important; font-weight: 700 !important; line-height: 1.4 !important; }
.nav-foot {
  display: block !important;
  margin: 20px 5px 0 !important;
  padding: 11px 10px !important;
  background: #f8fafc;
  border: 1px solid #e1e8ef;
  border-radius: 6px;
}
.nav-foot > span { display: none !important; }
.nav-foot b { color: #66798c !important; font-size: 10px !important; }
.nav-foot small { color: #909daa !important; font-size: 9px !important; }

main { padding: 18px 22px 44px !important; }
.product-page-head {
  min-height: 70px !important;
  margin-bottom: 14px !important;
  padding: 0 0 13px !important;
  border-bottom: 1px solid #d6e0e9 !important;
}
.product-page-head h2 { font-size: 25px !important; }
.product-page-head p { max-width: 660px; font-size: 12px !important; }
.page-chip:not(.mode) { display: none; }

.grid { gap: 12px !important; }
.grid > .card { grid-column: span 6 !important; }
.grid > .card.metric-panel { grid-column: span 3 !important; }
.grid > .card.w4 { grid-column: 1 / -1 !important; }
.grid > .card:nth-child(-n+4) { grid-column: span 6 !important; }
.grid > .card.metric-panel:nth-child(-n+4) { grid-column: span 3 !important; }
.card {
  padding: 16px 17px !important;
  border: 1px solid #dfe6ed !important;
  border-radius: 7px !important;
  box-shadow: 0 3px 12px rgba(25, 50, 76, .035) !important;
}
.grid > .card:nth-child(-n+4),
.grid > .card.metric-panel {
  min-height: auto !important;
  border-top: 1px solid #dfe6ed !important;
}
.grid > .card.metric-panel {
  min-height: 98px !important;
  border-top: 3px solid #4b8dca !important;
}
.grid > .card:nth-child(-n+4)::before,
.grid > .card:nth-child(-n+4)::after,
.grid > .card.metric-panel::before,
.grid > .card.metric-panel::after { display: none !important; }
.panel-title { margin-bottom: 7px !important; font-size: 11px !important; }
.kpi { font-size: clamp(28px, 2.7vw, 36px) !important; }

.technical-details {
  grid-column: 1 / -1;
  overflow: hidden;
  background: #fff;
  border: 1px solid #dde5ec;
  border-radius: 7px;
}
.technical-details summary {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  min-height: 48px;
  padding: 11px 16px;
  color: #496176;
  background: #f8fafc;
  cursor: pointer;
  list-style: none;
  user-select: none;
}
.technical-details summary::-webkit-details-marker { display: none; }
.technical-details summary::after {
  content: "+";
  display: grid;
  place-items: center;
  width: 23px;
  height: 23px;
  flex: 0 0 23px;
  color: #226caf;
  background: #e8f2fb;
  border-radius: 50%;
  font-size: 16px;
  font-weight: 500;
}
.technical-details[open] summary::after { content: "−"; }
.technical-details summary b { display: block; font-size: 12px; }
.technical-details summary small { display: block; margin-top: 2px; color: #8997a5; font-size: 10px; }
.technical-details-body {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  padding: 12px;
  border-top: 1px solid #e3e9ef;
}
.technical-details-body > .card { min-height: auto !important; box-shadow: none !important; }
.technical-details-body > .card:last-child:nth-child(odd) { grid-column: 1 / -1; }

@media (max-width: 850px) and (min-width: 701px) {
  header { grid-template-columns: 185px minmax(0,1fr) auto !important; }
  .wrap { grid-template-columns: 190px minmax(0, 1fr) !important; }
  .product-nav { width: 190px !important; }
  .nav-heading { padding: 13px 11px !important; }
  .product-nav-item { padding: 9px 10px !important; }
  main { padding: 15px 14px 38px !important; }
  .grid > .card.metric-panel,
  .grid > .card.metric-panel:nth-child(-n+4) { grid-column: span 6 !important; }
}
@media (max-width: 700px) {
  header { min-height: 90px !important; }
  .product-nav { background: #0b3158 !important; }
  .product-nav-item { display: flex !important; color: #bed5e8 !important; border-left: 0 !important; }
  .product-nav-item.selected { color: #fff !important; background: #2478c5 !important; border-left: 0 !important; }
  .product-page-head { grid-template-columns: 1fr !important; }
  .technical-details-body { grid-template-columns: 1fr; }
  .technical-details-body > .card { grid-column: 1 !important; }
  .grid > .card.metric-panel,
  .grid > .card.metric-panel:nth-child(-n+4) { grid-column: 1 / -1 !important; }
}

/* Preserve the original dashboard width semantics across every route. */
.grid > .card.w1 { grid-column: span 3 !important; }
.grid > .card.w2 { grid-column: span 6 !important; }
.grid > .card.w3 { grid-column: span 9 !important; }
.grid > .card.w4,
.grid > .card.w4:nth-child(-n+4) { grid-column: 1 / -1 !important; }
.grid > .card.metric-panel:not(.w4) { grid-column: span 3 !important; }
table.compact-table {
  width: 100% !important;
  min-width: 100% !important;
  table-layout: auto !important;
}
table.wide-table { min-width: 1080px !important; }
table.compact-table th,
table.compact-table td { white-space: normal !important; }
.status-insufficient {
  color: #8a5a10 !important;
  font-weight: 720 !important;
}
.status-granted {
  color: #137152 !important;
  font-weight: 720 !important;
}
.status-empty {
  color: #758496 !important;
  font-weight: 650 !important;
}

@media (max-width: 850px) and (min-width: 701px) {
  .grid > .card.w1,
  .grid > .card.w2,
  .grid > .card.w3 { grid-column: span 6 !important; }
  .grid > .card.w4,
  .grid > .card.w4:nth-child(-n+4) { grid-column: 1 / -1 !important; }
  .grid > .card.metric-panel:not(.w4) { grid-column: span 6 !important; }
}
@media (max-width: 700px) {
  .grid > .card.w1,
  .grid > .card.w2,
  .grid > .card.w3,
  .grid > .card.w4,
  .grid > .card.w4:nth-child(-n+4),
  .grid > .card.metric-panel:not(.w4) { grid-column: 1 / -1 !important; }
}
"""


SCRIPT = r"""
<script>
(() => {
  document.documentElement.lang = 'zh-CN';
  document.body.classList.add('product-workbench');
  const exact = new Map([
    ['🎯 A股机会雷达 V8.6.6', 'A股机会雷达 V8.6.6'],
    ['Rich Workbench / Server Navigation', '真实数据研究工作台'],
    ['DEMO', '演示'], ['LIVE', '实时'], ['PIT', '历史'],
    ['● READY', '数据服务正常'], ['Meta-Label', '样本表现'],
    ['Conformal', '置信边界'], ['Monte Carlo', '风险压力测试'],
    ['Alpha 排名', '综合排名'], ['Alpha排名', '综合排名'],
    ['SAMPLE_INSUFFICIENT', '样本不足'], ['GRANTED', '已授权'],
    ['EMPTY', '暂无数据'], ['READY', '就绪'], ['OK', '正常'],
    ['NULL', '暂无'], ['PASS', '通过'], ['C', '观察级'],
    ['V8_LEGACY', 'V8历史数据'],
    ['V8_INDEPENDENT_DISCOVERY', 'V8独立发现'],
    ['V8_ACCEPTANCE', 'V8验收数据'],
    ['Meta Label', '二次筛选标签'],
    ['Alpha Rank', '综合强度排名'],
    ['Bootstrap', '重复抽样检验'],
    ['CPCV', '组合式样本外检验'],
    ['Purged CV', '去重交叉验证'],
    ['Factor Lab', '因子实验室'],
    ['D-SR', '修正夏普检验'],
    ['rank_ic', '排名相关性'],
    ['Tushare Token', '付费数据访问凭证'],
    ['REFRESH_LIVE_ANALYSIS.bat', '刷新实时分析启动文件'],
    ['trade_cal', '交易日历'],
    ['stock_basic', '股票基础资料'],
    ['daily', '日线行情'],
    ['daily_basic', '每日基础指标'],
    ['pro_bar', '复权行情'],
    ['stk_limit', '每日涨跌停价格'],
    ['limit_list_ths', '涨跌停榜单'],
    ['stk_auction_tick', '竞价逐笔数据'],
    ['stk_auction_o', '开盘竞价数据'],
    ['stk_auction_c', '收盘竞价数据'],
    ['moneyflow', '个股资金流'],
    ['moneyflow_ths', '个股资金流增强版'],
    ['moneyflow_ind_ths', '行业资金流'],
    ['share_float', '限售股解禁'],
    ['top_list', '龙虎榜'],
    ['block_trade', '大宗交易'],
    ['margin_detail', '融资融券明细'],
    ['factor_list', '因子目录'],
    ['factor_value', '因子数据'],
    ['rt_min', '实时分钟行情'],
    ['rt_min_daily', '当日分钟行情'],
    ['https://tt.dailyfetch.top/', '付费数据镜像地址']
  ]);
  const replacements = [
    ['REALTIME_WINNERS / EARLY_STARTUP / REVERSAL_REPAIR', '强势候选 / 早期启动 / 修复转强'],
    ['REALTIME_WINNERS / REVERSAL_REPAIR', '强势候选 / 修复转强'],
    ['REALTIME_WINNERS / EARLY_STARTUP', '强势候选 / 早期启动'],
    ['Tushare/dailyfetch standardized + V8 rule engine', '付费数据标准化结果 + V8规则引擎'],
    ['Tushare/dailyfetch standardized PIT snapshot', '付费数据标准化历史快照'],
    ['Deflated Sharpe / DSR', '修正夏普比率检验'],
    ['CPCV Precision >= 0.70', '组合式样本外检验准确率 ≥ 70%'],
    ['Meta概率 >= 0.85', '二次筛选概率 ≥ 85%'],
    ['T+1上涨概率 >= 0.62', '次日上涨概率 ≥ 62%'],
    ['Conformal 集合非空', '置信预测集合非空'],
    ['Primary Signal 已保存', '主信号已保存'],
    ['Top Winner Recall', '强势股覆盖率'],
    ['Singleton Positive', '单一正例'],
    ['amount_acceleration_rank, vwap_strength_rank, event_surprise_rank, persistence_rank', '成交额加速度排名、成交量加权均价强度排名、事件超预期排名、持续性排名'],
    ['data_provenance + tushare_client', '数据来源记录与付费数据客户端'],
    ['auction_tick: ERROR', '竞价过程数据：暂不可用'],
    ['EOD_CROSS_SECTION', '收盘横截面'], ['INTRADAY_PIT', '盘中真实快照'],
    ['REALTIME_WINNERS', '强势候选'], ['EARLY_STARTUP', '早期启动'],
    ['REVERSAL_REPAIR', '修复转强'], ['Meta-Label', '样本表现'],
    ['Meta Label', '二次筛选标签'], ['Meta有效样本', '有效训练样本'],
    ['Meta有效', '训练有效'], ['Meta 样本达标', '二次筛选样本达标'],
    ['Meta概率', '二次筛选概率'], ['Monte Carlo', '风险压力测试'],
    ['CPCV 样本外通过', '组合式样本外检验通过'],
    ['CPCV 评估', '组合式样本外评估'], ['CPCV验证', '组合式样本外验证'],
    ['CPCV', '组合式样本外检验'],
    ['Conformal 校准完成', '置信边界校准完成'],
    ['Conformal 预测集合', '置信预测集合'],
    ['Conformal', '置信边界'],
    ['Bootstrap置信区间', '重复抽样置信区间'],
    ['Bootstrap', '重复抽样检验'],
    ['Purged CV', '去重交叉验证'],
    ['Deflated Sharpe', '修正夏普比率'], ['DSR', '稳健性检验'],
    ['Precision', '准确率'], ['Recall', '覆盖率'],
    ['Brier', '概率误差'], ['PSI漂移', '分布漂移'], ['PSI', '分布漂移指数'],
    ['FDR', '错误发现率'], ['Sniper 评分', '狙击评分'],
    ['Alpha Rank', '综合强度排名'], ['Alpha排名', '综合排名'],
    ['Factor Lab', '因子实验室'], ['Primary Signal', '主信号'],
    ['Challenger', '候选模型'], ['Champion', '主模型'],
    ['LightGBM优先', '优先使用梯度提升树模型'],
    ['abstain', '暂缓判断'], ['AI应用', '人工智能应用'],
    ['CPO/光模块', '共封装光学/光模块'],
    ['V8_INDEPENDENT_DISCOVERY', 'V8独立发现'],
    ['V8_LEGACY', 'V8历史数据'],
    ['LIVE：READY', '实时数据：就绪'],
    ['DEMO百分比', '演示数据百分比'],
    ['PIT安全且带T+1', '决策时点安全且带次日结果'],
    ['PIT安全', '决策时点安全'], ['PIT记录', '历史实况记录'],
    ['PIT 历史样本', '决策时点历史样本'], ['PIT分钟快照', '决策时点分钟快照'],
    ['PIT 快照', '历史实况快照'], ['PIT：可用', '历史快照：可用'],
    ['PIT', '历史实况'],
    ['T+1标签', '次日结果标签'], ['带T+1', '带次日结果'],
    ['T+1收益', '次日收益'], ['T+1规则估计', '次日规则估计'],
    ['T+1', '次日'], ['DEMO', '演示数据'], ['LIVE', '实时数据'],
    ['source：', '数据来源：'], ['source:', '数据来源：'],
    ['observed_at：', '观测时间：'], ['observed_at:', '观测时间：'],
    ['data_quality：', '数据质量：'], ['Token：', '访问凭证：'],
    ['Token:', '访问凭证：'], ['镜像：OK', '镜像：正常'], ['镜像: OK', '镜像：正常'],
    ['standardized', '标准化'], ['rule engine', '规则引擎'],
    ['NO_PERMISSION', '未授权'], ['SAMPLE_INSUFFICIENT', '样本不足'],
    ['GRANTED', '已授权'], ['EMPTY', '暂无数据'],
    ['ERROR', '异常'], ['PASS', '通过'], ['READY', '就绪'],
    ['NULL', '暂无'], ['OFF', '关闭'], ['Top ', '前 ']
  ];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const textNodes = [];
  while (walker.nextNode()) textNodes.push(walker.currentNode);
  textNodes.forEach((node) => {
    if (!node.parentElement || ['SCRIPT', 'STYLE', 'PRE'].includes(node.parentElement.tagName)) return;
    const raw = node.nodeValue;
    const trimmed = raw.trim();
    if (!trimmed) return;
    if (exact.has(trimmed)) { node.nodeValue = raw.replace(trimmed, exact.get(trimmed)); return; }
    let value = raw;
    replacements.forEach(([from, to]) => { value = value.split(from).join(to); });
    value = value.replace(/(\d+(?:\.\d+)?)\s*ms\b/g, '$1 毫秒');
    value = value.replace(/(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/g, '$1 $2');
    value = value.replace(/(\d{6})\.SZ/g, '$1 · 深市');
    value = value.replace(/(\d{6})\.SH/g, '$1 · 沪市');
    value = value.replace(/(\d{6})\.BJ/g, '$1 · 北交所');
    node.nodeValue = value;
  });

  document.querySelectorAll('tr').forEach((row) => {
    const cells = Array.from(row.querySelectorAll('td'));
    const statusCell = cells.find((cell) => cell.textContent.trim() === '样本不足');
    if (statusCell) {
      const ratioCell = cells.find((cell) => /\d+\s*\/\s*\d+/.test(cell.textContent));
      const match = ratioCell && ratioCell.textContent.match(/(\d+)\s*\/\s*(\d+)/);
      if (match) {
        const current = Number(match[1]);
        const required = Number(match[2]);
        statusCell.textContent = `样本不足（还差 ${Math.max(required - current, 0)} 条）`;
      } else {
        statusCell.textContent = '样本不足（暂不生成结果）';
      }
      statusCell.classList.add('status-insufficient');
    }
    cells.filter((cell) => cell.textContent.trim() === '已授权').forEach((cell) => cell.classList.add('status-granted'));
    cells.filter((cell) => cell.textContent.trim() === '暂无数据').forEach((cell) => cell.classList.add('status-empty'));
  });

  const pageCopy = {
    overview: ['今日总览', '确认数据可信度，查看今日机会，再决定是否进入执行检查。'],
    a_exec: ['A级执行', '只看通过执行门槛的信号，并核对资金、竞价、筹码、支撑压力和风险。'],
    sniper: ['A+狙击', '查看最高等级观察对象及每项门槛，通过不足时不执行。'],
    meta: ['样本表现', '用真实结果评估历史信号，样本不足时不生成真实胜率。'],
    conformal: ['置信边界', '查看预测可能波动的范围，避免把单一概率理解为确定结果。'],
    cpcv: ['样本外检验', '判断规则在不同历史阶段是否稳定，并检查过拟合风险。'],
    factor: ['因子分析', '拆解资金、竞价、筹码和趋势等证据的实际贡献。'],
    limitup: ['连板观察', '观察涨停生态与板块扩散，只用于发现，不等同于追涨建议。'],
    sim: ['执行模拟', '评估滑点、成交概率和执行成本，不连接自动交易。'],
    mc: ['风险压力', '观察极端情景下的回撤和承受能力。'],
    drift: ['稳定性监控', '检查数据质量与历史效果是否发生变化。'],
    health: ['数据健康', '查看完整度、时效、异常字段与历史快照安全状态。'],
    sources: ['数据源与权限', '查看真实接口配置、授权、延迟、返回数据和最近错误。']
  };
  const params = new URLSearchParams(location.search);
  const page = params.get('page') || 'overview';
  const mode = (params.get('mode') || 'DEMO').toUpperCase();
  const modeName = { DEMO: '演示数据', LIVE: '实时数据', PIT: '历史实况' }[mode] || '演示数据';
  const [title, description] = pageCopy[page] || pageCopy.overview;
  document.title = `${title}｜A股机会雷达`;
  const main = document.querySelector('main');
  if (main) {
    const head = document.createElement('section');
    head.className = 'product-page-head';
    const action = page === 'overview' ? `<a class="primary-action" href="/?page=a_exec&mode=${mode}">进入A级执行</a>` : '';
    head.innerHTML = `<div><span class="eyebrow">A股机会雷达 / ${modeName}</span><h2>${title}</h2><p>${description}</p></div><div class="page-actions"><span class="page-chip mode">${modeName}</span><span class="page-chip">仅验证展示</span><span class="page-chip">不自动交易</span>${action}</div>`;
    main.prepend(head);
  }

  document.querySelectorAll('.grid').forEach((grid) => {
    Array.from(grid.children).filter((item) => item.classList.contains('card')).forEach((card, index) => {
      if (card.querySelector('.kpi')) card.classList.add('metric-panel');
      if ((card.textContent || '').includes('警示')) card.classList.add('alert-panel');
    });
  });
  if (!['health', 'sources'].includes(page)) {
    const technicalNames = ['数据血缘', '安全状态', '数据边界'];
    const technicalCards = Array.from(document.querySelectorAll('.grid > .card')).filter((card) => {
      const title = card.querySelector('.panel-title');
      return title && technicalNames.some((name) => title.textContent.includes(name));
    });
    if (technicalCards.length) {
      const firstCard = technicalCards[0];
      const parent = firstCard.parentElement;
      const details = document.createElement('details');
      details.className = 'technical-details';
      details.innerHTML = '<summary><span><b>数据来源与安全说明</b><small>需要核对数据时间、来源和限制时再展开</small></span></summary><div class="technical-details-body"></div>';
      parent.insertBefore(details, firstCard);
      const detailBody = details.querySelector('.technical-details-body');
      technicalCards.forEach((card) => detailBody.appendChild(card));
    }
  }
  document.querySelectorAll('table').forEach((table) => {
    if (!table.parentElement.classList.contains('table-scroll')) {
      const shell = document.createElement('div');
      shell.className = 'product-table-shell';
      table.parentNode.insertBefore(shell, table);
      shell.appendChild(table);
    }
    const headers = Array.from(table.querySelectorAll('thead th, tr:first-child th'));
    table.classList.add(headers.length > 6 ? 'wide-table' : 'compact-table');
    const changeIndex = headers.findIndex((header) => header.textContent.includes('涨跌'));
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
    html = _original_render_page(*args, **kwargs)
    if "</style>" in html:
        html = html.replace("</style>", CSS + "</style>", 1)
    else:
        html = html.replace("</head>", "<style>" + CSS + "</style></head>", 1)
    html = html.replace("</body>", SCRIPT + "</body>", 1)
    return html


core._render_nav = render_navigation
core.render_page = render_page


def main():
    host = os.environ.get("RADAR_HOST", "127.0.0.1")
    port = int(os.environ.get("RADAR_PORT", "8791"))
    server = ThreadingHTTPServer((host, port), core.H)
    print("A股机会雷达 V8.6.6 产品工作台")
    print("http://%s:%s/?page=overview&mode=LIVE" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
