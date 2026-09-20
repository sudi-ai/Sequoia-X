"""V8.6.6 Chinese decision workbench skin.

This module deliberately leaves the original dashboard, navigation routes, data
queries and strategy calculations untouched.  It replaces only presentation and
wording, then serves the original request handler on the same port.
"""

from html import escape
from http.server import ThreadingHTTPServer
import os

import browser_dashboard as core


_original_render_page = core.render_page


NAV_GROUPS = (
    (
        "今日决策",
        (
            ("overview", "今日总览", "市场、数据与候选"),
            ("a_exec", "A级执行", "严格门槛后的行动清单"),
            ("sniper", "A+狙击", "更高要求的观察区"),
            ("limitup", "连板观察", "涨停与板块扩散"),
        ),
    ),
    (
        "研究验证",
        (
            ("meta", "样本表现", "真实结果是否支持信号"),
            ("conformal", "置信边界", "结果有多大把握"),
            ("cpcv", "样本外检验", "防止回测过度乐观"),
            ("factor", "因子分析", "拆解有效与无效因素"),
            ("sim", "执行模拟", "滑点与可成交性"),
            ("mc", "风险压力", "极端情景承受能力"),
            ("drift", "稳定性监控", "模型与数据是否变质"),
        ),
    ),
    (
        "数据与系统",
        (
            ("health", "数据健康", "完整度、时效与异常"),
            ("sources", "数据源与权限", "付费接口运行状态"),
        ),
    ),
)


def _render_cn_nav(page_key, mode="DEMO"):
    safe_mode = mode if mode in {"DEMO", "LIVE", "PIT"} else "DEMO"
    chunks = [
        "<nav class='cn-nav' aria-label='工作台主导航'>",
        "<div class='nav-brand'>",
        "<span class='brand-mark'>V8</span>",
        "<span><b>A股机会雷达</b><small>专业选股决策工作台</small></span>",
        "</div>",
    ]
    number = 1
    for group_name, items in NAV_GROUPS:
        chunks.append("<p class='nav-section'>%s</p>" % escape(group_name))
        for key, label, helper in items:
            selected = " sel" if key == page_key else ""
            chunks.append(
                "<a class='nav-item%s' href='/?page=%s&amp;mode=%s' title='%s'>"
                "<span class='nav-index'>%02d</span>"
                "<span class='nav-copy'><b>%s</b><small>%s</small></span>"
                "</a>"
                % (
                    selected,
                    escape(key),
                    escape(safe_mode),
                    escape(helper),
                    number,
                    escape(label),
                    escape(helper),
                )
            )
            number += 1
    chunks.extend(
        (
            "<div class='nav-safety'>",
            "<b>当前仅验证与展示</b>",
            "<span>不自动交易，不用演示数据冒充真实结论。</span>",
            "</div>",
            "</nav>",
        )
    )
    return "".join(chunks)


