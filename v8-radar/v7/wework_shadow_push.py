from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import CONFIG
from .signal_lab import DEFAULT_DB
from .state_machine import complete_push_attempt, get_push_status, reserve_push_attempt

ROOT_DIR = Path(__file__).resolve().parent.parent
WEBHOOK_FILE = ROOT_DIR / "wework_webhook_v71_shadow.txt"


def _load_webhook(path: Path = WEBHOOK_FILE) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return value if value.startswith("http") else ""


def _post_json(url: str, payload: Mapping[str, Any], timeout: int = 5) -> tuple[bool, str]:
    try:
        data = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return True, body[:300]
    except Exception as exc:
        return False, type(exc).__name__


def _fmt(v: Any, default: str = "-") -> str:
    return default if v in (None, "") else str(v)


def _risk_cn(value: Any) -> str:
    return {
        "LOW": "低", "MEDIUM": "中", "HIGH": "高",
        "HARD_BLOCK": "严重风险", "UNKNOWN": "待确认",
        "DATA_UNAVAILABLE": "数据不可用",
    }.get(str(value or "UNKNOWN").upper(), _fmt(value, "待确认"))


def _minute_cn(value: Any) -> str:
    return {
        "OK": "正常", "BAD": "转弱", "UNKNOWN": "待确认",
        "STALE": "已过期", "DATA_UNAVAILABLE": "数据不可用",
    }.get(str(value or "UNKNOWN").upper(), _fmt(value, "待确认"))


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
            return dict(data) if isinstance(data, Mapping) else {}
        except Exception:
            return {}
    return {}


def _observation_zone(row: Mapping[str, Any]) -> str:
    snapshot = _json_obj(row.get("snapshot_json"))
    candidate = snapshot.get("candidate") if isinstance(snapshot.get("candidate"), Mapping) else {}
    deep = candidate.get("_v64") if isinstance(candidate, Mapping) and isinstance(candidate.get("_v64"), Mapping) else {}
    trade = deep.get("trade") if isinstance(deep, Mapping) and isinstance(deep.get("trade"), Mapping) else {}
    zone = trade.get("zone") if isinstance(trade, Mapping) else None
    return _fmt(zone, "未提供")


def _current_price(paid: Mapping[str, Any]) -> str:
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    daily = paid.get("realtime_daily") if isinstance(paid.get("realtime_daily"), Mapping) else {}
    value = minute.get("last") if isinstance(minute, Mapping) else None
    if value in (None, "") and isinstance(daily, Mapping):
        value = daily.get("current_price")
    return _fmt(value, "未提供")


def _data_time(paid: Mapping[str, Any]) -> str:
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    daily = paid.get("realtime_daily") if isinstance(paid.get("realtime_daily"), Mapping) else {}
    value = minute.get("data_time") if isinstance(minute, Mapping) else None
    if value in (None, "") and isinstance(daily, Mapping):
        value = daily.get("data_time")
    return _fmt(value, "未提供")


