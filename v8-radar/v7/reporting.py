from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .performance_tracker import comparison_stats
from .signal_lab import DEFAULT_DB, compare_summary, _connect, _LOCK

ROOT_DIR=Path(__file__).resolve().parent.parent
REPORT_DIR=ROOT_DIR/"reports_v7"
ENGINES=("V6_LEGACY","V7_SHADOW","V6_V7_FILTERED")


def _fmt_num(v, digits=2):
    try:
        if v==float("inf"): return "∞"
        return f"{float(v):.{digits}f}"
    except Exception: return "0"


def build_comparison_report(*, signal_date: Optional[str]=None, db_path: Path=DEFAULT_DB) -> str:
    day=signal_date or datetime.now().date().isoformat()
    signal_rows={r["engine"]:r for r in compare_summary(day,db_path=db_path)}
    lines=[f"# A股机会雷达 三组对照报告｜{day}","",
           "> V7.1-shadow-enhanced 规则冻结。V7.1 仍为 Shadow Mode，不替代 V6.6 正式信号。","",
           "## 当日信号","" ]
    for engine in ENGINES:
        r=signal_rows.get(engine,{})
        lines.append(f"- {engine}: {int(r.get('signals') or 0)} 个｜平均评分 {float(r.get('avg_score') or 0):.2f}｜版本 {r.get('strategy_version') or '-'}")

    for h in ("D1","D3","D5","D10"):
        stats=comparison_stats(horizon=h,signal_date=day,db_path=db_path)
        if not stats: continue
        lines += ["",f"## {h} 完整持有路径统计",""]
        for r in stats:
            lines.append(
                f"- {r['engine']}: 样本{r['samples']}（可判定路径{r.get('mature_strategy_samples',0)}）｜路径胜率{r['win_rate_pct']:.1f}%｜均值{r['avg_return_pct']:+.2f}%｜"
                f"中位数{r['median_return_pct']:+.2f}%｜净期望{r['expectancy_pct']:+.2f}%｜盈亏比{_fmt_num(r['payoff_ratio'])}｜"
                f"利润因子{_fmt_num(r['profit_factor'])}｜平均最大回撤{r['avg_max_drawdown_pct']:+.2f}%｜"
                f"止损{r['stop_rate_pct']:.1f}%｜目标1 {r['target1_rate_pct']:.1f}%｜同日冲突{r['ambiguous_rate_pct']:.1f}%｜"
                f"最大单笔盈利贡献{r['top_profit_contribution_pct']:.1f}%")

    only_v6,only_v7,both=_engine_overlap(day,db_path)
    filt=_filter_value(day,db_path)
    lines += ["","## 引擎差异","",f"- 仅 V6：{only_v6} 个股票/来源组合",f"- 仅 V7：{only_v7} 个股票/来源组合",
              f"- V6/V7 同时出现：{both} 个股票/来源组合","",
              "## V7 风险过滤价值","",
              f"- V6 原始信号：{filt['v6_total']} 个",f"- 通过 V7 风险过滤：{filt['filtered_total']} 个",
              f"- 被过滤：{filt['rejected_total']} 个",f"- 过滤率：{filt['filter_rate_pct']:.1f}%"]
    if filt.get("rejected_d5_samples"):
        lines += [f"- 被过滤信号 D5 已成熟样本：{filt['rejected_d5_samples']} 个",
                  f"- 被过滤信号 D5 平均收益：{filt['rejected_d5_avg_return_pct']:+.2f}%",
                  f"- 被过滤信号 D5 正收益占比（错杀观察）：{filt['rejected_d5_positive_pct']:.1f}%",
                  f"- 被过滤信号 D5 >5% 大涨占比（机会损失）：{filt['rejected_d5_big_winner_pct']:.1f}%"]
    lines += ["","## 说明","",
              "- D3/D5/D10 的最高收益、最低收益、止盈/止损均按信号后第1日至第N个交易日的完整路径计算。",
              "- 同一日同时触发止盈和止损时先标记 AMBIGUOUS；接入分钟历史后可按分钟顺序消歧。",
              "- DATA_UNAVAILABLE 不按0分处理，也不会因缺失付费数据被误否决。",
              "- 不同 strategy_version 的样本不应混在一起做策略优劣结论。",""]
    return "\n".join(lines)


def _engine_overlap(day: str, db_path: Path):
    with _LOCK:
        conn=_connect(db_path)
        try: rows=conn.execute("SELECT engine,code,source FROM signal_events WHERE trade_date=? GROUP BY engine,code,source",(day,)).fetchall()
        finally: conn.close()
    v6={(r["code"],r["source"]) for r in rows if r["engine"]=="V6_LEGACY"}
    v7={(r["code"],r["source"]) for r in rows if r["engine"]=="V7_SHADOW"}
    return len(v6-v7),len(v7-v6),len(v6&v7)