WORKBENCH_CSS = r"""

/* V8.6.6 Chinese Decision Workbench */
:root {
  --canvas: #f2f5f0;
  --canvas-warm: #f8f4ec;
  --surface: #ffffff;
  --surface-soft: #f7faf7;
  --ink: #18241f;
  --ink-soft: #425149;
  --muted: #708078;
  --brand: #165d4f;
  --brand-deep: #0d4339;
  --brand-pale: #e9f2ee;
  --accent: #b64832;
  --accent-pale: #fbede8;
  --gold: #a97220;
  --gold-pale: #fbf3df;
  --line: #dce5df;
  --line-strong: #cbd8d0;
  --shadow: 0 16px 40px rgba(25, 55, 43, .08);
}

* { box-sizing: border-box; }
html { background: var(--canvas); }
body {
  margin: 0 !important;
  color: var(--ink) !important;
  background:
    radial-gradient(circle at 82% -8%, rgba(181, 144, 73, .13), transparent 30rem),
    radial-gradient(circle at 8% 92%, rgba(22, 93, 79, .09), transparent 34rem),
    var(--canvas) !important;
  font-family: "Microsoft YaHei UI", "Source Han Sans SC", "PingFang SC", sans-serif !important;
  font-size: 15px !important;
  line-height: 1.65 !important;
  -webkit-font-smoothing: antialiased;
}

header {
  position: sticky !important;
  top: 0;
  z-index: 50;
  min-height: 86px !important;
  padding: 15px 26px !important;
  display: grid !important;
  grid-template-columns: auto 1fr auto !important;
  grid-template-rows: auto auto !important;
  align-items: center !important;
  column-gap: 24px !important;
  color: var(--ink) !important;
  background: rgba(255, 255, 255, .92) !important;
  border-bottom: 1px solid var(--line) !important;
  box-shadow: 0 6px 22px rgba(25, 55, 43, .05) !important;
  backdrop-filter: blur(18px);
}
header h1 {
  grid-column: 1;
  grid-row: 1;
  margin: 0 !important;
  color: var(--brand-deep) !important;
  font-size: 22px !important;
  font-weight: 800 !important;
  letter-spacing: -.02em;
}
header .sub {
  grid-column: 1;
  grid-row: 2;
  color: var(--muted) !important;
  font-size: 12px !important;
  letter-spacing: .08em;
}
header .mode-zone {
  grid-column: 2;
  grid-row: 1 / 3;
  justify-self: end;
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  min-width: 0;
}
header .ok {
  grid-column: 3;
  grid-row: 1 / 3;
  color: var(--brand) !important;
  background: var(--brand-pale);
  border: 1px solid #cfe1d9;
  border-radius: 999px;
  padding: 6px 11px;
  font-size: 12px !important;
  white-space: nowrap;
}
.mode-controls {
  display: inline-flex !important;
  gap: 3px !important;
  padding: 3px !important;
  background: #eef2ef !important;
  border: 1px solid var(--line) !important;
  border-radius: 11px !important;
}
.mode-link {
  min-width: 48px;
  padding: 5px 10px !important;
  border: 0 !important;
  border-radius: 8px !important;
  color: var(--muted) !important;
  background: transparent !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  text-align: center;
  text-decoration: none !important;
}
.mode-link.active {
  color: #fff !important;
  background: var(--brand) !important;
  box-shadow: 0 3px 10px rgba(22, 93, 79, .18);
}
.mode-pill {
  padding: 4px 9px !important;
  border: 1px solid var(--brand) !important;
  border-radius: 999px !important;
  color: var(--brand) !important;
  background: var(--brand-pale) !important;
  font-size: 11px !important;
  font-weight: 800 !important;
}
.source-line {
  max-width: 420px;
  overflow: hidden;
  color: var(--muted) !important;
  font-size: 12px !important;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wrap {
  display: grid !important;
  grid-template-columns: 258px minmax(0, 1fr) !important;
  align-items: start !important;
  min-height: calc(100vh - 86px);
}
.cn-nav {
  position: sticky !important;
  top: 86px !important;
  height: calc(100vh - 86px) !important;
  padding: 18px 14px 24px !important;
  overflow-x: hidden !important;
  overflow-y: auto !important;
  color: var(--ink) !important;
  background: rgba(250, 252, 249, .88) !important;
  border-right: 1px solid var(--line) !important;
  scrollbar-width: thin;
}
.nav-brand {
  display: flex;
  align-items: center;
  gap: 11px;
  margin: 0 5px 18px;
  padding: 12px;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 15px;
  box-shadow: 0 8px 24px rgba(25, 55, 43, .06);
}
.brand-mark {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  color: #fff;
  background: linear-gradient(145deg, var(--brand), var(--brand-deep));
  border-radius: 12px;
  font-family: Georgia, serif;
  font-size: 15px;
  font-weight: 800;
}
.nav-brand b, .nav-brand small { display: block; }
.nav-brand b { font-size: 14px; }
.nav-brand small { margin-top: 2px; color: var(--muted); font-size: 10px; }
.nav-section {
  margin: 18px 12px 7px !important;
  color: #8a978f !important;
  font-size: 10px !important;
  font-weight: 800 !important;
  letter-spacing: .16em;
}
.cn-nav .nav-item {
  display: flex !important;
  align-items: center !important;
  gap: 10px !important;
  margin: 3px 0 !important;
  padding: 8px 10px !important;
  color: var(--ink-soft) !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 12px !important;
  text-decoration: none !important;
  transition: background .18s ease, color .18s ease, transform .18s ease;
}
.cn-nav .nav-item:hover {
  color: var(--brand-deep) !important;
  background: #f0f5f2 !important;
  transform: translateX(2px);
}
.cn-nav .nav-item.sel {
  color: var(--brand-deep) !important;
  background: var(--brand-pale) !important;
  border-color: #cfe1d9 !important;
  box-shadow: inset 3px 0 0 var(--brand);
}
.nav-index {
  flex: 0 0 27px;
  color: #9aa69f;
  font-family: Georgia, serif;
  font-size: 11px;
}
.nav-item.sel .nav-index { color: var(--brand); }
.nav-copy { min-width: 0; }
.nav-copy b, .nav-copy small { display: block; }
.nav-copy b { font-size: 13px; font-weight: 750; }
.nav-copy small {
  margin-top: 1px;
  overflow: hidden;
  color: var(--muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-safety {
  margin: 20px 5px 0;
  padding: 13px;
  color: #795021;
  background: var(--gold-pale);
  border: 1px solid #ecd8a9;
  border-radius: 13px;
}
.nav-safety b, .nav-safety span { display: block; }
.nav-safety b { margin-bottom: 4px; font-size: 11px; }
.nav-safety span { font-size: 10px; line-height: 1.55; }

main {
  width: 100% !important;
  min-width: 0 !important;
  max-width: 1640px;
  margin: 0 auto !important;
  padding: 26px 30px 56px !important;
  color: var(--ink) !important;
}
.page-intro {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin: 0 0 18px;
  padding: 24px 26px;
  overflow: hidden;
  color: #fff;
  background:
    linear-gradient(118deg, rgba(13, 67, 57, .98), rgba(22, 93, 79, .91)),
    var(--brand-deep);
  border-radius: 20px;
  box-shadow: 0 18px 40px rgba(13, 67, 57, .16);
}
.page-intro::after {
  content: "";
  position: absolute;
  right: -55px;
  top: -75px;
  width: 230px;
  height: 230px;
  border: 1px solid rgba(255, 255, 255, .15);
  border-radius: 50%;
  box-shadow: 0 0 0 36px rgba(255, 255, 255, .04), 0 0 0 72px rgba(255, 255, 255, .025);
}
.page-intro-copy { position: relative; z-index: 1; max-width: 760px; }
.page-kicker {
  display: block;
  margin-bottom: 4px;
  color: #bcd8cd;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: .16em;
}
.page-intro h2 {
  margin: 0 0 5px !important;
  color: #fff !important;
  font-size: clamp(24px, 3vw, 34px) !important;
  line-height: 1.25 !important;
  letter-spacing: -.03em;
}
.page-intro p { margin: 0 !important; color: #dcebe5 !important; font-size: 14px !important; }
.intro-badges {
  position: relative;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 7px;
}
.intro-badge {
  padding: 6px 10px;
  color: #edf7f3;
  background: rgba(255, 255, 255, .1);
  border: 1px solid rgba(255, 255, 255, .2);
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}
.intro-badge.mode { color: #3f2a10; background: #f5ddb0; border-color: #f5ddb0; }
.reading-path {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: -3px 0 18px;
}
.reading-step {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  padding: 12px 15px;
  color: var(--ink-soft);
  background: rgba(255, 255, 255, .72);
  border: 1px solid var(--line);
  border-radius: 14px;
}
.reading-step b {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  color: var(--brand);
  background: var(--brand-pale);
  border-radius: 50%;
  font-family: Georgia, serif;
  font-size: 12px;
}
.reading-step span { font-size: 12px; }

.grid {
  display: grid !important;
  grid-template-columns: repeat(12, minmax(0, 1fr)) !important;
  gap: 16px !important;
}
.grid > .card { grid-column: span 3 !important; }
.grid > .card.w4 { grid-column: 1 / -1 !important; }
.card {
  min-width: 0;
  padding: 19px 20px !important;
  color: var(--ink-soft) !important;
  background: rgba(255, 255, 255, .93) !important;
  border: 1px solid var(--line) !important;
  border-radius: 17px !important;
  box-shadow: 0 8px 26px rgba(25, 55, 43, .055) !important;
}
.grid > .card:nth-child(-n+4) {
  position: relative;
  overflow: hidden;
  min-height: 148px;
}
.grid > .card:nth-child(-n+4)::after {
  content: "";
  position: absolute;
  right: -24px;
  bottom: -38px;
  width: 100px;
  height: 100px;
  background: var(--brand-pale);
  border-radius: 50%;
  opacity: .7;
}
.panel-title {
  margin: 0 0 9px !important;
  color: var(--muted) !important;
  font-size: 12px !important;
  font-weight: 750 !important;
  letter-spacing: .05em;
}
.kpi {
  position: relative;
  z-index: 1;
  margin: 0 0 4px !important;
  color: var(--ink) !important;
  font-family: Georgia, "Microsoft YaHei UI", serif !important;
  font-size: clamp(28px, 3vw, 42px) !important;
  font-weight: 700 !important;
  line-height: 1.15 !important;
  letter-spacing: -.03em;
}
.card p { color: var(--ink-soft) !important; }
.card ul { margin: 8px 0 0; padding-left: 20px; }
.card li { margin: 5px 0; }
.card:has(.panel-title:first-child):has(li) {
  background: linear-gradient(110deg, #fffaf1, #fff) !important;
  border-color: #ead8b5 !important;
}
.warn {
  margin: 8px 0 14px !important;
  padding: 10px 12px !important;
  color: #7d4c14 !important;
  background: var(--gold-pale) !important;
  border-left: 3px solid var(--gold) !important;
  border-radius: 4px 10px 10px 4px !important;
  font-size: 12px !important;
  font-weight: 650 !important;
}
.ok { color: var(--brand) !important; }
.bad, .err { color: var(--accent) !important; }

.table-scroll,
.table-shell {
  position: relative;
  width: 100%;
  overflow-x: auto !important;
  border: 1px solid var(--line) !important;
  border-radius: 13px !important;
  scrollbar-color: #a9bbb1 transparent;
}
table {
  width: 100% !important;
  min-width: 1120px !important;
  border-collapse: separate !important;
  border-spacing: 0 !important;
  color: var(--ink-soft) !important;
  background: var(--surface) !important;
  font-size: 12px !important;
}
thead th, table th {
  position: sticky;
  top: 0;
  z-index: 3;
  padding: 11px 10px !important;
  color: #55665d !important;
  background: #eef3ef !important;
  border-bottom: 1px solid var(--line-strong) !important;
  font-size: 11px !important;
  font-weight: 800 !important;
  line-height: 1.35 !important;
  text-align: left;
  white-space: normal;
}
tbody td, table td {
  padding: 11px 10px !important;
  color: var(--ink-soft) !important;
  background: #fff !important;
  border-bottom: 1px solid #e7ede9 !important;
  line-height: 1.5 !important;
  vertical-align: middle !important;
}
tbody tr:nth-child(even) td { background: #fbfcfb !important; }
tbody tr:hover td { background: #f2f7f4 !important; }
table th:first-child, table td:first-child {
  position: sticky;
  left: 0;
  z-index: 2;
  min-width: 86px;
  box-shadow: 8px 0 14px rgba(30, 55, 44, .035);
}
table th:first-child { z-index: 4; }
table a {
  color: var(--brand) !important;
  font-weight: 750 !important;
  text-decoration: none !important;
}
table a:hover { color: var(--accent) !important; text-decoration: underline !important; }
code, pre {
  color: var(--ink) !important;
  background: #eef3ef !important;
  border-color: var(--line) !important;
}

@keyframes workbench-rise {
  from { opacity: 0; transform: translateY(7px); }
  to { opacity: 1; transform: translateY(0); }
}
.page-intro, .reading-path, .grid > .card {
  animation: workbench-rise .35s ease both;
}
.grid > .card:nth-child(2) { animation-delay: .04s; }
.grid > .card:nth-child(3) { animation-delay: .08s; }
.grid > .card:nth-child(4) { animation-delay: .12s; }

@media (max-width: 1280px) {
  header .source-line { max-width: 270px; }
  .grid > .card { grid-column: span 6 !important; }
  .grid > .card.w4 { grid-column: 1 / -1 !important; }
}

@media (max-width: 920px) {
  header {
    position: relative !important;
    display: flex !important;
    align-items: flex-start !important;
    gap: 5px 12px !important;
    padding: 14px 16px !important;
  }
  header h1 { width: calc(100% - 100px); font-size: 19px !important; }
  header .sub { width: calc(100% - 100px); }
  header .ok { position: absolute; right: 15px; top: 16px; }
  header .mode-zone { width: 100%; margin-top: 8px; justify-content: flex-start; flex-wrap: wrap; }
  header .source-line { max-width: 100%; width: 100%; }
  .wrap { display: block !important; }
  .cn-nav {
    position: sticky !important;
    top: 0 !important;
    z-index: 40;
    display: flex !important;
    align-items: center !important;
    gap: 6px !important;
    width: 100% !important;
    height: auto !important;
    padding: 8px 10px !important;
    overflow-x: auto !important;
    overflow-y: hidden !important;
    border-right: 0 !important;
    border-bottom: 1px solid var(--line) !important;
  }
  .nav-brand, .nav-section, .nav-safety { display: none !important; }
  .cn-nav .nav-item { flex: 0 0 auto; margin: 0 !important; padding: 7px 10px !important; }
  .nav-index, .nav-copy small { display: none !important; }
  main { padding: 17px 14px 40px !important; }
  .page-intro { align-items: flex-start; flex-direction: column; padding: 20px; }
  .intro-badges { justify-content: flex-start; }
  .reading-path { grid-template-columns: 1fr; gap: 7px; }
  .grid > .card { grid-column: 1 / -1 !important; }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}

/* Premium Blue Edition: calm financial workstation, not a neon terminal. */
:root {
  --canvas: #eef3f8;
  --canvas-warm: #f4f7fb;
  --surface: #ffffff;
  --surface-soft: #f7f9fc;
  --ink: #10243e;
  --ink-soft: #3e516b;
  --muted: #718198;
  --brand: #175fc4;
  --brand-deep: #09294c;
  --brand-pale: #e9f2ff;
  --accent: #c64b3c;
  --accent-pale: #fcedea;
  --gold: #a97825;
  --gold-pale: #fbf3e2;
  --line: #d9e3ef;
  --line-strong: #c8d5e5;
  --shadow: 0 18px 45px rgba(20, 55, 96, .09);
}

html { background: var(--canvas); }
body {
  background:
    radial-gradient(circle at 86% -12%, rgba(35, 111, 207, .13), transparent 31rem),
    linear-gradient(rgba(255, 255, 255, .18) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, .18) 1px, transparent 1px),
    var(--canvas) !important;
  background-size: auto, 32px 32px, 32px 32px, auto !important;
}

header {
  background: rgba(250, 252, 255, .94) !important;
  border-bottom-color: #d4dfeb !important;
  box-shadow: 0 7px 25px rgba(20, 55, 96, .07) !important;
}
header h1 { color: #0a2b50 !important; }
header .ok {
  color: #1455a7 !important;
  background: #eaf2fd !important;
  border-color: #c9daee !important;
}
.mode-controls {
  background: #e9eef5 !important;
  border-color: #d6e0eb !important;
}
.mode-link.active {
  background: linear-gradient(135deg, #1454a9, #1f6fd3) !important;
  box-shadow: 0 5px 14px rgba(23, 95, 196, .22) !important;
}
.mode-pill {
  color: #1759ad !important;
  background: #eaf2fd !important;
  border-color: #9ebce0 !important;
}

.cn-nav {
  background: rgba(247, 250, 253, .93) !important;
  border-right-color: #d6e1ed !important;
}
.nav-brand {
  border-color: #d7e2ee !important;
  box-shadow: 0 9px 26px rgba(20, 55, 96, .075) !important;
}
.brand-mark {
  background: linear-gradient(145deg, #1d6bcd, #08284a) !important;
  box-shadow: 0 7px 16px rgba(18, 79, 151, .22);
}
.cn-nav .nav-item:hover {
  color: #0b3768 !important;
  background: #edf3fa !important;
}
.cn-nav .nav-item.sel {
  color: #0a376d !important;
  background: linear-gradient(90deg, #e4effd, #eef5fd) !important;
  border-color: #c7daef !important;
  box-shadow: inset 3px 0 0 #1c66c8 !important;
}
.nav-item.sel .nav-index { color: #175fc4 !important; }
.nav-safety {
  color: #67491c !important;
  background: #fbf4e5 !important;
  border-color: #ead7ae !important;
}

.page-intro {
  background:
    linear-gradient(115deg, rgba(7, 31, 59, .99), rgba(13, 65, 120, .97) 58%, rgba(27, 104, 190, .93)),
    #09294c !important;
  border: 1px solid rgba(126, 174, 224, .22);
  border-radius: 16px !important;
  box-shadow: 0 22px 48px rgba(9, 41, 76, .19) !important;
}
.page-intro::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, .045) 1px, transparent 1px),
    linear-gradient(rgba(255, 255, 255, .035) 1px, transparent 1px);
  background-size: 36px 36px;
  mask-image: linear-gradient(90deg, transparent 42%, #000);
}
.page-intro::after {
  border-color: rgba(132, 187, 241, .19) !important;
  box-shadow: 0 0 0 36px rgba(86, 154, 222, .055), 0 0 0 72px rgba(86, 154, 222, .03) !important;
}
.page-kicker { color: #9fc6ec !important; }
.page-intro p { color: #d8e8f7 !important; }
.intro-badge.mode {
  color: #09294c !important;
  background: #d7e9fb !important;
  border-color: #d7e9fb !important;
}

.reading-step {
  background: rgba(255, 255, 255, .8) !important;
  border-color: #d6e1ed !important;
  box-shadow: 0 6px 18px rgba(20, 55, 96, .035);
}
.reading-step b {
  color: #175fc4 !important;
  background: #e8f1fd !important;
}

.card {
  border-color: #d9e3ef !important;
  border-radius: 14px !important;
  box-shadow: 0 9px 28px rgba(20, 55, 96, .06) !important;
}
.grid > .card:nth-child(-n+4) {
  min-height: 142px;
  border-top: 3px solid #2a70c8 !important;
}
.grid > .card:nth-child(-n+4)::before {
  content: "";
  position: absolute;
  right: 18px;
  top: 18px;
  width: 38px;
  height: 4px;
  background: linear-gradient(90deg, #7daee6, #1b65bd);
  border-radius: 999px;
  opacity: .65;
}
.grid > .card:nth-child(-n+4)::after {
  right: -20px !important;
  bottom: -48px !important;
  width: 125px !important;
  height: 125px !important;
  background: radial-gradient(circle, rgba(39, 112, 199, .1), rgba(39, 112, 199, 0) 68%) !important;
  border-radius: 50% !important;
}
.kpi { color: #17324f !important; }
.card:has(.panel-title:first-child):has(li) {
  background: linear-gradient(110deg, #fffaf2, #fff) !important;
  border-color: #ead8b6 !important;
}
.warn {
  color: #6d4a19 !important;
  background: #fbf4e5 !important;
  border-left-color: #a97825 !important;
}
.ok { color: #1760b7 !important; }

.table-scroll, .table-shell { border-color: #d4dfeb !important; }
table { color: #3e516b !important; }
thead th, table th {
  color: #415873 !important;
  background: #eaf0f7 !important;
  border-bottom-color: #c8d5e5 !important;
}
tbody td, table td {
  color: #344b65 !important;
  border-bottom-color: #e4ebf3 !important;
}
tbody tr:nth-child(even) td { background: #fafcff !important; }
tbody tr:hover td { background: #edf5ff !important; }
table a { color: #155fc3 !important; }
table a:hover { color: #0b4389 !important; }
code, pre { background: #edf2f8 !important; border-color: #d5e0ec !important; }

/* Typography and permanent workstation rail. */
body {
  font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", "Source Han Sans SC", sans-serif !important;
  font-variant-numeric: tabular-nums;
  letter-spacing: .005em;
}
header h1,
.page-intro h2,
.nav-brand b,
.nav-copy b,
.panel-title {
  font-family: "Segoe UI Variable Display", "Microsoft YaHei UI", "Source Han Sans SC", sans-serif !important;
}
header h1 {
  font-size: 23px !important;
  font-weight: 760 !important;
  letter-spacing: -.035em !important;
}
header .sub {
  font-size: 11px !important;
  font-weight: 650 !important;
  letter-spacing: .12em !important;
}
.page-intro h2 {
  font-weight: 780 !important;
  letter-spacing: -.045em !important;
}
.page-intro p {
  max-width: 760px;
  font-size: 14px !important;
  font-weight: 450 !important;
  letter-spacing: .01em;
}
.kpi {
  font-family: Bahnschrift, "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif !important;
  font-size: clamp(31px, 3.2vw, 44px) !important;
  font-weight: 620 !important;
  letter-spacing: -.045em !important;
}
.panel-title {
  color: #526781 !important;
  font-size: 12px !important;
  font-weight: 720 !important;
  letter-spacing: .035em !important;
}
table {
  font-family: "Segoe UI Variable Text", "Microsoft YaHei UI", "Source Han Sans SC", sans-serif !important;
  font-size: 12.5px !important;
}
thead th, table th {
  font-size: 11.5px !important;
  font-weight: 720 !important;
  letter-spacing: .025em;
}
tbody td, table td { font-weight: 470 !important; }

.wrap { grid-template-columns: 270px minmax(0, 1fr) !important; }
.cn-nav {
  width: 270px;
  padding: 20px 15px 26px !important;
  color: #d9e6f5 !important;
  background:
    radial-gradient(circle at 10% 2%, rgba(47, 126, 219, .22), transparent 17rem),
    linear-gradient(180deg, #0a294b 0%, #071d35 58%, #06182c 100%) !important;
  border-right: 1px solid #173c62 !important;
  box-shadow: 12px 0 34px rgba(8, 35, 66, .1);
  scrollbar-color: #365b80 transparent;
}
.nav-brand {
  gap: 13px !important;
  margin: 0 2px 24px !important;
  padding: 15px 13px !important;
  color: #fff !important;
  background: linear-gradient(135deg, rgba(34, 108, 191, .34), rgba(255, 255, 255, .055)) !important;
  border: 1px solid rgba(143, 191, 238, .2) !important;
  border-radius: 13px !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .08), 0 14px 30px rgba(0, 12, 28, .18) !important;
}
.brand-mark {
  width: 46px !important;
  height: 46px !important;
  flex-basis: 46px !important;
  font-family: Bahnschrift, "Segoe UI Variable Display", sans-serif !important;
  font-size: 15px !important;
  letter-spacing: -.02em;
  background: linear-gradient(145deg, #388be7, #17549a) !important;
  border: 1px solid rgba(255, 255, 255, .24);
  box-shadow: 0 9px 20px rgba(0, 15, 38, .28) !important;
}
.nav-brand b {
  color: #fff !important;
  font-size: 16px !important;
  font-weight: 760 !important;
  letter-spacing: -.025em;
}
.nav-brand small {
  margin-top: 3px !important;
  color: #9fc2e5 !important;
  font-size: 10px !important;
  font-weight: 650;
  letter-spacing: .035em;
}
.nav-section {
  margin: 21px 12px 8px !important;
  color: #6f94b9 !important;
  font-size: 10px !important;
  font-weight: 750 !important;
}
.cn-nav .nav-item {
  gap: 11px !important;
  min-height: 48px;
  padding: 8px 11px !important;
  color: #b9cce0 !important;
  border-radius: 10px !important;
}
.cn-nav .nav-item:hover {
  color: #fff !important;
  background: rgba(88, 150, 216, .13) !important;
  border-color: rgba(130, 181, 231, .1) !important;
}
.cn-nav .nav-item.sel {
  color: #fff !important;
  background: linear-gradient(90deg, rgba(38, 117, 205, .48), rgba(42, 112, 190, .2)) !important;
  border-color: rgba(128, 181, 235, .28) !important;
  box-shadow: inset 3px 0 0 #66aaf2, 0 7px 18px rgba(0, 14, 33, .14) !important;
}
.nav-index {
  color: #587da3 !important;
  font-family: Bahnschrift, "Segoe UI Variable Text", sans-serif !important;
  font-size: 10px !important;
  font-weight: 650;
}
.nav-item.sel .nav-index { color: #91c5fa !important; }
.nav-copy b {
  color: inherit !important;
  font-size: 13px !important;
  font-weight: 680 !important;
}
.nav-copy small {
  color: #7899ba !important;
  font-size: 9.5px !important;
}
.nav-item.sel .nav-copy small { color: #b5d3ef !important; }
.nav-safety {
  margin: 23px 2px 0 !important;
  color: #d8e5f2 !important;
  background: rgba(255, 255, 255, .055) !important;
  border-color: rgba(145, 181, 216, .16) !important;
}
.nav-safety b { color: #f1d69d !important; }
.nav-safety span { color: #91aac2 !important; }

@media (min-width: 721px) and (max-width: 920px) {
  .wrap {
    display: grid !important;
    grid-template-columns: 224px minmax(0, 1fr) !important;
  }
  .cn-nav {
    position: sticky !important;
    top: 0 !important;
    z-index: 40;
    display: block !important;
    width: 224px !important;
    height: 100vh !important;
    padding: 16px 11px 22px !important;
    overflow-x: hidden !important;
    overflow-y: auto !important;
    border-right: 1px solid #173c62 !important;
    border-bottom: 0 !important;
  }
  .nav-brand {
    display: flex !important;
    margin-bottom: 18px !important;
    padding: 12px 10px !important;
  }
  .brand-mark {
    width: 40px !important;
    height: 40px !important;
    flex-basis: 40px !important;
  }
  .nav-brand b { font-size: 14px !important; }
  .nav-brand small { display: block !important; font-size: 9px !important; }
  .nav-section { display: block !important; margin: 16px 10px 6px !important; }
  .cn-nav .nav-item {
    display: flex !important;
    min-height: 42px;
    margin: 2px 0 !important;
    padding: 7px 9px !important;
  }
  .nav-index { display: inline !important; }
  .nav-copy small { display: block !important; }
  .nav-safety { display: block !important; }
  main { padding: 18px 16px 42px !important; }
  .page-intro { padding: 21px !important; }
}

@media (max-width: 720px) {
  .wrap { display: block !important; }
  .cn-nav {
    width: 100% !important;
    background: #092746 !important;
  }
}

/* Chinese market-terminal edition, inspired by familiar domestic quote tools. */
body {
  color: #17283c !important;
  background: #edf1f5 !important;
  font-size: 14px !important;
  line-height: 1.55 !important;
}
header {
  position: sticky !important;
  top: 0 !important;
  z-index: 60 !important;
  min-height: 68px !important;
  padding: 10px 20px !important;
  display: grid !important;
  grid-template-columns: auto 1fr auto !important;
  grid-template-rows: auto auto !important;
  gap: 0 20px !important;
  color: #fff !important;
  background: linear-gradient(90deg, #071c32, #0b2d50 62%, #0c3863) !important;
  border-bottom: 1px solid #28537d !important;
  box-shadow: 0 5px 18px rgba(5, 25, 46, .2) !important;
}
header h1 {
  grid-column: 1 !important;
  grid-row: 1 !important;
  color: #fff !important;
  font-size: 19px !important;
  font-weight: 720 !important;
}
header .sub {
  grid-column: 1 !important;
  grid-row: 2 !important;
  color: #8fb3d6 !important;
  font-size: 9px !important;
}
header .mode-zone {
  grid-column: 2 !important;
  grid-row: 1 / 3 !important;
  align-self: center !important;
  justify-self: end !important;
}
header .ok {
  grid-column: 3 !important;
  grid-row: 1 / 3 !important;
  color: #d5e9fb !important;
  background: rgba(75, 144, 211, .18) !important;
  border-color: rgba(143, 190, 234, .34) !important;
}
.mode-controls {
  padding: 2px !important;
  background: rgba(1, 14, 27, .42) !important;
  border-color: #315777 !important;
  border-radius: 6px !important;
}
.mode-link {
  min-width: 46px !important;
  padding: 4px 9px !important;
  color: #91aec9 !important;
  border-radius: 4px !important;
}
.mode-link.active {
  color: #fff !important;
  background: #176ac5 !important;
  box-shadow: none !important;
}
.mode-pill {
  color: #f4c7c0 !important;
  background: rgba(190, 58, 46, .17) !important;
  border-color: rgba(230, 116, 105, .48) !important;
}
.source-line { color: #9bb5cf !important; }

.wrap { grid-template-columns: 238px minmax(0, 1fr) !important; }
.cn-nav {
  top: 68px !important;
  width: 238px !important;
  height: calc(100vh - 68px) !important;
  padding: 12px 10px 34px !important;
  background: linear-gradient(180deg, #0a2745 0%, #071c33 100%) !important;
  border-right-color: #1c456d !important;
}
.nav-brand {
  margin: 0 0 13px !important;
  padding: 12px 11px !important;
  background: #0e345b !important;
  border-color: #27557f !important;
  border-radius: 7px !important;
  box-shadow: none !important;
}
.brand-mark {
  width: 38px !important;
  height: 38px !important;
  flex-basis: 38px !important;
  background: #176bc5 !important;
  border-radius: 6px !important;
  box-shadow: none !important;
}
.nav-brand b { font-size: 14px !important; }
.nav-brand small { color: #8db2d5 !important; font-size: 9px !important; }
.nav-section {
  margin: 13px 9px 5px !important;
  color: #668bad !important;
  letter-spacing: .13em !important;
}
.cn-nav .nav-item {
  min-height: 40px !important;
  margin: 1px 0 !important;
  padding: 6px 9px !important;
  border-radius: 5px !important;
}
.cn-nav .nav-item:hover {
  background: #103a63 !important;
  transform: none !important;
}
.cn-nav .nav-item.sel {
  background: linear-gradient(90deg, #145d9f, #104678) !important;
  border-color: #347ab5 !important;
  border-radius: 5px !important;
  box-shadow: inset 3px 0 0 #79b9ee !important;
}
.nav-copy b { font-size: 12px !important; }
.nav-copy small { margin-top: 0 !important; font-size: 9px !important; }
.nav-safety {
  margin-top: 14px !important;
  padding: 10px !important;
  border-radius: 5px !important;
}

main {
  max-width: none !important;
  padding: 14px 16px 42px !important;
}
.page-intro {
  min-height: 84px;
  margin-bottom: 10px !important;
  padding: 15px 18px !important;
  align-items: center !important;
  color: #17283c !important;
  background: linear-gradient(100deg, #fff, #f4f8fc) !important;
  border: 1px solid #cdd9e6 !important;
  border-left: 4px solid #176ac5 !important;
  border-radius: 6px !important;
  box-shadow: 0 3px 10px rgba(24, 54, 87, .06) !important;
}
.page-intro::before {
  background:
    linear-gradient(90deg, rgba(27, 93, 161, .045) 1px, transparent 1px),
    linear-gradient(rgba(27, 93, 161, .035) 1px, transparent 1px) !important;
  background-size: 22px 22px !important;
  mask-image: linear-gradient(90deg, transparent 45%, #000) !important;
}
.page-intro::after { display: none !important; }
.page-kicker {
  margin-bottom: 2px !important;
  color: #47759f !important;
  font-size: 9px !important;
}
.page-intro h2 {
  margin-bottom: 2px !important;
  color: #113659 !important;
  font-size: 23px !important;
  letter-spacing: -.035em !important;
}
.page-intro p { color: #607287 !important; font-size: 12px !important; }
.intro-badge {
  padding: 4px 8px !important;
  color: #45627f !important;
  background: #f1f5f9 !important;
  border-color: #d3deea !important;
  border-radius: 4px !important;
  font-size: 10px !important;
}
.intro-badge.mode {
  color: #9b2e25 !important;
  background: #fff0ee !important;
  border-color: #edc6c1 !important;
}
.reading-path {
  gap: 6px !important;
  margin: 0 0 10px !important;
}
.reading-step {
  min-height: 40px;
  padding: 7px 10px !important;
  background: #fff !important;
  border-color: #d4dee9 !important;
  border-radius: 5px !important;
  box-shadow: none !important;
}
.reading-step b {
  width: 22px !important;
  height: 22px !important;
  flex-basis: 22px !important;
  color: #fff !important;
  background: #176ac5 !important;
  border-radius: 3px !important;
  font-size: 10px !important;
}
.reading-step span { font-size: 11px !important; }

.grid { gap: 8px !important; }
.card {
  padding: 13px 14px !important;
  border-color: #d4dee9 !important;
  border-radius: 6px !important;
  box-shadow: 0 2px 8px rgba(20, 51, 84, .045) !important;
}
.grid > .card:nth-child(-n+4) {
  min-height: 104px !important;
  border-top: 2px solid #2a72bb !important;
}
.grid > .card:nth-child(-n+4)::before {
  right: 13px !important;
  top: 14px !important;
  width: 25px !important;
  height: 2px !important;
}
.grid > .card:nth-child(-n+4)::after { display: none !important; }
.panel-title { margin-bottom: 5px !important; font-size: 11px !important; }
.kpi { margin-bottom: 1px !important; font-size: clamp(26px, 2.5vw, 36px) !important; }
.card p { margin-top: 4px; margin-bottom: 4px; font-size: 12px; }
.card ul { margin-top: 4px; }
.card li { margin: 2px 0; font-size: 12px; }
.warn {
  margin: 5px 0 9px !important;
  padding: 7px 9px !important;
  border-radius: 2px !important;
  font-size: 11px !important;
}

.table-scroll, .table-shell { border-radius: 4px !important; }
table { min-width: 1080px !important; font-size: 12px !important; }
thead th, table th {
  padding: 8px 8px !important;
  color: #dce9f6 !important;
  background: #12395d !important;
  border-bottom-color: #2b587f !important;
  font-size: 10.5px !important;
}
tbody td, table td {
  padding: 7px 8px !important;
  color: #2e4359 !important;
  border-bottom-color: #e0e7ee !important;
  line-height: 1.4 !important;
}
tbody tr:nth-child(even) td { background: #f7f9fc !important; }
tbody tr:hover td { background: #e8f2fc !important; }
table th:first-child { background: #12395d !important; }
table a { color: #175eaa !important; }
.market-up { color: #d73b32 !important; font-weight: 700 !important; }
.market-down { color: #168357 !important; font-weight: 700 !important; }
.market-flat { color: #6c7b8b !important; }

@media (min-width: 721px) and (max-width: 920px) {
  header { position: sticky !important; display: grid !important; padding: 9px 14px !important; }
  header .ok { position: static !important; }
  header .mode-zone { width: auto !important; margin-top: 0 !important; flex-wrap: nowrap !important; }
  header .source-line { display: none !important; }
  .wrap { grid-template-columns: 206px minmax(0, 1fr) !important; }
  .cn-nav {
    top: 68px !important;
    width: 206px !important;
    height: calc(100vh - 68px) !important;
    padding: 10px 8px 28px !important;
  }
  .nav-brand { padding: 9px 8px !important; }
  .brand-mark { width: 34px !important; height: 34px !important; flex-basis: 34px !important; }
  .nav-brand b { font-size: 12px !important; }
  .cn-nav .nav-item { min-height: 38px !important; }
  main { padding: 10px 10px 36px !important; }
  .page-intro { min-height: 76px; padding: 12px 14px !important; }
}

@media (max-width: 720px) {
  header { position: relative !important; display: flex !important; min-height: 96px !important; }
  .cn-nav { top: 0 !important; height: auto !important; padding: 7px 8px !important; }
  .page-intro { align-items: flex-start !important; }
}

/* Readable navigation edition: the workstation must be legible at a glance. */
.wrap { grid-template-columns: 282px minmax(0, 1fr) !important; }
.cn-nav {
  width: 282px !important;
  padding: 15px 13px 36px !important;
}
.nav-brand {
  gap: 14px !important;
  margin-bottom: 18px !important;
  padding: 15px 13px !important;
  border-radius: 9px !important;
}
.brand-mark {
  width: 44px !important;
  height: 44px !important;
  flex-basis: 44px !important;
  font-size: 16px !important;
  border-radius: 8px !important;
}
.nav-brand b {
  font-size: 17px !important;
  font-weight: 760 !important;
  line-height: 1.3 !important;
}
.nav-brand small {
  margin-top: 5px !important;
  color: #acd0f0 !important;
  font-size: 11px !important;
  font-weight: 560 !important;
  line-height: 1.4 !important;
}
.nav-section {
  position: relative;
  margin: 20px 10px 8px !important;
  padding-bottom: 7px;
  color: #8db1d2 !important;
  border-bottom: 1px solid rgba(126, 168, 207, .17);
  font-size: 11.5px !important;
  font-weight: 720 !important;
  letter-spacing: .11em !important;
}
.cn-nav .nav-item {
  display: grid !important;
  grid-template-columns: 31px minmax(0, 1fr);
  align-items: center !important;
  gap: 10px !important;
  min-height: 54px !important;
  margin: 3px 0 !important;
  padding: 8px 11px !important;
  border-radius: 7px !important;
}
.nav-index {
  display: grid !important;
  place-items: center;
  width: 29px;
  height: 29px;
  color: #8bafd1 !important;
  background: rgba(117, 166, 211, .09);
  border: 1px solid rgba(130, 176, 217, .13);
  border-radius: 6px;
  font-size: 11px !important;
  font-weight: 720 !important;
}
.nav-copy b {
  font-size: 14px !important;
  font-weight: 690 !important;
  line-height: 1.35 !important;
}
.nav-copy small {
  margin-top: 4px !important;
  color: #91b0cc !important;
  font-size: 11px !important;
  font-weight: 480 !important;
  line-height: 1.35 !important;
  white-space: normal !important;
}
.cn-nav .nav-item:hover {
  background: #113d68 !important;
  border-color: rgba(109, 165, 218, .22) !important;
}
.cn-nav .nav-item.sel {
  background: linear-gradient(90deg, #1768ad, #104979) !important;
  border-color: #4389c2 !important;
  box-shadow: inset 4px 0 0 #8ac8fa, 0 8px 18px rgba(1, 15, 31, .16) !important;
}
.nav-item.sel .nav-index {
  color: #fff !important;
  background: rgba(255, 255, 255, .13);
  border-color: rgba(255, 255, 255, .2);
}
.nav-item.sel .nav-copy small { color: #d0e5f7 !important; }
.nav-safety {
  margin-top: 20px !important;
  padding: 13px !important;
}
.nav-safety b { font-size: 12px !important; }
.nav-safety span { margin-top: 4px; font-size: 11px !important; line-height: 1.6 !important; }

@media (min-width: 721px) and (max-width: 1100px) {
  .wrap { grid-template-columns: 244px minmax(0, 1fr) !important; }
  .cn-nav { width: 244px !important; padding: 12px 9px 30px !important; }
  .nav-brand { gap: 10px !important; padding: 12px 10px !important; }
  .brand-mark { width: 38px !important; height: 38px !important; flex-basis: 38px !important; }
  .nav-brand b { font-size: 15px !important; }
  .nav-brand small { font-size: 10px !important; }
  .nav-section { margin-top: 17px !important; font-size: 11px !important; }
  .cn-nav .nav-item {
    grid-template-columns: 27px minmax(0, 1fr) !important;
    min-height: 49px !important;
    padding: 7px 8px !important;
  }
  .nav-index { width: 26px; height: 26px; font-size: 10px !important; }
  .nav-copy b { font-size: 13px !important; }
  .nav-copy small { font-size: 10px !important; }
}

@media (max-width: 720px) {
  .wrap { display: block !important; }
  .cn-nav {
    display: flex !important;
    width: 100% !important;
    height: auto !important;
    padding: 8px !important;
  }
  .cn-nav .nav-item {
    display: flex !important;
    min-height: 40px !important;
    padding: 7px 11px !important;
  }
  .nav-index, .nav-copy small { display: none !important; }
  .nav-copy b { font-size: 13px !important; white-space: nowrap; }
}
"""