def build_state_message(state: str, row: Mapping[str, Any], *, reasons: Sequence[str] | None = None,
                        paid: Mapping[str, Any] | None = None, now: str | None = None) -> str:
    paid = dict(paid or {})
    reasons = list(reasons or [])
    name = _fmt(row.get("name"), "候选")
    code = _fmt(row.get("code"))
    stamp = now or datetime.now().astimezone().isoformat(timespec="seconds")
    ann = paid.get("announcement") if isinstance(paid.get("announcement"), Mapping) else {}
    auction = paid.get("auction") if isinstance(paid.get("auction"), Mapping) else {}
    minute = paid.get("realtime_minute") if isinstance(paid.get("realtime_minute"), Mapping) else {}
    degraded = str(auction.get("status") or "") == "AUCTION_REALTIME_UNAVAILABLE"
    degraded_note = "实时竞价数据未接入，本次为降级确认。" if degraded else ""

    if state == "WATCH":
        return "\n".join([
            "🟡 A股机会雷达 V7.1｜次日观察", "",
            f"{name} {code}",
            f"⭐ V6.6 {_fmt(row.get('v66_score'))}｜V7.1质量 {_fmt(row.get('v71_quality_score'))}｜潜力 {_fmt(row.get('potential_label'),'低')}",
            f"📊 排序 {_fmt(row.get('next_day_potential_score'))}｜公告风险 {_risk_cn(ann.get('announcement_risk_level'))}", "",
            "👉 操作", "仅观察，等待09:35后量价、流动性和板块共同确认，不追高。",
            "🛡 失效：重大风险公告、无法交易、分钟结构恶化或确认窗口结束。",
            f"⚠️ {degraded_note}" if degraded_note else "",
            "📌 结论：当前仅观察，不代表可以买入。",
        ])
    if state == "PREOPEN_CHECK":
        return "\n".join([
            "🟡 A股机会雷达 V7.1｜等待确认", "", f"{name} {code}",
            f"📢 公告风险 {_risk_cn(ann.get('announcement_risk_level'))}｜{'；'.join(reasons or ann.get('announcement_risk_reasons') or ['无明确高风险证据'])}", "",
            "👉 操作", "继续观察，等待09:35实时分钟、日线、流动性和板块确认。",
            f"⚠️ {degraded_note}" if degraded_note else "实时竞价已接入，等待确认。",
            "📌 结论：尚未触发买点，不追高。",
        ])
    if state == "BUY_CONFIRMED":
        trigger = "公告无明确高风险 + 09:35后分钟数据新鲜且结构通过 + 实时日线/流动性/板块条件通过"
        if not degraded:
            trigger = "实时竞价可交易 + 开盘分钟数据新鲜且结构通过 + 实时日线/流动性/板块条件通过"
        return "\n".join([
            "🟢 A股机会雷达 V7.1｜可建仓·买点确认（Shadow）", "", f"{name} {code}",
            f"💰 当前价格：{_current_price(paid)}｜观察价格区间：{_observation_zone(row)}",
            f"🕘 确认时间：{stamp}｜数据时间：{_data_time(paid)}",
            f"⭐ V6.6 {_fmt(row.get('v66_score'))}｜V7.1质量 {_fmt(row.get('v71_quality_score'))}",
            f"✅ 分钟 {_minute_cn(minute.get('quality'))}｜公告风险 {_risk_cn(ann.get('announcement_risk_level'))}", "",
            "【系统状态：首次确认·建立持仓跟踪】",
            f"【系统模拟成本：{_current_price(paid)}｜持有日：D0】",
            "👉 操作", ("允许测试仓建仓；本次为降级确认，若价格已明显拉升则等待回踩，禁止追高。" if degraded
                         else "允许测试仓建仓；若看到消息时价格已明显拉升则等待回踩，禁止追高。"),
            f"🎯 触发依据：{trigger}",
            "🛡 失效条件：价格脱离观察区、分钟转弱、流动性恶化或出现重大风险公告。",
            f"⚠️ {degraded_note}" if degraded_note else "",
            "📌 后续洗盘、转弱、失效和止盈提醒应关联本次信号；仅用于Shadow测试，不构成投资建议。",
        ])
    if state == "CANCELLED":
        return "\n".join([
            "❌ A股机会雷达 V7.1｜信号失效", "", f"{name} {code}",
            f"🕘 取消时间：{stamp}",
            f"🔴 原因：{'；'.join(reasons or ['风险/交易质量条件未通过'])}",
            f"📊 公告时间 {_fmt(ann.get('announcement_latest_time'),'未提供')}｜分钟时间 {_data_time(paid)}", "",
            "👉 操作：已按确认建仓则减仓/退出；尚未成交则取消，不补仓摊低成本。",
            "📌 结论：本轮信号结束跟踪。",
        ])
    if state == "EXPIRED":
        return "\n".join(["⚪ A股机会雷达 V7.1｜候选过期", "", f"{name} {code}", f"🕘 时间：{stamp}",
                          f"原因：{'；'.join(reasons or ['确认窗口结束'])}", "", "📌 结论：当天不再确认买点。"]) 
    return f"【V7.1 Shadow】\n{name} {code}\n状态：{state}\n时间：{stamp}"


