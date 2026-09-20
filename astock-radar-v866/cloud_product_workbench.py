"""Read-only product views built from a consistent cloud quote snapshot.

Discovery is not promoted into a trade signal. No provider calls, orders,
portfolio writes, configuration secrets or notification side effects here.
"""
import json
import math
import re
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from runtime_freshness import quote_guard

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
_cache = (0, None)
_lock = threading.Lock()
TITLES = {
    "overview": "市场总览", "tickets": "全天候选", "a_exec": "策略评估",
    "limitup": "涨停与板块", "sniper": "A+验证进度", "sim": "发现时间线",
    "stock_detail": "个股证据", "review": "当日复盘", "health": "数据健康",
    "sources": "数据与推送", "meta": "历史信号表现", "cpcv": "样本外检验",
    "conformal": "概率校准", "factor": "发现因子", "mc": "风险压力",
    "drift": "稳定性", "upgrade": "产品运行状态", "portfolio": "我的持仓",
}


def text(value, fallback="未提供"):
    return escape(str(fallback if value is None or value == "" else value))


def num(value):
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (ValueError, TypeError):
        return None


def fmt(value, digits=2):
    value = num(value)
    return "未提供" if value is None else f"{value:,.{digits}f}"


def change(value):
    value = num(value)
    if value is None:
        return '<span class="pd-muted">缺少报价</span>'
    cls = "pd-up" if value > 0 else "pd-down" if value < 0 else "pd-flat"
    return f'<b class="{cls}">{value:+.2f}%</b>'


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
    except (ValueError, TypeError):
        return None


def freshness(snapshot, now=None):
    now = now or datetime.now(TZ)
    received = stamp(snapshot.get("observed_at"))
    if not received:
        return "数据时间未知", False
    age = (now - received).total_seconds()
    if age < -60:
        return "数据时间异常", False
    if str(snapshot.get("trade_date")) != now.strftime("%Y%m%d"):
        return "历史快照 · 等待新交易日数据", False
    hhmm = now.strftime("%H:%M")
    in_session = now.weekday() < 5 and ("09:15" <= hhmm <= "11:30" or "13:00" <= hhmm <= "15:00")
    if in_session:
        valid, reason = quote_guard(snapshot, now)
        if not valid:
            return "行情时效或质量异常 · 暂停策略判断", False
        if reason == 'RECEIPT_ONLY_SOURCE_TIME_UNVERIFIED':
            return "采集接收更新中 · 行情源时间未核验", False
        return "盘中行情时间已核验", True
    if hhmm > "15:00":
        return "盘后最新快照 · 收盘价待核验", False
    return "非连续交易时段 · 保留最近快照", False


def link(page, label, code=""):
    query = {"page": page, "mode": "LIVE"}
    if code:
        query["code"] = code
    return f'<a href="/?{escape(urlencode(query), quote=True)}">{text(label)}</a>'


def panel(title, body):
    return f'<section class="pd-panel"><h2>{text(title)}</h2>{body}</section>'


def metric(label, value, detail, target):
    return f'<a class="pd-metric" href="/?page={target}&amp;mode=LIVE"><span>{text(label)}</span><b>{text(value)}</b><small>{text(detail)} →</small></a>'


def note(message):
    return '<p class="pd-note">' + text(message) + '</p>'


def discovery_evidence(raw):
    try:
        evidence = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(evidence, dict):
            evidence = {}
    except (TypeError, ValueError):
        evidence = {}
    labels = (
        ('amount_acceleration_rank', '成交额加速排名值'),
        ('vwap_strength_rank', '相对成交均价强度排名值'),
        ('persistence_rank', '持续性排名值'),
        ('sector_diffusion_rank', '板块扩散排名值'),
    )
    facts = []
    missing = 0
    for key, label in labels:
        value = num(evidence.get(key))
        if value is None:
            missing += 1
        facts.append('<div><dt>'+text(label)+'</dt><dd>'+
                     (fmt(value, 1) if value is not None else '暂无数据，未参与本项判断')+'</dd></div>')
    states = {'WAIT_FIRST_BREAKOUT': '等待首次突破',
              'WAIT_PULLBACK': '等待回踩',
              'WAIT_SECOND_STRENGTH': '等待二次转强',
              'SECOND_STRENGTH': '二次转强状态记录',
              'BREAKOUT': '突破状态记录',
              'INVALIDATED': '本轮观察已失效'}
    state = states.get(str(evidence.get('sequence_state') or ''), '暂无可解释的阶段记录')
    facts.append('<div><dt>当前观察阶段</dt><dd>'+text(state)+'</dd></div>')
    explanation = '这是首次发现时保存的证据，不是当前实时判断，也不是买入确认。排名值不等于涨幅、上涨家数占比或胜率。'
    if missing:
        explanation += f' 有 {missing} 项排名数据缺失，不能据此认定资金、持续性等条件已经通过。'
    return note(explanation)+'<dl class="pd-facts">'+''.join(facts)+'</dl>'


