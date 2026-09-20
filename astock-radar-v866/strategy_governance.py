# -*- coding: utf-8 -*-
"""Independent-governance layer for the V8.6.6 upgrade.

This module deliberately treats legacy V8 output as traceable evidence only.
It never changes an execution grade, opens A+, sends an order, or writes to a
legacy V8 file/database. The workbench uses it to show the current upgrade
boundary and the real-data readiness of the new independent validation chain.
"""
from __future__ import annotations

from html import escape

from live_analysis import load_pit_history


LEGACY_EVIDENCE_CONTRACT = (
    ("旧V8可提供", "原始信号、原始评分、推送时间、规则理由、持仓防守经验"),
    ("新版必须独立保存", "观测时间、数据来源、PIT安全、风险证据、人工执行事实、后续结果"),
    ("旧V8不能直接决定", "新版执行等级、A+开启、真实胜率、自动交易或策略参数"),
)


def _value(value, default="0"):
    return escape(str(value if value not in (None, "") else default))


def _sample_counts(status):
    domains = status.get("sample_domains", {}) or {}
    discovery = domains.get("discovery", {}) or {}
    trade_signal = domains.get("trade_signal", {}) or {}
    history = load_pit_history() or {}
    return {
        "pit_rows": discovery.get("pit_market_rows", 0),
        "trade_days": discovery.get("complete_trade_days", 0),
        "signals": trade_signal.get("trade_signals", history.get("signal_count", 0)),
        "paired": trade_signal.get("paired", history.get("paired_count", 0)),
        "t1": trade_signal.get("t1_labeled", history.get("t1_count", 0)),
        "meta": trade_signal.get("meta_eligible", history.get("meta_eligible", 0)),
        "pit_safe": history.get("pit_safe_count", 0),
    }


def render_upgrade_page(status, mode):
    """Render the operational upgrade contract without exposing DEMO metrics."""
    counts = _sample_counts(status)
    rows = "".join(
        "<tr><td><b>%s</b></td><td>%s</td></tr>" % (escape(title), escape(detail))
        for title, detail in LEGACY_EVIDENCE_CONTRACT
    )
    mode_name = {"DEMO": "演示数据", "LIVE": "实时数据", "PIT": "历史实况"}.get(mode, "演示数据")
    return (
        "<div class='grid'>"
        "<div class='card focus-metric'><p class='panel-title'>当前升级阶段</p>"
        "<p class='kpi'>P0</p><p>真实数据与结果基础设施</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>全市场PIT快照</p>"
        "<p class='kpi'>%s</p><p>真实股票快照行</p></div>" % _value(counts["pit_rows"])
        + "<div class='card focus-metric'><p class='panel-title'>真实交易信号</p>"
        "<p class='kpi'>%s</p><p>仅 A/B/A+/主信号</p></div>" % _value(counts["signals"])
        + "<div class='card focus-metric'><p class='panel-title'>可用于二次验证</p>"
        "<p class='kpi'>%s</p><p>真实且PIT安全的有效样本</p></div>" % _value(counts["meta"])
        + "<div class='card w4'><p class='panel-title'>新版升级原则：借鉴旧V8经验，但不复制旧V8结论</p>"
        "<p class='warn'>当前模式：%s。新版仍处于验证阶段，不生成真实胜率、不训练二次模型、不启用A+、不自动交易。</p>"
        "<table><tr><th>边界</th><th>规则</th></tr>%s</table></div>" % (escape(mode_name), rows)
        + "<div class='card w2'><p class='panel-title'>旧V8应保留的优点</p>"
        "<p>早期发现、规则解释、洗盘与真假跌观察、持仓防守、微信运行经验。</p>"
        "<p class='muted'>这些内容进入新版时必须保留原始来源和时间，作为证据，而不是新版本的自动结论。</p></div>"
        "<div class='card w2'><p class='panel-title'>新版必须新建的能力</p>"
        "<p>全市场盘中PIT、统一信号票据、人工成交账本、结果回填、样本隔离、独立复盘。</p>"
        "<p class='muted'>没有真实决策时点和真实结果的内容，只能展示，不能进入胜率或模型训练。</p></div>"
        "<div class='card w4'><p class='panel-title'>升级执行顺序</p>"
        "<table><tr><th>顺序</th><th>新版要完成什么</th><th>验收证据</th></tr>"
        "<tr><td>1</td><td>固定保存全市场 09:25、09:35、10:00、11:00、13:30、14:30 快照</td><td>完整交易日与每个时点均可追溯</td></tr>"
        "<tr><td>2</td><td>保存系统当时真实产生的交易信号</td><td>信号时间、来源、评分、风险与PIT状态齐全</td></tr>"
        "<tr><td>3</td><td>未来发生后再回填 T+1/T+3/T+5、MFE、MAE</td><td>未发生的未来结果必须保持空值</td></tr>"
        "<tr><td>4</td><td>把人工建仓与旧V8持仓防守接入新版持仓台</td><td>建议、实际成交、持仓风险和复盘分别记录</td></tr>"
        "<tr><td>5</td><td>只用真实交易信号验证 Meta、CPCV、Conformal</td><td>样本不足时明确停用，不显示演示胜率</td></tr>"
        "</table></div>"
        "<div class='card w4'><p class='panel-title'>当前真实进度</p>"
        "<p>完整交易日：%s｜已配对信号：%s｜已有次日结果：%s｜PIT安全历史记录：%s。</p>"
        "<p class='muted'>这些数字只说明数据积累进度，不代表胜率、收益或可以交易。</p></div>"
        % (_value(counts["trade_days"]), _value(counts["paired"]), _value(counts["t1"]), _value(counts["pit_safe"]))
        + "</div>"
    )