def build_evening_review(summary: Mapping[str, Any]) -> str:
    top = summary.get("top_watch") or []
    top_text = "、".join(f"{x.get('name') or x.get('code')}({x.get('potential_label','-')})" for x in top[:5]) or "无"
    return "\n".join([
        "【V7.1每日复盘】",
        f"市场风险：{_fmt(summary.get('market_risk'),'UNKNOWN')}｜强势板块：{_fmt(summary.get('strong_sectors'),'数据未提供')}",
        f"V6.6原始候选：{_fmt(summary.get('v66_count'),0)}｜V7.1通过：{_fmt(summary.get('v71_pass_count'),0)}｜过滤：{_fmt(summary.get('filtered_count'),0)}",
        f"WATCH：{_fmt(summary.get('watch_count'),0)}｜BUY_CONFIRMED：{_fmt(summary.get('buy_confirmed_count'),0)}｜CANCELLED：{_fmt(summary.get('cancelled_count'),0)}",
        f"D0：{_fmt(summary.get('d0'),'暂无成熟数据')}｜D1/D3/D5：{_fmt(summary.get('history_performance'),'暂无成熟数据')}",
        f"次日观察前5：{top_text}",
        f"接口健康：{_fmt(summary.get('api_health'),'UNKNOWN')}",
        f"数据异常：{_fmt(summary.get('data_anomalies'),'无明确异常')}",
        "Shadow复盘仅用于系统验证，不构成投资建议。",
    ])


def send_message(text: str, *, webhook: str | None = None,
                 post_func: Callable[[str, Mapping[str, Any], int], tuple[bool, str]] | None = None) -> tuple[bool, str]:
    if not CONFIG.push_enabled and webhook is None:
        return False, "push_disabled"
    url = webhook or _load_webhook()
    if not url:
        return False, "webhook_missing"
    func = post_func or _post_json
    return func(url, {"msgtype": "text", "text": {"content": text}}, 5)


def send_state_once(row: Mapping[str, Any], state: str, *, reasons: Sequence[str] | None = None,
                    paid: Mapping[str, Any] | None = None, webhook: str | None = None,
                    post_func=None, db_path: Path = DEFAULT_DB) -> tuple[bool, str]:
    """Send a state change with finite persistent retries.

    Dedupe becomes successful only after WeCom reports a successful send. Failed
    attempts are persisted and can be retried until ``V71_PUSH_MAX_ATTEMPTS``.
    """
    if not CONFIG.push_enabled and webhook is None:
        return False, "push_disabled"
    trade_date = str(row.get("trade_date") or datetime.now().date().isoformat())
    code = str(row.get("code") or "").split(".")[0].zfill(6)
    max_attempts = CONFIG.push_max_attempts
    last_detail = "not_attempted"
    while True:
        reserved = reserve_push_attempt(trade_date=trade_date, code=code, state=state,
                                        max_attempts=max_attempts, db_path=db_path)
        if not reserved.get("allowed"):
            return False, str(reserved.get("reason") or "push_not_allowed")
        ok, detail = send_message(build_state_message(state, row, reasons=reasons, paid=paid),
                                  webhook=webhook, post_func=post_func)
        complete_push_attempt(trade_date=trade_date, code=code, state=state, success=ok,
                              error="" if ok else detail, db_path=db_path)
        if ok:
            return True, detail
        last_detail = detail
        status = get_push_status(trade_date=trade_date, code=code, state=state, db_path=db_path) or {}
        if int(status.get("attempt_count") or 0) >= max_attempts:
            return False, f"push_retry_limit_reached:{last_detail}"
        if CONFIG.push_retry_backoff_seconds > 0:
            time.sleep(CONFIG.push_retry_backoff_seconds)

# ---------------- V7.2 Portfolio Shadow additions ----------------
def _portfolio_push_allowed(db_path: Path = DEFAULT_DB) -> bool:
    try:
        from .config import V72_CONFIG
        from .portfolio import get_setting
        return bool(V72_CONFIG.portfolio_push and get_setting('portfolio_push_user_confirmed', False, db_path))
    except Exception:
        return False