def read_json(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def connection(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    return conn


def load_data():
    global _cache
    with _lock:
        if _cache[1] is not None and time.monotonic() - _cache[0] < 12:
            return _cache[1]
        result = {"snapshot": {}, "quotes": [], "by_code": {}, "first": {}, "errors": [], "analysis": {}, "legacy": None, "positions": None}
        path = ROOT / "data" / "p0_intraday_pit.db"
        try:
            with closing(connection(path)) as conn:
                row = conn.execute("SELECT * FROM snapshot_manifest WHERE row_count>0 ORDER BY observed_at DESC,snapshot_id DESC LIMIT 1").fetchone()
                if row:
                    result["snapshot"] = dict(row)
                    result["quotes"] = [dict(r) for r in conn.execute("SELECT * FROM intraday_quote WHERE snapshot_id=? ORDER BY CASE WHEN market_rank IS NULL THEN 1 ELSE 0 END,market_rank,pct_chg DESC", (row["snapshot_id"],))]
                    result["by_code"] = {r["ts_code"]: r for r in result["quotes"]}
                    # Retain first discovery across the whole trading day, not just
                    # candidates emitted in the latest collector iteration.
                    query = """WITH first_times AS (
                        SELECT ts_code,MIN(observed_at) AS first_at FROM discovery_observation
                        WHERE trade_date=? GROUP BY ts_code)
                        SELECT d.* FROM discovery_observation d JOIN first_times f
                        ON d.ts_code=f.ts_code AND d.observed_at=f.first_at
                        WHERE d.trade_date=? ORDER BY d.ts_code,d.discovery_type"""
                    for r in conn.execute(query, (row["trade_date"], row["trade_date"])):
                        item = dict(r)
                        result["first"].setdefault(item["ts_code"], item)
        except (sqlite3.Error, OSError) as exc:
            result["errors"].append("盘中数据库读取失败：" + type(exc).__name__)
        result["analysis"] = read_json(ROOT / "data" / "live_analysis.json")
        try:
            with closing(connection(ROOT / "data" / "portfolio_monitor.db")) as conn:
                result["positions"] = conn.execute("SELECT COUNT(*) FROM portfolio_position WHERE quantity>0").fetchone()[0]
        except (sqlite3.Error, OSError):
            pass
        legacy = Path("/opt/v8-radar/data_v8/signal_lab_v8.sqlite3")
        if legacy.exists():
            try:
                with closing(connection(legacy)) as conn:
                    total = conn.execute("SELECT COUNT(DISTINCT event_key) FROM v8_signal_decisions WHERE action='SHADOW_ENTRY_CONFIRMED'").fetchone()[0]
                    rows = conn.execute("""SELECT o.horizon,COUNT(*) n,
                        SUM(CASE WHEN o.net_return_pct>0 THEN 1 ELSE 0 END) wins,AVG(o.net_return_pct) average
                        FROM v8_execution_outcomes o JOIN v8_signal_decisions d ON d.event_key=o.event_key
                        WHERE d.action='SHADOW_ENTRY_CONFIRMED' AND o.track='FIXED'
                        AND o.matured=1 AND o.net_return_pct IS NOT NULL GROUP BY o.horizon ORDER BY o.horizon""").fetchall()
                    result["legacy"] = {"total": total, "rows": [dict(r) for r in rows]}
            except sqlite3.Error:
                result["errors"].append("旧V8结果暂时无法读取")
        _cache = (time.monotonic(), result)
        return result


def sectors(data):
    grouped = defaultdict(list)
    for item in data["quotes"]:
        if num(item.get("pct_chg")) is not None:
            grouped[item.get("industry") or "未分类"].append(float(item["pct_chg"]))
    return sorted([(name, len(v), sum(v)/len(v), 100*sum(x>0 for x in v)/len(v)) for name,v in grouped.items()], key=lambda r:r[2], reverse=True)


def board_estimate(item):
    """Approximation only; IPO and special arrangements need official metadata."""
    previous, price = num(item.get("pre_close")), num(item.get("close"))
    if previous is None or previous <= 0 or price is None:
        return None
    code = str(item.get("ts_code", ""))
    if code.endswith(".BJ"):
        rate = .30
    elif code.startswith(("300", "301", "688", "689")):
        rate = .20
    elif code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        rate = .05 if "ST" in str(item.get("name", "")).upper() else .10
    else:
        return None
    upper = float((Decimal(str(previous))*(Decimal(1)+Decimal(str(rate)))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
    lower = float((Decimal(str(previous))*(Decimal(1)-Decimal(str(rate)))).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
    high = num(item.get("high"))
    return {"upper": upper, "lower": lower, "near_up": abs(price-upper)<=.011,
            "near_down": abs(price-lower)<=.011, "opened": high is not None and high>=upper-.011 and price<upper-.011}


def pool(data):
    rows = []
    for code, first in data["first"].items():
        quote = data["by_code"].get(code, {})
        item = dict(quote)
        item.update(ts_code=code, first=first)
        rows.append(item)
    return sorted(rows, key=lambda r: (r["first"].get("observed_at", ""), r["ts_code"]))


def stock_table(rows, data, limit=200):
    if not rows:
        return note("当前范围没有记录。可以查看市场报价与数据健康，确认采集及发现流程。")
    body = []
    for item in rows[:limit]:
        code = item.get("ts_code", "")
        first = item.get("first") or data["first"].get(code) or {}
        baseline, current = num(first.get("price")), num(item.get("close"))
        after = (current/baseline-1)*100 if baseline and baseline>0 and current is not None else None
        quote_name = item.get("name") or code
        estimate = board_estimate(item)
        state = "全天发现 · 尚非交易确认" if first else "市场报价 · 非发现信号"
        if current is None:
            state = "最新快照缺少该股报价"
        elif estimate and estimate["near_up"]:
            state += " · 接近涨幅上限（估算）"
        body.append('<tr><td>'+link("stock_detail",quote_name,code)+f'<small>{text(code)} · {text(item.get("industry"))}</small></td>'
            +f'<td>{text(str(first.get("observed_at", "尚无发现记录")).replace("T"," "))}</td>'
            +f'<td class="pd-base">{fmt(baseline)}<small>{change(first.get("pct_chg")) if first else "未记录"}</small></td>'
            +f'<td>{fmt(current)}<small>{change(item.get("pct_chg"))}</small></td>'
            +f'<td class="pd-after">{change(after) if first else "不适用"}</td><td>{text(state)}</td><td>'+link("stock_detail","查看依据",code)+'</td></tr>')
    return '<div class="pd-table-wrap"><table class="pd-table pd-searchable"><thead><tr><th>股票 / 板块</th><th>首次发现时间</th><th>发现价 / 当时涨幅</th><th>最新价 / 当日涨幅</th><th>发现后实际涨跌</th><th>记录性质</th><th>详情</th></tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'


def search():
    return '<label class="pd-search">查找股票、代码或板块 <input id="pd-search" type="search" placeholder="例如：英维克、002837、半导体" autocomplete="off"></label><p id="pd-search-empty" hidden>当前筛选没有匹配股票，请清除关键词。</p>'


def sector_table(data):
    rows = ''.join(f'<tr><td>{text(name)}</td><td>{count}</td><td>{change(avg)}</td><td>{ratio:.1f}%</td></tr>' for name,count,avg,ratio in sectors(data))
    return '<div class="pd-table-wrap"><table class="pd-table pd-searchable"><thead><tr><th>行业</th><th>报价股票数</th><th>平均涨跌</th><th>上涨家数占比</th></tr></thead><tbody>'+rows+'</tbody></table></div>' if rows else note("当前快照没有可计算行业扩散的记录。")


def analysis_candidates(data):
    return data["analysis"].get("candidates") or []


def analysis_current(data):
    analysis = data["analysis"]
    generated = stamp(analysis.get("generated_at"))
    snapshot = stamp(data["snapshot"].get("observed_at"))
    snapshot_match=analysis.get('input_snapshot_id')==data['snapshot'].get('snapshot_id') and bool(analysis.get('input_snapshot_id'))
    return bool(snapshot_match and generated and snapshot and abs((snapshot-generated).total_seconds()) <= 180 and generated.date()==snapshot.date())


def assessment_table(data):
    industry={row[0]:row[3] for row in sectors(data)}
    rows=[]
    for item in pool(data):
        current,average=num(item.get('close')),num(item.get('vwap'))
        first=num(item['first'].get('price'))
        since=(current/first-1)*100 if current is not None and first and first>0 else None
        relative=(current/average-1)*100 if current is not None and average and average>0 else None
        breadth=industry.get(item.get('industry'))
        facts=[]
        if since is not None: facts.append('发现后走强' if since>0 else '发现后回落' if since<0 else '发现后持平')
        if relative is not None: facts.append('均价上方' if relative>0 else '均价下方' if relative<0 else '接近均价')
        rows.append('<tr><td>'+link('stock_detail',item.get('name') or item['ts_code'],item['ts_code'])+'<small>'+text(item['ts_code'])+'</small></td>'
            +'<td>'+change(since)+'</td><td>'+change(relative)+'</td><td>'+(f'{breadth:.1f}%' if breadth is not None else '板块未提供')+'</td>'
            +'<td>'+text('；'.join(facts) or '报价缺失，无法判断')+'</td><td>'+link('stock_detail','核对原始依据',item['ts_code'])+'</td></tr>')
    return note('这些是同一行情快照中的可观测事实，不等同于洗盘、主升或买入确认。')+'<div class="pd-table-wrap"><table class="pd-table pd-searchable"><thead><tr><th>股票</th><th>发现后涨跌</th><th>相对成交均价</th><th>行业上涨家数占比</th><th>当前证据</th><th>详情</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></div>' if rows else note('当日尚无发现记录可评估。')


def validation(data, status):
    candidates = analysis_candidates(data)
    top = next((r for r in candidates if r.get("pool")=="A"), None)
    current = analysis_current(data)
    passed = bool(top and current)
    checks = [
        ("行情记录", "已接通" if data["quotes"] else "数据缺失", "当前快照覆盖 %s 只股票" % len(data["quotes"])),
        ("个股策略评估", "已有当时A级评估" if passed else "待评估", "现有分析文件未形成与当前行情同时点的A级评估" if not passed else "只代表规则通过，仍需核对当时执行条件"),
        ("历史有效样本", "待验证", "查看历史信号表现；研究样本不足不影响候选和持仓展示"),
        ("样本外检验", "未完成", "没有可核验的样本外通过记录"),
        ("概率校准", "未完成", "未接通经校准的概率输出，不展示推测胜率"),
        ("个股风险核验", "待评估" if not passed else "有风险否决" if top.get("veto") else "需补事件证据", "没有当前个股评估，不能声称已排除风险" if not passed else str(top.get("veto") or "未触发当前规则不等于已覆盖公告、新闻及全部风险")),
    ]
    rows=''.join(f'<tr><td>{text(a)}</td><td><span class="pd-status">{text(b)}</span></td><td>{text(c)}</td></tr>' for a,b,c in checks)
    return panel("A+验证进度", note("A+尚未具备启用证据。你仍可正常查看全天候选、个股证据和持仓提醒。")+'<table class="pd-table"><tr><th>环节</th><th>真实状态</th><th>原因</th></tr>'+rows+'</table>'+link("meta","查看已有历史结果"))


def legacy_panel(data):
    legacy=data["legacy"]
    if legacy is None:
        return panel("旧V8确认信号",note("当前部署未接通旧V8数据库，无法读取历史结果；不是零条记录。"))
    rows=''.join(f'<tr><td>{text(r["horizon"])}</td><td>{r["n"]}</td><td>{r["wins"]}</td><td>{100*r["wins"]/r["n"]:.2f}%</td><td>{change(r["average"])}</td></tr>' for r in legacy["rows"])
    return panel("旧V8建仓确认 · 历史对照",note(f'共 {legacy["total"]} 条独立确认信号。下面为各周期已成熟的固定周期模拟净收益，不是新版胜率或真实账户收益。')+'<table class="pd-table"><tr><th>持有周期</th><th>成熟样本</th><th>净盈利</th><th>正收益率</th><th>平均净收益</th></tr>'+rows+'</table>')


def diagnostics(data, status):
    snap=data["snapshot"]
    state, fresh=freshness(snap)
    issues=note("；".join(data["errors"])) if data["errors"] else ""
    analysis=data["analysis"]
    rows=[("报价读取",str(len(data["quotes"]))+" 只", "清单直接来自云端盘中数据库"),
          ("全天发现",str(len(data["first"]))+" 只", "跨轮次保留当日首次发现，不以本轮数量代替全天数量"),
          ("数据时间",str(snap.get("observed_at") or "未提供"),state),
          ("行情源时间",str(snap.get("source_trade_time") or "未返回"),"行情源未返回时，接收时间不能冒充交易所时间"),
          ("策略分析", "与当前快照同时点" if analysis_current(data) else "未生成或已过期",str(analysis.get("generated_at") or "没有分析文件时间")),
          ("页面刷新","60秒","持仓编辑与搜索过程中不自动打断"),
          ("数据源",str(snap.get("source") or "未提供"),"当前页面不调用付费接口，仅读取已采集数据")]
    bridge=read_json(ROOT/"data"/"PUSH_BRIDGE_AUDIT.json")
    rows.append(("推送审计",str(bridge.get("status") or "暂无审计记录"),"审计时间："+str(bridge.get("checked_at") or "未提供")+"；未做新的群消息送达测试"))
    return panel("采集 → 发现 → 分析 → 页面 / 推送",issues+'<table class="pd-table"><tr><th>环节</th><th>当前值</th><th>说明</th></tr>'+''.join(f'<tr><td>{text(a)}</td><td>{text(b)}</td><td>{text(c)}</td></tr>' for a,b,c in rows)+'</table>')


def render(page, status, code=""):
    data=load_data()
    snap=data["snapshot"]
    state,fresh=freshness(snap)
    discovered=pool(data)
    quotes=data["quotes"]
    title=TITLES.get(page,"工作台")
    header=f'<div class="pd-root"><header class="pd-heading"><div><span>云端工作台 · {text(snap.get("trade_date"))}</span><h1>{text(title)}</h1></div><button type="button" id="pd-refresh">刷新数据</button></header>'
    header+=f'<div class="pd-data-state"><b>{text(state)}</b><span>服务器接收：{text(snap.get("observed_at"))} · 覆盖 {len(quotes)} 只 · 每60秒刷新</span></div>'
    if not snap:
        header+=note("尚无有效云端行情快照。请查看数据健康中的原因；空缺数据不会填成零。")
    if page=="overview":
        up=sum(num(r.get("pct_chg")) is not None and float(r["pct_chg"])>0 for r in quotes)
        down=sum(num(r.get("pct_chg")) is not None and float(r["pct_chg"])<0 for r in quotes)
        cards=metric("行情覆盖",len(quotes),f"上涨 {up} / 下跌 {down}","health")+metric("全天发现",len(discovered),"查看最早发现和后续表现","tickets")+metric("持有股票",data["positions"] if data["positions"] is not None else "未接通","进入持仓监控与录入","portfolio")+metric("策略分析","已生成" if analysis_current(data) else "待更新","查看真实评估状态","a_exec")
        from operator_workspace import operator_panel
        body=operator_panel(data)+'<div class="pd-metrics">'+cards+'</div>'+panel("全天发现 · 最近出现",stock_table(list(reversed(discovered)),data,15)+link("tickets","查看全天全部候选"))+panel("行业强弱",sector_table(data))
    elif page in {"tickets","sim"}:
        body=panel("全天发现记录",note(f'当日累计发现 {len(discovered)} 只。蓝灰为首次发现基准；红色上涨、绿色下跌。发现后涨跌按当前价÷发现价−1计算。')+search()+stock_table(discovered,data,len(discovered)))
        if not discovered:
            body+=panel("市场报价观察",note("发现记录为空，以下仅为最新行情排名；没有将报价自动升级为候选。")+stock_table(quotes,data,100))
    elif page=="a_exec":
        candidates=analysis_candidates(data)
        qualified=[r for r in candidates if r.get("pool")=="A"] if analysis_current(data) else []
        body=panel("策略评估状态",note("当前没有与行情同时点的A级评估。全天发现仍可查看，需等待策略分析完成。" if not qualified else f"有 {len(qualified)} 条同时间段A级规则评估。当前时段状态：{state}。"))
        if qualified:
            rows=''.join('<tr><td>'+link("stock_detail",r.get("name") or r.get("ts_code"),r.get("ts_code", ""))+f'</td><td>{text(r.get("veto") or "仍需核验事件风险")}</td><td>{text(r.get("observed_at"))}</td></tr>' for r in qualified)
            body+=panel("规则评估记录",'<table class="pd-table"><tr><th>股票</th><th>风险说明</th><th>评估时间</th></tr>'+rows+'</table>')
        body+=panel("盘中证据复核",search()+assessment_table(data))
    elif page=="limitup":
        estimates=[(r,board_estimate(r)) for r in quotes]
        uppers=[r for r,e in estimates if e and e["near_up"]]
        lowers=[r for r,e in estimates if e and e["near_down"]]
        opened=[r for r,e in estimates if e and e["opened"]]
        body=note("以下按昨收和常规板块涨跌幅规则估算，不是官方涨停池；新股、特殊交易安排及真实封单情况需专门数据核验。")
        body+='<div class="pd-metrics">'+metric("接近涨幅上限 · 估算",len(uppers),"下方可展开个股明细","limitup")+metric("接近跌幅下限 · 估算",len(lowers),"下方可展开个股明细","limitup")+metric("日内触及后回落 · 估算",len(opened),"根据日内最高价与最新价","limitup")+metric("行业覆盖",len(sectors(data)),"上涨占比由实际报价家数计算","limitup")+'</div>'
        body+=search()+panel("接近涨幅上限的股票",stock_table(uppers,data,len(uppers)))
        body+='<details class="pd-panel"><summary>接近跌幅下限的股票</summary>'+stock_table(lowers,data,len(lowers))+'</details>'
        body+='<details class="pd-panel"><summary>日内触及估算上限后回落</summary>'+stock_table(opened,data,len(opened))+'</details>'+panel("行业扩散",sector_table(data))
    elif page=="sniper":
        body=validation(data,status)
    elif page=="stock_detail":
        item=data["by_code"].get(code) or next((r for c,r in data["by_code"].items() if c.split('.')[0]==str(code).split('.')[0]),None)
        if item:
            first=data["first"].get(item["ts_code"])
            facts=[("行业",item.get("industry")),("现价",fmt(item.get("close"))),("昨收",fmt(item.get("pre_close"))),("开盘",fmt(item.get("open"))),("日内最高",fmt(item.get("high"))),("日内最低",fmt(item.get("low"))),("成交均价",fmt(item.get("vwap"))),("首次发现",first.get("observed_at") if first else "当日无发现记录"),("行情来源",item.get("source")),("报价接收时间",item.get("observed_at"))]
            body=panel(str(item.get("name"))+" · "+item["ts_code"],stock_table([item],data,1)+'<dl class="pd-facts">'+''.join(f'<div><dt>{text(k)}</dt><dd>{text(v)}</dd></div>' for k,v in facts)+'</dl>')
            body+=panel("现在有什么依据",note("行情和发现记录可核对；新闻、公告、主动资金和筹码证据没有在本页完成核验，不据此声称洗盘或主升已经确认。"))
            if first:
                evidence=first.get("evidence_json") or "{}"
                body+='<details class="pd-panel"><summary>查看首次发现依据（中文说明）</summary>'+discovery_evidence(evidence)+'</details>'
            body+=link("portfolio","进入我的持仓")+" · "+link("tickets","返回全天候选")
        else:
            body=panel("该股暂缺本轮报价",note("代码未出现在当前快照中。不能用成本价或旧价格当作现价。")+link("tickets","返回候选")+" · "+link("health","查看数据健康"))
    elif page=="review":
        body=panel("当日发现复盘",note("复盘使用页面标注时刻的最新快照，不假定它已经是正式收盘价。发现后的涨跌不是交易胜率。")+search()+stock_table(discovered,data,len(discovered)))+legacy_panel(data)
    elif page=="meta":
        body=legacy_panel(data)+panel("新版验证范围",note("旧V8结果保留为历史对照。新版需建立自身信号与后续结果的配对记录，不能继承旧版胜率。"))
    elif page in {"health","sources","upgrade"}:
        body=diagnostics(data,status)
        body+=panel("已接通与待完成",note("已接通：云端行情、全天发现、个股证据、持仓入口、旧V8历史表现。待完成：当日独立策略评估覆盖、官方涨停与事件证据、完整样本外验证。"))
    else:
        body=panel(title+" · 研究状态",note("当前尚无该项已完成且可核验的验证结果，不显示虚构通过状态或概率。研究门槛不影响使用行情、候选和持仓监控。")+link("meta","查看已有样本结果")+" · "+link("health","查看数据覆盖"))+legacy_panel(data)
    from fusion_freshness_status import panel as freshness_panel
    return header+freshness_panel(data['snapshot'], *freshness(data['snapshot']))+body+'<footer class="pd-footer">云端数据 · 发现记录与交易确认分开显示 · '+link("health","检查更新时间与缺失原因")+'</footer></div>'


CSS = r"""
<style id="pd-product-style">
:root{--pd-bg:#101923;--pd-card:#162330;--pd-line:#33485c;--pd-text:#edf3f9;--pd-muted:#b0c0d1;--pd-up:#ff7785;--pd-down:#53d6a4}
html body,body main,body .focus-content{background:var(--pd-bg)!important;color:var(--pd-text)!important}
body .focus-title,body main h1,body main h2,body main h3,body .panel-title,body .card .panel-title{color:#edf3f9!important;background:transparent!important}
body .warn,body .warning,body .card .warn,body .card p.warn{background:#382d19!important;color:#ffe0a3!important;border-color:#aa8240!important}
body .warn *,body .warning *{color:inherit!important}
body .card p,body .card small,body .decision-brief p,body .focus-safety,body .focus-safety *{color:#bdccdc!important}
body .focus-safety,body .focus-sources,body details,body summary{background:#172637!important;color:#d9e7f5!important;border-color:#3b536b!important}
body .card .kpi{color:#f1f6fb!important}body .focus-nav a{color:#d5e4f2!important}body .focus-nav a.active{background:#274b70!important;color:#fff!important}
.pd-root{max-width:1600px;margin:0 auto;color:var(--pd-text);font-size:14px;line-height:1.6}
.pd-root .pd-heading{display:flex;justify-content:space-between;gap:20px;align-items:center;background:none!important;border:0!important;padding:8px 0 20px}
.pd-heading h1{font-size:28px;margin:4px 0;letter-spacing:.03em}.pd-heading span{color:#a4c7eb}.pd-root .pd-heading button{width:auto!important;flex:0 0 auto;padding:10px 20px;border-radius:6px;cursor:pointer}
.pd-root .pd-data-state{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;background:#1c3043;border:1px solid #486a8a;padding:12px 16px;border-radius:8px;margin-bottom:18px}
.pd-data-state span{color:#c5d9ec}.pd-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}
.pd-root a.pd-metric{display:grid;gap:7px;background:#172737;border:1px solid #415b74;padding:18px;border-radius:8px;text-decoration:none!important;color:#d7e6f4!important}
.pd-root a.pd-metric:hover{background:#20374f;border-color:#7ab9f2}.pd-metric b{font-size:29px;color:#f4f8fc!important;line-height:1.3}.pd-metric small{color:#bacee0!important}
.pd-root .pd-panel{background:var(--pd-card)!important;border:1px solid var(--pd-line);border-radius:8px;padding:18px;margin:0 0 18px;min-width:0}
.pd-panel h2{font-size:17px;margin:0 0 14px}.pd-root .pd-note{background:#253343;color:#dfeaf6!important;border-left:3px solid #88b9ec;padding:12px 14px;margin:10px 0 18px}
.pd-table-wrap{overflow:auto;max-height:70vh}.pd-root table.pd-table{width:100%;border-collapse:collapse;background:transparent!important;font-size:13px}
.pd-root .pd-table th{position:sticky;top:0;background:#24415d!important;color:#fff!important;padding:12px 10px;text-align:left;white-space:nowrap;z-index:1}
.pd-root .pd-table td{background:#172633!important;color:#e3edf7!important;border-bottom:1px solid #33475a;padding:12px 10px;vertical-align:top}
.pd-root .pd-table tr:nth-child(even) td{background:#1b2b3b!important}.pd-root .pd-table tr:hover td{background:#263f56!important}.pd-table td small{display:block;color:#b9cada!important;margin-top:4px}
.pd-root .pd-table td.pd-base{background:#20384c!important}.pd-root .pd-table td.pd-after{border-left:2px solid #50697d}
.pd-root .pd-up,body .pd-root .pd-table .pd-up{color:var(--pd-up)!important}.pd-root .pd-down,body .pd-root .pd-table .pd-down{color:var(--pd-down)!important}.pd-root .pd-flat{color:#d6e2ee!important}
.pd-root a{color:#9dd0ff!important;text-underline-offset:3px}.pd-root a:focus-visible,.pd-root button:focus-visible,.pd-root summary:focus-visible{outline:3px solid #f4c16f;outline-offset:3px}
.pd-search{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:10px 0 18px;color:#dbe9f6}.pd-search input{min-width:260px;max-width:100%;padding:10px 12px;border:1px solid #6483a1;border-radius:5px}
.pd-root .pd-status{background:#29415a;color:#dceeff;padding:3px 8px;border-radius:4px}.pd-root .pd-panel summary{cursor:pointer;font-size:16px;font-weight:700;padding:8px;background:transparent!important}
.pd-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.pd-facts>div{padding:12px;border:1px solid #3b5065;border-radius:5px}.pd-facts dt{color:#aec3d8}.pd-facts dd{margin:4px 0 0;color:#fff}
.pd-root pre{white-space:pre-wrap;word-break:break-word;color:#dceaf7}.pd-footer{color:#c0d0df;padding:8px 0 20px}.pd-muted{color:#bac9d9!important}
@media(max-width:1000px){.pd-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.pd-facts{grid-template-columns:repeat(2,minmax(0,1fr))}.pd-table{min-width:850px}}
@media(max-width:600px){.pd-heading h1{font-size:23px}.pd-root .pd-panel{padding:12px}.pd-data-state{font-size:12px}.pd-search input{min-width:0;width:100%}.pd-facts{grid-template-columns:1fr}.pd-metric b{font-size:24px}}
</style>
"""

SCRIPT = r"""<script id="pd-product-script">
(()=>{let root=document.querySelector('.pd-root');if(!root)return;
const page=new URL(location.href).searchParams.get('page')||'overview';
let busy=false;let status=document.createElement('p');status.setAttribute('role','status');status.className='pd-note';root.prepend(status);
status.textContent='每60秒更新数据；保留搜索与展开状态。';
const refresh=document.getElementById('pd-refresh');if(refresh)refresh.addEventListener('click',()=>location.reload());
function filter(){const search=root.querySelector('#pd-search');if(!search)return;const term=search.value.trim().toLowerCase();let visible=0;root.querySelectorAll('.pd-searchable tbody tr').forEach(row=>{row.hidden=!!term&&!row.textContent.toLowerCase().includes(term);if(!row.hidden)visible++});root.querySelector('#pd-search-empty').hidden=visible>0;}
document.addEventListener('input',event=>{if(event.target.id==='pd-search')filter()});
async function update(){if(busy||document.hidden)return;busy=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),12000);
try{if(page==='portfolio'){const response=await fetch('/api/health?mode=LIVE',{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error();const value=await response.json();status.textContent=value.market_state+'；持仓表单保留，刷新页面查看新防守记录。';return;}
const query=new URLSearchParams({name:page,mode:'LIVE',code:new URL(location.href).searchParams.get('code')||''});const response=await fetch('/api/page?'+query,{cache:'no-store',signal:controller.signal});if(!response.ok)throw Error();const value=await response.json();
const active=document.activeElement;if(root.contains(active)&&active.matches('input:not(#pd-search),textarea,select')){status.textContent='正在编辑：暂缓替换页面，行情状态：'+value.market_state;return;}
const oldSearch=root.querySelector('#pd-search');const searchValue=oldSearch?.value||'';const searchFocus=active===oldSearch;const expanded=[...root.querySelectorAll('details')].map((d,i)=>d.open?i:-1);const scrolls=[...root.querySelectorAll('.pd-table-wrap')].map(e=>[e.scrollTop,e.scrollLeft]);const y=window.scrollY;
const fragment=new DOMParser().parseFromString(value.html,'text/html').querySelector('.pd-root');if(!fragment)throw Error();root.replaceWith(fragment);root=fragment;root.prepend(status);const newSearch=root.querySelector('#pd-search');if(newSearch){newSearch.value=searchValue;filter();if(searchFocus)newSearch.focus({preventScroll:true});}root.querySelectorAll('details').forEach((d,i)=>d.open=expanded.includes(i));root.querySelectorAll('.pd-table-wrap').forEach((e,i)=>{if(scrolls[i]){e.scrollTop=scrolls[i][0];e.scrollLeft=scrolls[i][1]}});window.scrollTo(0,y);root.querySelector('#pd-refresh')?.addEventListener('click',()=>location.reload());status.textContent='页面已更新 '+new Date().toLocaleTimeString()+'；'+value.market_state;
}catch(error){status.textContent='刷新失败：当前为旧页面，暂停据此作实时判断。正在等待下一轮重试。';}finally{clearTimeout(timer);busy=false;}}
setInterval(update,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)update()});
})();</script>"""


def install(workbench):
    original=workbench.core.render_page
    workbench.PAGE_TITLES.update(TITLES)
    workbench.NAV_GROUPS=tuple((name,icon,description,tuple((page,TITLES.get(page,label)) for page,label in entries)) for name,icon,description,entries in workbench.NAV_GROUPS)

    def product_page(*args, **kwargs):
        page=kwargs.get("page",args[0] if args else "overview")
        mode=kwargs.get("mode","DEMO")
        html=original(*args,**kwargs)
        if mode=="LIVE" and page in TITLES and page!="portfolio":
            status=kwargs.get("status") or {}
            body=render(page,status,kwargs.get("code", ""))
            html=re.sub(r'(<main\b[^>]*>).*?(</main>)',lambda m:m.group(1)+body+m.group(2),html,count=1,flags=re.S)
            # The old DOM enhancement makes cards clickable by text matching and
            # reloads while editing. These product views use explicit links.
            html=html.replace(workbench.SCRIPT,"")
            html=re.sub(r'<script>setTimeout\(function\(\)\{ window.location.reload\(\); \}, 60000\);</script>',"",html)
            html=html.replace('</body>',SCRIPT+'</body>',1)
        if mode=="LIVE":
            if page=='portfolio':
                from operator_workspace import operator_panel
                from fusion_freshness_status import panel
                current=load_data()
                summary='<div class="pd-root">'+panel(current['snapshot'], *freshness(current['snapshot']))+operator_panel(current)+'</div>'
                html=re.sub(r'(<main\b[^>]*>)',lambda m:m.group(1)+summary,html,count=1)
                html=html.replace('</body>',SCRIPT+'</body>',1)
            state,_=freshness(load_data()["snapshot"])
            html=html.replace('Rich Workbench / Server Navigation','云端行情 · 候选 · 持仓 · 复盘')
            html=html.replace('>DEMO<','>演示<').replace('>LIVE<','>真实数据<').replace('>PIT<','>历史<')
            html=html.replace('● READY',text(state))
        return html.replace('</head>',CSS+'</head>',1)

    workbench.core.render_page=product_page
    original_handler=workbench.core.H

    class ProductHandler(original_handler):
        def product_json(self,payload,code=200):
            encoded=json.dumps(payload,ensure_ascii=False,default=str,allow_nan=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            parsed=urlparse(self.path)
            query=parse_qs(parsed.query)
            path=parsed.path.lower()
            mode=query.get('mode',['LIVE'])[0].upper()
            if path=='/' and ('mode' not in query or mode=='AUTO'):
                query['mode']=['LIVE']
                self.path='/?'+urlencode(query,doseq=True)
                mode='LIVE'
            handled={'/api','/api/health','/api/data-sources','/api/page','/api/stock','/api/portfolio/lookup'}
            if path not in handled or mode=='DEMO':
                return super().do_GET()
            if mode=='PIT':
                return self.product_json({'data_mode':'PIT','error':'该接口需指定历史快照；不会用今日报价或演示统计代替历史数据。'},409)
            data=load_data()
            state,fresh=freshness(data['snapshot'])
            base={'data_mode':'LIVE','source':'cloud_persisted_snapshot','snapshot':data['snapshot'],
                  'market_state':state,'fresh_for_intraday_review':fresh,'quote_count':len(data['quotes']),
                  'discovered_count':len(data['first']),'errors':data['errors']}
            from fusion_freshness_status import describe
            base['data_freshness']=describe(data['snapshot'], state, fresh)
            if path=='/api/health':
                base.update(status='ok' if data['quotes'] and not data['errors'] and fresh else 'degraded',
                            analysis_current=analysis_current(data),statistics_ready=False)
                return self.product_json(base)
            if path=='/api/data-sources':
                base.update(analysis_time=data['analysis'].get('generated_at'),
                            legacy_connected=data['legacy'] is not None,
                            source_trade_time=data['snapshot'].get('source_trade_time'))
                return self.product_json(base)
            if path in {'/api/stock','/api/portfolio/lookup'}:
                code=query.get('code',[''])[0].upper().strip()
                if not re.fullmatch(r'\d{6}(?:\.(?:SH|SZ|BJ))?',code):
                    return self.product_json({'found':False,'error':'请输入六位股票代码，可带.SH/.SZ/.BJ后缀'},400)
                item=data['by_code'].get(code)
                if item is None and '.' not in code:
                    item=next((r for key,r in data['by_code'].items() if key.split('.')[0]==code),None)
                if item is None:
                    if path=='/api/portfolio/lookup':
                        return super().do_GET()
                    return self.product_json(dict(base,found=False,code=code,reason='本轮快照缺少该股报价'),404)
                if path=='/api/portfolio/lookup':
                    return self.product_json({'found':True,'ts_code':item['ts_code'],'code':item['ts_code'],
                        'name':item.get('name'),'sector':item.get('industry'),'last_price':item.get('close'),
                        'price_observed_at':item.get('observed_at'),'source':item.get('source')})
                return self.product_json(dict(base,found=True,quote=item,first_discovery=data['first'].get(item['ts_code']),execution_confirmed=False))
            if path=='/api/page':
                page=query.get('name',['overview'])[0]
                if page not in TITLES or page=='portfolio':
                    return self.product_json({'error':'请使用该功能的工作台页面'},404)
                return self.product_json(dict(base,page=page,html=render(page,{},query.get('code',[''])[0])))
            try:
                limit=min(6000,max(1,int(query.get('limit',['200'])[0])))
                offset=max(0,int(query.get('offset',['0'])[0]))
            except ValueError:
                return self.product_json({'error':'limit和offset必须为整数'},400)
            return self.product_json(dict(base,quotes=data['quotes'][offset:offset+limit],
                discoveries=pool(data)[offset:offset+limit],offset=offset,limit=limit,
                analysis_current=analysis_current(data),model_statistics=None))

    workbench.core.H=ProductHandler

from fusion_clean_theme import CSS as CLEAN_WORKBENCH_CSS
CSS += CLEAN_WORKBENCH_CSS
