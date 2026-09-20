"""Read-only decision cards. No signals, provider requests or order side effects."""
from datetime import datetime, timedelta, timezone
from functools import wraps
from html import escape
import math
from urllib.parse import urlencode

TZ = timezone(timedelta(hours=8))


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def date_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)
    except (ValueError, TypeError):
        return None


def text(value):
    return escape(str(value if value is not None else "未提供"), quote=True)


def fmt(value, suffix="", digits=2):
    value = number(value)
    return "未提供" if value is None else f"{value:.{digits}f}{suffix}"


def assess_quote(record, snapshot, now):
    """A receipt timestamp is never substituted for an exchange timestamp."""
    received = date_time(record.get("observed_at"))
    source = date_time(record.get("source_trade_time") or record.get("trade_time"))
    issues = []
    if not received or received.date() != now.date() or not -60 <= (now - received).total_seconds() <= 180:
        issues.append("接收时间缺失或超过3分钟")
    if not source or source.date() != now.date() or not -60 <= (now - source).total_seconds() <= 180:
        issues.append("个股行情源时间未确认或已过期")
    if str(record.get("snapshot_id", "")) != str(snapshot.get("snapshot_id", "")) or not snapshot.get("snapshot_id"):
        issues.append("无法确认与当前快照一致")
    quality = number(record.get("data_quality"))
    if quality is None or quality < .75:
        issues.append("数据质量不足")
    if record.get("is_pit_safe") not in (1, True, "1"):
        issues.append("PIT标记未通过")
    provenance = str(record.get("source") or "").upper()
    if not provenance or any(tag in provenance for tag in ("DEMO", "MOCK", "SIM", "LEGACY")):
        issues.append("来源缺失或不是可信实时来源")
    if number(record.get("close")) is None or number(record.get("close")) <= 0:
        issues.append("无有效价格")
    minutes = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (570 <= minutes <= 690 or 780 <= minutes <= 900):
        issues.append("非连续交易时段，仅供复盘")
    return {"issues": issues, "fresh": not issues, "source_time": source, "received": received}