def build_portfolio_message(result: Mapping[str, Any]) -> str:
    pos = result.get('position') if isinstance(result.get('position'), Mapping) else {}
    research = result.get('research') if isinstance(result.get('research'), Mapping) else {}
    state = result.get('state') if isinstance(result.get('state'), Mapping) else {}
    cls = result.get('classification') if isinstance(result.get('classification'), Mapping) else {}
    paid = result.get('paid') if isinstance(result.get('paid'), Mapping) else {}
    evidence = result.get('evidence') if isinstance(result.get('evidence'), Mapping) else {}
    ann = paid.get('announcement') if isinstance(paid.get('announcement'), Mapping) else {}
    minute = paid.get('realtime_minute') if isinstance(paid.get('realtime_minute'), Mapping) else {}
    news = paid.get('news') if isinstance(paid.get('news'), Mapping) else {}
    chip = paid.get('chip_cost') if isinstance(paid.get('chip_cost'), Mapping) else {}
    report = paid.get('report') if isinstance(paid.get('report'), Mapping) else {}
    ptype = str(pos.get('position_type') or 'ACTUAL')
    label = ('【实际持仓】' if ptype == 'ACTUAL' and pos.get('status') == 'HOLDING' else ('【模拟仓】' if ptype == 'SIMULATED' else ('【未持仓观察】' if pos.get('status') == 'CLOSED' else '【待确认成交】')))
    cur = result.get('current_price'); cost = pos.get('cost_price'); pnl = result.get('unrealized_pct')
    reasons = cls.get('reasons') or []
    data_time = minute.get('data_time') or '未提供'
    raw_state = str(state.get('current_state') or cls.get('state') or 'NEUTRAL')
    state_cn = {
        'MAIN_RISE': '主升延续', 'WASHOUT': '健康洗盘', 'EXHAUSTION': '主升衰竭',
        'TRUE_WEAKNESS': '真实转弱', 'SECOND_STRENGTH': '二次转强',
        'HARD_RISK': '重大风险', 'NEUTRAL': '正常观察',
    }.get(raw_state, raw_state)
    action = {
        'MAIN_RISE': '继续持有观察；不追涨，按防守位管理。',
        'WASHOUT': '持有观察，暂不加仓；等待承接或重新转强。',
        'EXHAUSTION': '收紧防守，保护利润；可考虑分批减仓。',
        'TRUE_WEAKNESS': '防守优先；减仓/退出，不补仓摊低成本。',
        'SECOND_STRENGTH': '重新进入观察，等待买点确认后再决定，不直接追涨。',
        'HARD_RISK': '重大风险优先处理；停止加仓并检查减仓/退出。',
        'NEUTRAL': '继续观察，证据不足时不做新增操作。',
    }.get(raw_state, '继续观察，等待下一轮确认。')
    confidence_cn = {'HIGH':'高', 'MEDIUM':'中', 'LOW':'低'}.get(str(cls.get('evidence_strength') or 'LOW').upper(), _fmt(cls.get('evidence_strength'),'低'))
    def similarity(value):
        try:
            number = float(value)
            return f"{number * 100:.0f}%" if 0 <= number <= 1 else f"{number:.0f}%"
        except Exception:
            return '数据不足'
    stop = pos.get('manual_stop_price')
    stop_text = _fmt(stop, '未设置；请结合成本、ATR和关键结构确认')
    sector_value = evidence.get('sector_strength')
    sector_text = similarity(sector_value) if sector_value is not None else '数据不足'
    news_text = _risk_cn(news.get('news_risk_level'))
    chip_text = (f"获利盘 {_fmt(chip.get('winner_rate'))}%｜平均成本 {_fmt(chip.get('weight_avg'),'待确认')}"
                 if chip.get('status') not in {'DATA_UNAVAILABLE',None} else '数据不足')
    report_count = report.get('report_count_30d')
    report_text = f"近30日 {report_count}份｜评级变化 {_fmt(report.get('rating_change'),'无明确变化')}" if report_count is not None else '数据不足'
    return '\n'.join([
        f"【V7.2持仓提醒｜{state_cn}】",
        f"{label} {_fmt(pos.get('name'),'持仓')} {_fmt(pos.get('ts_code'))}",
        f"💰 现价 {_fmt(cur,'未提供')}｜成本 {_fmt(cost,'未提供')}｜浮盈亏 {_fmt(round(pnl,2) if isinstance(pnl,(int,float)) else pnl,'未提供')}%",
        f"📍 当前状态：{state_cn}｜证据强度：{confidence_cn}",
        f"📊 主升 {similarity(research.get('main_rise_similarity'))}｜洗盘 {similarity(research.get('washout_similarity'))}｜真跌风险 {similarity(research.get('true_decline_risk_similarity'))}",
        f"📰 公告风险：{_risk_cn(ann.get('announcement_risk_level'))}｜分钟数据：{_minute_cn(minute.get('quality') or minute.get('freshness_status'))}",
        f"🏭 板块强度：{sector_text}｜新闻风险：{news_text}",
        f"🧩 筹码参考：{chip_text}",
        f"📚 研报参考：{report_text}",
        f"🕘 数据时间：{data_time}", "",
        "👉 操作", action,
        f"🛡 防守参考：{stop_text}",
        f"🔎 判断依据：{'；'.join(str(x) for x in reasons) if reasons else '当前证据不足，继续观察'}",
        '📌 系统不会自动下单；请按实际成交和风险承受能力执行。'
    ])