def _filter_value(day: str, db_path: Path):
    with _LOCK:
        conn=_connect(db_path)
        try:
            rows=conn.execute("SELECT engine,code,source,id FROM signal_events WHERE trade_date=?",(day,)).fetchall()
            v6={(r["code"],r["source"]):int(r["id"]) for r in rows if r["engine"]=="V6_LEGACY"}
            filt={(r["code"],r["source"]) for r in rows if r["engine"]=="V6_V7_FILTERED"}
            rejected=set(v6)-filt
            vals=[]
            for key in rejected:
                row=conn.execute("SELECT close_return_pct FROM signal_outcomes WHERE signal_id=? AND horizon='D5'",(v6[key],)).fetchone()
                if row and row["close_return_pct"] is not None: vals.append(float(row["close_return_pct"]))
        finally: conn.close()
    total=len(v6); rejected_n=len(rejected)
    return {"v6_total":total,"filtered_total":len(filt),"rejected_total":rejected_n,
            "filter_rate_pct":rejected_n/total*100 if total else 0,
            "rejected_d5_samples":len(vals),"rejected_d5_avg_return_pct":sum(vals)/len(vals) if vals else 0,
            "rejected_d5_positive_pct":sum(1 for x in vals if x>0)/len(vals)*100 if vals else 0,
            "rejected_d5_big_winner_pct":sum(1 for x in vals if x>=5.0)/len(vals)*100 if vals else 0}


def write_comparison_report(*, signal_date: Optional[str]=None, db_path: Path=DEFAULT_DB) -> str:
    day=signal_date or datetime.now().date().isoformat(); REPORT_DIR.mkdir(parents=True,exist_ok=True)
    path=REPORT_DIR/f"V6_vs_V7_{day}.md"; path.write_text(build_comparison_report(signal_date=day,db_path=db_path),encoding="utf-8")
    return str(path)


def build_evening_review_data(*, trade_date: Optional[str]=None, db_path: Path=DEFAULT_DB) -> dict:
    day=trade_date or datetime.now().date().isoformat()
    with _LOCK:
        conn=_connect(db_path)
        try:
            state_rows=conn.execute("SELECT state,COUNT(*) n FROM v71_candidate_states WHERE trade_date=? GROUP BY state",(day,)).fetchall()
            counts={r["state"]:int(r["n"]) for r in state_rows}
            top=[dict(r) for r in conn.execute("SELECT code,name,potential_label,next_day_potential_score FROM v71_candidate_states WHERE trade_date=? AND state IN ('WATCH','PREOPEN_CHECK') ORDER BY next_day_potential_score DESC LIMIT 5",(day,)).fetchall()]
            sig=conn.execute("SELECT engine,COUNT(*) n FROM signal_events WHERE trade_date=? GROUP BY engine",(day,)).fetchall()
            sig_counts={r["engine"]:int(r["n"]) for r in sig}
            dq=conn.execute("SELECT dataset,status,is_complete,details_json FROM data_quality_runs WHERE requested_trade_date=? ORDER BY id DESC LIMIT 5",(day,)).fetchall()
        finally: conn.close()
    perf=[]
    for h in ("D1","D3","D5"):
        rows=comparison_stats(horizon=h,signal_date=day,db_path=db_path)
        if rows: perf.append({"horizon":h,"rows":rows})
    anomalies=[]
    for r in dq:
        if not int(r["is_complete"] or 0): anomalies.append(f"{r['dataset']}:{r['status']}")
    return {
        "market_risk":"UNKNOWN",
        "strong_sectors":"需由市场层提供",
        "v66_count":sig_counts.get("V6_LEGACY",0),
        "v71_pass_count":sig_counts.get("V6_V7_FILTERED",0),
        "filtered_count":max(0,sig_counts.get("V6_LEGACY",0)-sig_counts.get("V6_V7_FILTERED",0)),
        "watch_count":counts.get("WATCH",0)+counts.get("PREOPEN_CHECK",0),
        "buy_confirmed_count":counts.get("BUY_CONFIRMED",0),
        "cancelled_count":counts.get("CANCELLED",0),
        "d0":"见signal_outcomes D0",
        "history_performance":perf,
        "top_watch":top,
        "api_health":"见data_v7/api_health.json",
        "data_anomalies":"；".join(anomalies) if anomalies else "无明确异常",
    }
