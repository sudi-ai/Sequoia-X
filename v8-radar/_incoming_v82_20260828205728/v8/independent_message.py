from __future__ import annotations

from typing import Any, Mapping


def _cn(value: Any) -> str:
    labels={"LOW":"低","MEDIUM":"中等","HIGH":"高","UNKNOWN":"待确认","OK":"正常",
      "ATTACK":"进攻","NORMAL":"正常","DEFENSE":"防守","STRONG":"强","WEAK":"弱"}
    text=str(value or "待确认")
    return labels.get(text.upper(),text)


def _n(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "待确认"


def build_candidate_message(row: Mapping[str, Any], state: str) -> str:
    styles = {
        "WATCH": ("🟡", "独立候选观察"),
        "CONFIRMED": ("🟢", "建仓条件确认（研究验证）"),
        "WAIT_PULLBACK": ("🟠", "方向通过·买点未通过"),
        "DEFENSE_STRONG": ("🟠", "防守环境·极强候选观察"),
        "BLOCKED": ("🔴", "风险拦截"),
        "EXPIRED": ("⚪", "候选失效"),
    }
    icon, title = styles.get(state, ("🔵", state))
    operation = {
        "WATCH": "继续观察，等待3/6/10分钟持续度、回踩承接和板块扩散确认；不追高。",
        "CONFIRMED": "仅记录研究测试建仓；优先等待回踩承接，价格脱离观察区不追高。",
        "WAIT_PULLBACK": "方向与资金已通过，但当前位置不适合追入；等待回踩承接或二次转强后重新确认。",
        "DEFENSE_STRONG": "个股条件较强，但市场处于防守状态；只记录观察，不新建仓。",
        "BLOCKED": "本轮取消，不买入、不补仓；等待新的独立信号。",
        "EXPIRED": "持续度未通过或结构已消失，本轮结束跟踪。",
    }.get(state, "继续观察。")
    evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
    reasons = row.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    trend_ok = str((evidence.get("daily") or {}).get("status") or "UNKNOWN") == "OK"
    minute_ok = str((evidence.get("minute") or {}).get("status") or "UNKNOWN") == "OK"
    auction_ok = str(row.get("auction_status") or "UNKNOWN") == "OK"
    chip_ok = str(row.get("chip_status") or "UNKNOWN") == "OK"
    lines = [
        f"{icon} A股机会雷达 V8｜{title}", "",
        f"📌 {row.get('name','')} {row.get('code','')}｜{row.get('industry') or '行业待确认'}",
        f"💰 现价 {_n(row.get('price'),2)}｜涨幅 {_n(row.get('pct_chg'),2,'%')}｜成交额 {_n((row.get('amount') or 0)/100000000,2,'亿')}",
        f"⭐ 机会 {_n(row.get('opportunity_score'))}｜初筛 {_n(row.get('discovery_score'))}｜通道 {row.get('channel','独立发现')}",
        f"🌐 市场 {_cn(row.get('market_regime'))} {_n(row.get('market_score'))}｜🏭 板块 {_n(row.get('sector_score'))}",
        f"🎯 买点质量 {_n(row.get('entry_quality_score'))}｜追高风险 {_n(row.get('chase_risk_score'))}",
    ]
    if row.get("entry_low") is not None and row.get("entry_high") is not None:
        lines.append(f"📍 允许观察区 {_n(row.get('entry_low'),2)}—{_n(row.get('entry_high'),2)}｜"
                     f"回踩二次转强 {'已确认' if row.get('pullback_reclaim') else '待确认'}")
    if trend_ok or minute_ok:
        trend_text = _n(row.get('trend_score')) if trend_ok else "待确认"
        fund_text = _n(row.get('fund_score')) if minute_ok else "待确认"
        lines.append(f"📈 趋势 {trend_text}｜💵 资金 {fund_text}｜⏱ 持续度 {_n(row.get('persistence_score'))}")
    else:
        lines.append(f"⏱ 持续度 {_n(row.get('persistence_score'))}｜趋势/分钟资金待确认")
    if auction_ok:
        lines.append(f"🌅 竞价 {_n(row.get('auction_score'))}｜风险 {_cn(row.get('auction_risk'))}")
    if chip_ok:
        lines.append(f"🧩 筹码 {_n(row.get('chip_score'))}｜风险 {_cn(row.get('chip_risk'))}")
    missing = []
    if not trend_ok: missing.append("日线趋势")
    if not minute_ok: missing.append("实时分钟")
    if not auction_ok: missing.append("竞价")
    if not chip_ok: missing.append("筹码")
    if missing:
        lines.append("⚪ 待确认：" + "、".join(missing))
    hard_risk = bool((row.get("evidence") or {}).get("event_context", {}).get("hard_risk"))
    risk_text = "发现硬风险" if hard_risk else "暂未发现硬风险"
    lines += [f"🛡 {risk_text}｜数据 {row.get('source_time') or '待确认'}", "", "👉 操作", operation]
    if reasons:
        lines.append("🔎 依据：" + "；".join(str(x) for x in reasons[:4]))
    if evidence.get("data_limitations"):
        lines.append("⚠ 数据限制：" + "；".join(str(x) for x in evidence["data_limitations"][:2]))
    lines += ["📌 V8独立信号｜不复制6.6/V7｜不自动下单",
              "研究验证阶段｜不构成投资建议"]
    return "\n".join(lines)


def build_scan_status(stats: Mapping[str, Any]) -> str:
    return "\n".join([
        "🔵 A股机会雷达 V8｜独立扫描状态", "",
        f"✅ 独立全市场快照 {stats.get('universe_count',0)}只｜有效 {stats.get('eligible_count',0)}只",
        f"📌 本轮候选 {stats.get('candidate_count',0)}只｜付费复核 {stats.get('enriched_count',0)}只",
        f"🌐 市场 {_cn(stats.get('market_regime'))}｜市场分 {_n(stats.get('market_score'))}",
        f"🔌 行情源 {stats.get('source','')}｜数据时间 {stats.get('source_time','待确认')}", "",
        "👉 操作", "这是运行状态，不是买入信号；具体候选会单独推送。",
        "V8独立运行｜不自动下单",
    ])