def _portfolio_rate_check(position_id: str, state: str, *, trade_date: str, db_path: Path, hard_risk: bool = False) -> tuple[bool, str]:
    from .config import V72_CONFIG
    from .portfolio import ensure_schema
    ensure_schema(db_path)
    from .signal_lab import _LOCK, _connect
    from datetime import datetime
    with _LOCK:
        c=_connect(db_path)
        rows=c.execute('select * from v72_portfolio_push_log where trade_date=? and position_id=? order by id desc',(trade_date,position_id)).fetchall()
        success_rows=[r for r in rows if int(r['success'] or 0)==1]
        cap=V72_CONFIG.portfolio_single_stock_push_limit + (2 if hard_risk else 0)
        if len(success_rows) >= cap:
            c.close(); return False,'single_stock_daily_limit'
        state_failures=sum(1 for r in rows if r['state']==state and int(r['success'] or 0)==0)
        if state_failures >= CONFIG.push_max_attempts:
            c.close(); return False,'push_retry_limit_reached'
        now=datetime.now().astimezone()
        if success_rows and not hard_risk:
            try:
                last=datetime.fromisoformat(success_rows[0]['attempted_at'])
                if (now-last).total_seconds() < V72_CONFIG.portfolio_state_cooldown_minutes*60 and success_rows[0]['state']==state:
                    c.close(); return False,'state_cooldown'
            except Exception:
                pass
        global_row=c.execute('select attempted_at from v72_portfolio_push_log where success=1 order by id desc limit 1').fetchone()
        if global_row and V72_CONFIG.portfolio_global_cooldown_seconds>0 and not hard_risk:
            try:
                glast=datetime.fromisoformat(global_row['attempted_at'])
                if (now-glast).total_seconds() < V72_CONFIG.portfolio_global_cooldown_seconds:
                    c.close(); return False,'global_cooldown'
            except Exception:
                pass
        c.close(); return True,'allowed'


def send_portfolio_state(result: Mapping[str, Any], *, sender: Callable[[str, Mapping[str, Any]], tuple[bool,str]] | None = None,
                         db_path: Path = DEFAULT_DB) -> dict[str, Any]:
    """Send one Portfolio attempt. Failed attempts remain retryable until the finite cap."""
    if not _portfolio_push_allowed(db_path):
        return {'sent':False,'reason':'portfolio_push_not_confirmed'}
    pos=result.get('position') if isinstance(result.get('position'),Mapping) else {}
    st=result.get('state') if isinstance(result.get('state'),Mapping) else {}
    pid=str(pos.get('position_id') or ''); state=str(st.get('current_state') or st.get('to_state') or 'NEUTRAL'); day=datetime.now().date().isoformat()
    hard_risk=state in {'HARD_RISK'}
    allowed,reason=_portfolio_rate_check(pid,state,trade_date=day,db_path=db_path,hard_risk=hard_risk)
    if not allowed:return {'sent':False,'reason':reason}
    webhook=_load_webhook()
    if not webhook:return {'sent':False,'reason':'missing_v7_shadow_webhook'}
    message=build_portfolio_message(result); payload={'msgtype':'text','text':{'content':message}}
    fn=sender or (lambda url,p:_post_json(url,p,timeout=CONFIG.request_timeout_seconds))
    ok,detail=fn(webhook,payload)
    from .portfolio import ensure_schema
    ensure_schema(db_path)
    from .signal_lab import _LOCK,_connect
    with _LOCK:
        c=_connect(db_path); c.execute('insert into v72_portfolio_push_log(trade_date,position_id,state,attempted_at,success,error) values(?,?,?,?,?,?)',
                                      (day,pid,state,datetime.now().astimezone().isoformat(timespec='microseconds'),int(bool(ok)),'' if ok else str(detail)[:160])); c.commit(); c.close()
    if not ok:
        try:
            from .runtime_log_v72 import log_event
            log_event('wework_portfolio',f'持仓微信发送失败：{detail}',code=str(pos.get('ts_code') or ''),degraded=True,event='wework_send')
        except Exception:
            pass
    return {'sent':bool(ok),'reason':'sent' if ok else 'send_failed','detail':detail}

