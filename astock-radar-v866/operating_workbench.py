# -*- coding: utf-8 -*-
"""Daily operator views for the V8.6.6 independent-validation workbench.

These views are read-only. They consolidate the existing real-analysis file,
immutable signal history and manual portfolio ledger without changing legacy V8
rules, execution thresholds, notifications or any broker state.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from live_analysis import load_live_analysis, load_pit_history
from portfolio_monitor import PortfolioMonitor


ROOT = Path(__file__).resolve().parent


def _audit_payload(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _runtime_status():
    pit = _audit_payload(ROOT / "P0_REALTIME_ACCEPTANCE.json")
    outcome = _audit_payload(ROOT / "SIGNAL_OUTCOME_AUDIT.json")
    push = _audit_payload(ROOT / "data" / "PUSH_BRIDGE_AUDIT.json")
    return {
        "pit": pit.get("status", "尚未生成审计"),
        "pit_at": pit.get("generated_at", pit.get("checked_at", "--")),
        "outcome": outcome.get("status", "尚未生成审计"),
        "outcome_at": outcome.get("generated_at", outcome.get("completed_at", "--")),
        "future_null": outcome.get("future_null_preserved", "--"),
        "push": push.get("status", "未启动推送桥"),
        "push_at": push.get("checked_at", "--"),
    }


def _text(value, fallback="--"):
    return escape(str(value if value not in (None, "") else fallback))


def _number(value, digits=1):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _percent(value):
    try:
        number = float(value)
        if abs(number) > 1:
            number /= 100.0
        return f"{number * 100:.1f}%"
    except (TypeError, ValueError):
        return "--"


def _legacy_count(history):
    return sum(1 for item in history.get("signals", []) if str(item.get("source", "")).startswith("V8"))


def _ticket_card(item):
    evidence = item.get("evidence", {}) or {}
    veto = item.get("veto") or "未触发硬否决，仍需人工核验事件风险"
    pool = str(item.get("pool") or "观察")
    action = str(item.get("action") or "观察")
    return (
        "<div class='card w2 focus-signal-card'>"
        f"<p class='panel-title'>{_text(item.get('name'))} <code>{_text(item.get('ts_code'))}</code>｜{_text(pool)}级｜{_text(action)}</p>"
        "<div class='grid'>"
        f"<div><p class='kpi'>{_number(item.get('score'))}</p><p>规则评分</p></div>"
        f"<div><p class='kpi'>{_percent(item.get('t1_probability'))}</p><p>次日规则估计</p></div>"
        f"<div><p class='kpi'>{_number(item.get('rr'), 2)}</p><p>估算盈亏比</p></div>"
        f"<div><p class='kpi'>{_number(item.get('data_quality'), 2)}</p><p>数据质量</p></div>"
        "</div>"
        f"<p>市场 {_text(item.get('market_phase'))} / {_number(item.get('market_temp'))}｜板块 {_text(item.get('sector'))} / {_number(item.get('sector_strength'))}</p>"
        f"<p>竞价 {_number(item.get('auction_quality'))}｜资金 {_number(item.get('main_flow_score'))}｜筹码 {_number(item.get('chip_lock_score'))}｜趋势 {_number(item.get('trend_score'))}</p>"
        f"<p>支撑 {_number(item.get('support'), 3)} / 强支撑 {_number(item.get('strong_support'), 3)}｜压力 {_number(item.get('resistance'), 3)} / 强压力 {_number(item.get('strong_resistance'), 3)}</p>"
        f"<p>隔夜风险 {_number(item.get('overnight_risk'))}｜风险否决：<span class='warn'>{_text(veto)}</span></p>"
        "<details class='signal-evidence'><summary>查看数据证据与来源</summary><div class='signal-evidence-body'>"
        f"<p class='muted'>日线：{_text(evidence.get('daily'))}<br>板块：{_text(evidence.get('sector'))}<br>资金：{_text(evidence.get('moneyflow'))}<br>竞价：{_text(evidence.get('auction'))}<br>筹码：{_text(evidence.get('chip'))}<br>风险：{_text(evidence.get('risk'))}</p>"
        f"<p class='muted'>来源：{_text(item.get('source'))}｜观测时间：{_text(item.get('observed_at'))}｜PIT安全：{'是' if item.get('is_pit_safe') else '待确认'}</p>"
        "</div></details></div>"
    )


def render_ticket_page(status, mode):
    """Render the daily evidence tickets; no candidate is converted to an order."""
    analysis = load_live_analysis() or {}
    candidates = list(analysis.get("candidates", []) or [])
    history = load_pit_history() or {}
    qualified = [item for item in candidates if str(item.get("pool")) in {"A", "B"}]
    shown = qualified[:12] or candidates[:12]
    observed_at = analysis.get("generated_at", status.get("updated_at"))
    notice = (
        "当前没有 A/B 执行候选；以下仅展示高分观察对象，不构成买入建议。"
        if not qualified else
        "以下是新版独立票据：先核对证据、风险和时点，再由执行者决定是否观察或放弃。"
    )
    cards = "".join(_ticket_card(item) for item in shown)
    if not cards:
        cards = "<div class='card w4'><p class='warn'>尚无真实分析候选。请先完成实时分析刷新与PIT快照采集。</p></div>"
    return (
        "<div class='grid'>"
        "<div class='card focus-metric'><p class='panel-title'>本次分析候选</p>"
        f"<p class='kpi'>{_text(len(candidates))}</p><p>来自当前真实分析快照</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>A/B执行候选</p>"
        f"<p class='kpi'>{_text(len(qualified))}</p><p>未等同于已买入</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>旧V8历史信号</p>"
        f"<p class='kpi'>{_text(_legacy_count(history))}</p><p>只作历史证据保留</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>真实信号结果</p>"
        f"<p class='kpi'>{_text(history.get('t1_count', 0))}</p><p>已发生的次日结果</p></div>"
        f"<div class='card w4'><p class='panel-title'>执行边界</p><p class='warn'>{escape(notice)}</p>"
        f"<p class='muted'>数据模式：{_text(mode)}｜分析观测时间：{_text(observed_at)}。次日规则估计不是胜率，系统不会自动下单或发送买卖指令。</p></div>"
        + cards + "</div>"
    )


def render_review_page(status, mode):
    """Render an end-of-day review from actual stored state and manual holdings."""
    analysis = load_live_analysis() or {}
    history = load_pit_history() or {}
    portfolio = PortfolioMonitor().snapshot()
    discovery = analysis.get("discovery", {}) or {}
    candidates = list(analysis.get("candidates", []) or [])
    positions = portfolio.get("positions", []) or []
    risks = portfolio.get("risks", []) or ["当前没有触发默认账户风险线。"]
    checkpoints = (status.get("sample_domains", {}) or {}).get("discovery", {}).get("checkpoints", {}) or {}
    runtime = _runtime_status()
    checkpoint_text = "｜".join(
        f"{label}：{_text((checkpoints.get(label) or {}).get('days', 0))}天"
        for label in ("09:25", "09:35", "10:00", "11:00", "13:30", "14:30")
    )
    risk_html = "".join(f"<li>{_text(item)}</li>" for item in risks)
    actions = (
        "<li>确认六个盘中时点是否已保存；缺失时只记录缺失，不使用盘后数据补造。</li>"
        "<li>确认今天真实产生的交易信号是否进入信号票据；普通发现候选不得进入胜率样本。</li>"
        "<li>确认已建仓股票的当前价、止损线、目标线和实际操作是否已手工更新。</li>"
        "<li>未来结果未发生时保持空值；不得提前生成次日、三日或五日结果。</li>"
    )
    return (
        "<div class='grid'>"
        "<div class='card focus-metric'><p class='panel-title'>今日发现候选</p>"
        f"<p class='kpi'>{_text(len(candidates))}</p><p>全市场发现层</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>真实交易信号</p>"
        f"<p class='kpi'>{_text(history.get('signal_count', 0))}</p><p>累计信号票据</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>人工持仓</p>"
        f"<p class='kpi'>{_text(len(positions))}</p><p>仅手工录入</p></div>"
        "<div class='card focus-metric'><p class='panel-title'>PIT安全记录</p>"
        f"<p class='kpi'>{_text(history.get('pit_safe_count', 0))}</p><p>可追溯历史记录</p></div>"
        "<div class='card w2'><p class='panel-title'>盘中快照进度</p>"
        f"<p>{checkpoint_text}</p><p class='muted'>完整交易日：{_text(discovery.get('complete_trade_days', 0))}｜召回已标注：{_text(discovery.get('recall_labeled_days', 0))}天</p></div>"
        "<div class='card w2'><p class='panel-title'>持仓防守状态</p>"
        f"<p>持仓市值：{_number(portfolio.get('total_value'), 0)} 元｜预计止损风险：{_number(portfolio.get('estimated_stop_risk'), 0)} 元</p>"
        f"<ul class='portfolio-risk-list'>{risk_html}</ul></div>"
        "<div class='card w2'><p class='panel-title'>P0采集器审计</p>"
        f"<p>状态：{_text(runtime['pit'])}</p><p class='muted'>最近审计：{_text(runtime['pit_at'])}</p>"
        "<p class='muted'>启动文件：START_V8_6_6_OPERATOR.bat</p></div>"
        "<div class='card w2'><p class='panel-title'>结果回填审计</p>"
        f"<p>状态：{_text(runtime['outcome'])}｜未来结果保持空值：{_text(runtime['future_null'])}</p>"
        f"<p class='muted'>最近审计：{_text(runtime['outcome_at'])}｜收盘文件：CLOSE_V8_6_6_REVIEW.bat</p></div>"
        "<div class='card w4'><p class='panel-title'>收盘复盘清单</p><ul>" + actions + "</ul>"
        "<p class='warn'>本页只汇总已经保存的真实记录。没有生成真实胜率、没有训练模型、没有开启A+、没有自动交易。</p></div>"
        "<div class='card w4'><p class='panel-title'>复盘结论边界</p>"
        f"<p>数据模式：{_text(mode)}｜发现状态：{_text(discovery.get('mode'))}｜当前召回状态：{_text(discovery.get('recall_status'))}。</p>"
        f"<p class='muted'>推送桥状态：{_text(runtime['push'])}｜最近检查：{_text(runtime['push_at'])}。默认运行入口不启动推送桥，避免与旧V8消息混乱。</p>"
        "<p class='muted'>今日表现只能在未来结果发生并被回填后评估；候选上涨不等同于系统胜率，人工持仓盈亏也不等同于信号模型表现。</p></div>"
        "</div>"
    )