def build_view(data, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    now = now.astimezone(TZ)
    snapshot = dict(data.get("snapshot") or {})
    by_code = data.get("by_code") or {}
    rows = []
    for code, raw in by_code.items():
        row = dict(raw)
        pct, price, volume = number(row.get("pct_chg")), number(row.get("close")), number(row.get("vol"))
        # This is a transparent display filter, not a fitted stock-selection model.
        if pct is None or price is None or price <= 0 or volume is None or volume <= 0 or not 0 < pct < 8:
            continue
        row["ts_code"] = row.get("ts_code") or code
        row["assessment"] = assess_quote(row, snapshot, now)
        rows.append(row)
    rows.sort(key=lambda r: (not r["assessment"]["fresh"], number(r.get("market_rank")) or float("inf"), str(r["ts_code"])))
    return {"snapshot": snapshot, "rows": rows[:5], "filtered_count": len(rows), "now": now}


STYLE = """<style>
.decision-space{--dw-gold:#ebc981;--dw-muted:#a6b5c9;--dw-border:rgba(152,178,208,.23);margin:20px 0;color:#eef3fa;font-family:'Microsoft YaHei','PingFang SC',sans-serif}
.decision-space *{box-sizing:border-box}.decision-space .dw-head{padding:25px 26px;border:1px solid var(--dw-border);border-radius:18px;background:radial-gradient(ellipse at 100% 0,rgba(235,201,129,.13),transparent 57%),linear-gradient(130deg,#172637,#101b2b)}
.decision-space .dw-eyebrow{color:var(--dw-gold);font-size:12px;letter-spacing:.14em}.decision-space h2{font-size:24px;line-height:1.35;margin:10px 0}.decision-space p{color:var(--dw-muted);line-height:1.7;margin:8px 0;font-size:13px}
.decision-space .dw-meta{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.decision-space .dw-pill{font-size:12px;border:1px solid var(--dw-border);border-radius:7px;padding:6px 10px;color:#d6e0ee}
.decision-space .dw-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:15px}.decision-space .dw-card{background:linear-gradient(145deg,#172437,#111c2a);border:1px solid var(--dw-border);border-top:2px solid #738ba3;padding:18px;border-radius:12px;min-width:0}
.decision-space .dw-card h3{font-size:18px;margin:0 0 6px}.decision-space a{color:#e9d29f;text-decoration:none}.decision-space a:hover{text-decoration:underline}.decision-space .dw-numbers{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:17px 0}.decision-space dt{font-size:11px;color:var(--dw-muted)}.decision-space dd{font-family:Consolas,monospace;font-size:19px;margin:4px 0 0}.decision-space .dw-line{border-top:1px solid var(--dw-border);padding-top:10px;margin-top:10px;font-size:12px;line-height:1.7;overflow-wrap:anywhere}.decision-space .dw-label{color:var(--dw-gold)}.decision-space details{margin-top:10px;font-size:12px;color:var(--dw-muted)}.decision-space summary{cursor:pointer}.decision-space .dw-empty{padding:24px;border:1px dashed var(--dw-border);border-radius:12px;margin-top:14px}
@media(max-width:620px){.decision-space .dw-head{padding:19px}.decision-space h2{font-size:21px}.decision-space .dw-grid{grid-template-columns:1fr}.decision-space .dw-pill{max-width:100%;overflow-wrap:anywhere}}
@media(prefers-reduced-motion:no-preference){.decision-space .dw-card{animation:dw-reveal .35s ease-out both}@keyframes dw-reveal{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:translateY(0)}}}
</style>"""


def render_cards(data):
    view = build_view(data)
    snapshot = view["snapshot"]
    html = [STYLE, '<section class="decision-space" aria-label="重点观察与数据依据"><div class="dw-head"><div class="dw-eyebrow">DECISION DESK / 观察与证据</div><h2>先看持仓风险，再看值得跟踪的机会</h2><p>这里不把评分翻译成胜率，也不把昨日强势股当成今天的买点。以下最多5只仅为行情观察，不新增A级、交易指令或微信推送。</p><div class="dw-meta">']
    for label in ("界面补丁 D1 · 未改变策略", f"快照接收：{snapshot.get('observed_at') or '未提供'}", "统计优势：本模块未验证"):
        html.append(f'<span class="dw-pill">{text(label)}</span>')
    html.append('</div></div><p>筛选口径：正涨幅且低于8%、有效价格与成交量；优先展示个股时效检查通过的记录，再按已有市场排名排序。该口径不是收益预测或完整风控；交易日历与事件风险仍需复核。</p><div class="dw-grid">')
    for row in view["rows"]:
        assessment = row["assessment"]
        code = row["ts_code"]
        url = "/?" + urlencode({"page": "stock_detail", "mode": "LIVE", "code": code})
        vwap, price = number(row.get("vwap")), number(row.get("close"))
        gap = (price / vwap - 1) * 100 if price is not None and vwap is not None and vwap > 0 else None
        state = "行情时效通过 · 仅观察" if assessment["fresh"] else "证据待补 · 不作实时决策"
        issues = assessment["issues"] or ["仅通过行情门槛；未完成事件、可成交性及策略样本外验证"]
        html.append(f'<article class="dw-card"><h3><a href="{text(url)}">{text(row.get("name") or code)}</a></h3><p>{text(code)} · {text(row.get("industry") or "板块未知")}</p><span class="dw-pill">{text(state)}</span><dl class="dw-numbers"><div><dt>快照价</dt><dd>{fmt(price)}</dd></div><div><dt>当时涨幅</dt><dd>{fmt(row.get("pct_chg"), "%")}</dd></div><div><dt>距成交均价 VWAP</dt><dd>{fmt(gap, "%")}</dd></div><div><dt>已有市场排名</dt><dd>{fmt(row.get("market_rank"), digits=0)}</dd></div></dl>')
        html.append('<div class="dw-line"><span class="dw-label">继续观察</span><br>等待新的同日行情确认量价延续；同时复核公告、板块联动、价差及实际可成交性。未满足条件不升级为执行。</div>')
        html.append('<div class="dw-line"><span class="dw-label">停止参考本卡</span><br>行情过期、来源不明或快照不一致时停止实时参考。这不是自动止损价，也不替代持仓风控。</div>')
        html.append(f'<details><summary>缺失证据与时间戳</summary><p>{text("；".join(issues))}</p><p>个股源时间：{text(row.get("source_trade_time") or row.get("trade_time"))}<br>接收时间：{text(row.get("observed_at"))}<br>快照：{text(row.get("snapshot_id"))}</p></details></article>')
    html.append('</div>')
    if not view["rows"]:
        html.append('<div class="dw-empty">当前没有满足本观察口径的数据。不使用DEMO或硬编码候选填满页面，请先检查数据健康页。</div>')
    html.append('</section>')
    return "".join(html)


def install():
    """Extend the existing holdings panel without replacing account/HTTP routes."""
    import operator_workspace
    import cloud_product_workbench

    original = operator_workspace.operator_panel
    if getattr(original, "_decision_workspace", False):
        return

    @wraps(original)
    def enhanced(*args, **kwargs):
        result = original(*args, **kwargs)
        data = next((value for value in (*args, *kwargs.values()) if isinstance(value, dict) and "by_code" in value), None)
        if data is None:
            return result
        return result + render_cards(data)

    enhanced._decision_workspace = True
    operator_workspace.operator_panel = enhanced
    # Support both local imports and a module-level imported reference.
    if getattr(cloud_product_workbench, "operator_panel", None) is original:
        cloud_product_workbench.operator_panel = enhanced