def build_v72_evening_review_message(review: Mapping[str, Any]) -> str:
    p=review.get('portfolio') if isinstance(review.get('portfolio'),Mapping) else {}
    f=review.get('forward') if isinstance(review.get('forward'),Mapping) else {}
    r=review.get('research') if isinstance(review.get('research'),Mapping) else {}
    ss=review.get('second_strength') if isinstance(review.get('second_strength'),Mapping) else {}
    health=review.get('api_health') if isinstance(review.get('api_health'),Mapping) else {}
    errs=review.get('runtime_errors') if isinstance(review.get('runtime_errors'),Mapping) else {}
    d3=(f.get('by_horizon') or {}).get('3',{}) if isinstance(f.get('by_horizon'),Mapping) else {}
    d5=(f.get('by_horizon') or {}).get('5',{}) if isinstance(f.get('by_horizon'),Mapping) else {}
    states = p.get('states',{}) if isinstance(p.get('states'),Mapping) else {}
    state_names = {'MAIN_RISE':'主升延续','WASHOUT':'健康洗盘','EXHAUSTION':'主升衰竭','TRUE_WEAKNESS':'真实转弱','SECOND_STRENGTH':'二次转强','HARD_RISK':'重大风险','NEUTRAL':'正常观察'}
    state_text = '｜'.join(f"{state_names.get(str(k),k)} {v}" for k,v in states.items()) or '暂无持仓状态'
    critical = health.get('critical',{}) if isinstance(health.get('critical'),Mapping) else {}
    api_names = {'rt_k':'实时日K','rt_min':'实时分钟','trade_cal':'交易日','stk_auction_tick':'竞价Tick'}
    api_text = '｜'.join(f"{api_names.get(str(k),k)} {v}" for k,v in critical.items()) or '暂无接口明细'
    modules = errs.get('by_module',{}) if isinstance(errs.get('by_module'),Mapping) else {}
    error_text = '｜'.join(f"{k} {v}" for k,v in modules.items()) or '无'
    second = ss.get('SECOND_STRENGTH',{}) if isinstance(ss.get('SECOND_STRENGTH'),Mapping) else {}
    second_text = f"成熟 {second.get('n',0)}条" if second else '暂无成熟结果'
    return '\n'.join([
      '【V7.2每日复盘｜Research Shadow】',
      f"📦 实际持仓 {p.get('holding',0)}｜模拟仓 {p.get('simulated',0)}｜待确认 {p.get('pending_fill',0)}",
      f"📍 持仓状态：{state_text}",
      f"🔬 Research记录 {r.get('n',0)}条｜当前仅作后台验证",
      f"📈 Forward成熟 {f.get('matured',0)}｜未成熟 {f.get('immature',0)}",
      f"D3净收益：{_fmt(d3.get('avg_net_return_pct'),'未成熟/无数据')}｜成熟样本 {d3.get('n',0)}",
      f"D5净收益：{_fmt(d5.get('avg_net_return_pct'),'未成熟/无数据')}｜成熟样本 {d5.get('n',0)}",
      f"🔁 二次转强：{second_text}",
      f"🔌 接口状态：{health.get('status','待确认')}｜{api_text}",
      f"⚠️ 当日运行错误 {errs.get('total',0)}｜模块：{error_text}", "",
      "👉 明日操作", "只处理已经确认的持仓状态；观察票继续等待买点确认，不因Research相似度直接买入。",
      '📌 未成熟样本不计入胜率，系统不会自动下单。'
    ])


def send_v72_evening_review(review: Mapping[str, Any], *, sender: Callable[[str, Mapping[str, Any]], tuple[bool,str]] | None=None) -> dict[str,Any]:
    try:
        from .config import V72_CONFIG
        if not V72_CONFIG.research_push:return {'sent':False,'reason':'research_push_disabled'}
    except Exception:return {'sent':False,'reason':'config_unavailable'}
    webhook=_load_webhook()
    if not webhook:return {'sent':False,'reason':'missing_v7_shadow_webhook'}
    payload={'msgtype':'text','text':{'content':build_v72_evening_review_message(review)}}
    fn=sender or (lambda url,p:_post_json(url,p,timeout=CONFIG.request_timeout_seconds))
    last='not_attempted'
    for _ in range(CONFIG.push_max_attempts):
        ok,detail=fn(webhook,payload); last=detail
        if ok:return {'sent':True,'reason':'sent','detail':detail}
        try:
            from .runtime_log_v72 import log_event
            log_event('wework_evening_review',f'晚间复盘微信发送失败：{detail}',degraded=True,event='wework_send')
        except Exception:
            pass
        if CONFIG.push_retry_backoff_seconds>0:time.sleep(CONFIG.push_retry_backoff_seconds)
    return {'sent':False,'reason':'push_retry_limit_reached','detail':last}