WORKBENCH_JS = r"""
<script>
(() => {
  document.documentElement.lang = 'zh-CN';
  document.body.classList.add('cn-workbench');

  const exactText = new Map([
    ['🎯 A股机会雷达 V8.6.6', 'A股机会雷达'],
    ['Rich Workbench / Server Navigation', 'V8.6.6 中文决策工作台'],
    ['DEMO', '演示'],
    ['LIVE', '实时'],
    ['PIT', '历史实况'],
    ['● READY', '系统已就绪'],
    ['Meta-Label', '样本表现'],
    ['Conformal', '置信边界'],
    ['Monte Carlo', '风险压力测试'],
    ['Alpha 排名', '综合排名'],
    ['Alpha排名', '综合排名']
  ]);
  const phraseText = [
    ['auction_tick: ERROR', '竞价过程数据：暂不可用'],
    ['EOD_CROSS_SECTION', '收盘横截面'],
    ['INTRADAY_PIT', '盘中真实快照'],
    ['REALTIME_WINNERS', '强势候选'],
    ['EARLY_STARTUP', '早期启动'],
    ['REVERSAL_REPAIR', '修复转强'],
    ['Meta-Label', '样本表现'],
    ['Monte Carlo', '风险压力测试'],
    ['CPCV', '样本外检验'],
    ['Conformal', '置信边界'],
    ['DSR', '稳健性检验'],
    ['NO_PERMISSION', '未授权'],
    ['ERROR', '异常'],
    ['PASS', '通过']
  ];

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach((node) => {
    const parent = node.parentElement;
    if (!parent || ['SCRIPT', 'STYLE', 'CODE', 'PRE'].includes(parent.tagName)) return;
    const trimmed = node.nodeValue.trim();
    if (!trimmed) return;
    if (exactText.has(trimmed)) {
      node.nodeValue = node.nodeValue.replace(trimmed, exactText.get(trimmed));
      return;
    }
    let value = node.nodeValue;
    phraseText.forEach(([from, to]) => { value = value.split(from).join(to); });
    node.nodeValue = value;
  });

  const pages = {
    overview: ['今日总览', '先确认数据是否可信，再看候选强弱；发现层用于找机会，不等于可以买。'],
    a_exec: ['A级执行', '这里只看通过执行门槛的信号，并逐项解释支撑、压力、资金、竞价与风险否决。'],
    sniper: ['A+狙击', '比A级更严格的观察区；门槛未全部通过时保持观察，不自动开启。'],
    meta: ['真实样本表现', '回答信号过去是否有效；样本不足时不生成真实胜率。'],
    conformal: ['置信边界', '展示预测的不确定范围，避免把一个概率数字误当成确定结果。'],
    cpcv: ['样本外检验', '检查策略在不同历史阶段是否仍然稳定，重点防止未来泄漏和过拟合。'],
    factor: ['因子分析', '拆开查看资金、竞价、筹码和趋势等证据，未验证因子不进入正式执行链。'],
    limitup: ['连板与板块观察', '查看涨停生态和板块扩散，只用于发现强势方向，不等同于追涨建议。'],
    sim: ['执行模拟', '估算滑点、成交概率和真实执行代价；当前仅验证，不自动交易。'],
    mc: ['风险压力测试', '用多种不利情景观察回撤承受力，不把模拟结果当成真实收益。'],
    drift: ['稳定性监控', '检查数据、规则和历史表现是否发生漂移，发现异常时优先暂停判断。'],
    health: ['数据健康', '集中查看字段完整度、更新时间、异常接口和PIT安全状态。'],
    sources: ['数据源与权限', '查看真实付费接口是否已配置、是否授权、返回多少数据以及最近错误。']
  };
  const params = new URLSearchParams(location.search);
  const page = params.get('page') || 'overview';
  const mode = (params.get('mode') || 'DEMO').toUpperCase();
  const modeNames = { DEMO: '演示数据', LIVE: '实时数据', PIT: '历史实况' };
  const [title, description] = pages[page] || pages.overview;
  document.title = `${title}｜A股机会雷达`;

  const main = document.querySelector('main');
  if (main) {
    const intro = document.createElement('section');
    intro.className = 'page-intro';
    intro.innerHTML = `
      <div class="page-intro-copy">
        <span class="page-kicker">A股机会雷达 · V8.6.6</span>
        <h2>${title}</h2>
        <p>${description}</p>
      </div>
      <div class="intro-badges">
        <span class="intro-badge mode">${modeNames[mode] || '演示数据'}</span>
        <span class="intro-badge">仅验证与展示</span>
        <span class="intro-badge">不自动交易</span>
      </div>`;
    main.prepend(intro);

    if (page === 'overview') {
      const path = document.createElement('section');
      path.className = 'reading-path';
      path.setAttribute('aria-label', '建议阅读顺序');
      path.innerHTML = `
        <div class="reading-step"><b>1</b><span>先看数据覆盖和异常提示</span></div>
        <div class="reading-step"><b>2</b><span>再看强势候选及入选原因</span></div>
        <div class="reading-step"><b>3</b><span>最后进入A级执行核对风险</span></div>`;
      intro.insertAdjacentElement('afterend', path);
    }
  }

  document.querySelectorAll('table').forEach((table) => {
    if (!table.parentElement.classList.contains('table-scroll') &&
        !table.parentElement.classList.contains('table-shell')) {
      const shell = document.createElement('div');
      shell.className = 'table-shell';
      table.parentNode.insertBefore(shell, table);
      shell.appendChild(table);
    }
  });
})();
</script>
"""


def render_page(*args, **kwargs):
    html = _original_render_page(*args, **kwargs)
    if "</style>" in html:
        html = html.replace("</style>", WORKBENCH_CSS + "</style>", 1)
    elif "</head>" in html:
        html = html.replace("</head>", "<style>" + WORKBENCH_CSS + "</style></head>", 1)
    else:
        html = "<style>" + WORKBENCH_CSS + "</style>" + html
    if "</body>" in html:
        html = html.replace("</body>", WORKBENCH_JS + "</body>", 1)
    else:
        html += WORKBENCH_JS
    return html


core._render_nav = _render_cn_nav
core.render_page = render_page


def main():
    host = os.environ.get("RADAR_HOST", "127.0.0.1")
    port = int(os.environ.get("RADAR_PORT", "8791"))
    server = ThreadingHTTPServer((host, port), core.H)
    print("A股机会雷达 V8.6.6 中文决策工作台")
    print("打开：http://%s:%s/?page=overview&mode=LIVE" % (host, port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
