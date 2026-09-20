"""Truthful, read-only view over research worker state."""
import datetime as dt
import json
from pathlib import Path

from decision_workspace import STYLE, text
from fusion_engine import FAMILIES, TZ, timestamp
from fusion_journal_v2 import VERSION

STATE = Path(__file__).resolve().parent/'data'/'fusion_runtime.json'


def value(number, digits=2):
    try:
        return f'{float(number):.{digits}f}' if number is not None else '不可用'
    except (TypeError,ValueError):
        return '不可用'


def render_payload(payload, now=None):
    now = now or dt.datetime.now(TZ)
    generated = timestamp(payload.get('generated_at'))
    fresh = generated is not None and -30 <= (now-generated).total_seconds() <= 180
    compatible = payload.get('version') == VERSION
    status = payload.get('status','NOT_STARTED') if fresh and compatible else 'STALE_OR_NOT_STARTED'
    push = '研究通知已显式开启' if payload.get('push_enabled') and fresh and compatible else '新研究通知关闭或状态未知'
    rows = payload.get('candidates',[]) if status == 'RESEARCH_ONLY' else []
    statuses = {'RESEARCH_ONLY':'研究观察中','MARKET_CLOSED':'非交易时段，暂停盘中信号',
                'CALENDAR_UNKNOWN':'交易日历异常，等待重试','QUOTE_UNAVAILABLE':'实时行情不可用',
                'STALE_OR_NOT_STARTED':'研究进程未启动或状态过期','WORKER_ERROR':'研究进程异常'}
    html = [STYLE, '<section style="margin:20px 0;padding:22px;border:1px solid #887449;border-radius:16px;background:linear-gradient(125deg,#132433,#192e30);color:#eef2e7">',
            '<small>EVIDENCE DESK / FUSION SHADOW 2</small><h2>先看证据，再做决定</h2>',
            '<p>低位蓄势 · 趋势回踩 · 启动早期。保留 V8 提前发现的方向，但不把评分当胜率。</p>',
            f'<p><b>{text(statuses.get(status,status))}</b> · {text(push)}<br>数据时间：{text(payload.get("generated_at","无"))}</p>',
            '<p style="color:#e5c47f">全部为研究观察，无执行资格。公告风险覆盖未知，Meta / CPCV / Conformal 未通过真实样本外验证。</p>',
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:14px">']
    for family,label in FAMILIES.items():
        matched = [r for r in rows if r.get('state') == 'WATCH' and r.get('family') == family]
        html.append(f'<article style="background:#ffffff08;padding:16px;border-radius:12px"><h3>{text(label)} <small>{len(matched)}</small></h3>')
        if not matched:
            html.append('<p>当前无满足条件且数据完整的新鲜观察，不用旧票填充。</p>')
        for row in matched[:3]:
            plan = row.get('decision_plan') or {}
            levels = plan.get('levels') or {}
            html.extend([f'<div style="border-top:1px solid #ffffff26;padding:12px 0"><b>{text(row.get("name",""))} {text(row.get("ts_code",""))}</b>',
                f'<p><b>{text(plan.get("action", "仅观察，不买入"))}</b><br>有效至：{text(plan.get("valid_until") or "未确认")}</p>',
                f'<p>当前 {value(row.get("price"))} · 初见 {value(row.get("first_seen_price"))}<br>初见时间：{text(row.get("first_seen_at","未知"))}</p>',
                f'<p>入池依据：{text("；".join(plan.get("why") or ["等待证据同步"]))}</p>',
                f'<p>结构支撑 {value(levels.get("support"))} · 平台阻力 {value(levels.get("resistance"))}<br>仅供结构复核，不是买入或止损指令。</p>',
                f'<p>后续观察：{text(row.get("next_condition","等待确认"))}<br>失效条件：{text(row.get("invalidation","数据过期或条件不再成立"))}</p>',
                f'<p>执行拦截：{text("；".join(plan.get("blockers") or ["尚未核验，不执行"]))}</p>',
                f'<details><summary>证据与限制</summary><p>{text("；".join(row.get("issues",[])))}</p><p>行情：{text(row.get("source_trade_time","未知"))}<br>接收：{text(row.get("observed_at","未知"))}</p></details></div>'])
        html.append('</article>')
    html.append('</div><h3>今天怎样使用</h3><p>先确认源行情与接收时间，再查看自己的持仓风险，最后复核观察池。入池不是买点；缺失公告、成交或组合风险证据时不升级执行。老V8B维持原样，新版不接管其指令。</p>')
    html.append('<p>消息规则：研究入池、数据暂停、条件失效、风险否决按状态记录。盘前计划、收盘复盘和新版持仓提醒由独立运行链路处理；未开启推送、未配置专用群或缺少持仓输入时，不代表已发送或已监控。</p>')
    ops = payload.get('operations') or {}
    html.append('<h3>新版独立运行与消息台账</h3>')
    html.append('<p>新版只使用专用群机器人，不回退老群。盘前计划09:00—09:25、收盘复盘15:30—16:30，仅确认交易日才运行；只有运行窗口内的新消息可发送，不补发关闭期间的旧消息。</p>')
    html.append(f'<p>数据预计算：{text(json.dumps(payload.get("preparation", {}),ensure_ascii=False))}</p>')
    html.append(f'<p>旧版参考接入：{text(json.dumps(ops.get("legacy", {}),ensure_ascii=False))}<br>新版持仓：{text(json.dumps(ops.get("portfolio", {}),ensure_ascii=False))}</p>')
    html.append(f'<p>专用通道：{text(json.dumps(ops.get("channel", {}),ensure_ascii=False))}</p>')
    for notice in ops.get('notices',[]):
        html.append(f'<p>{text(notice.get("created_at", ""))} · {text(notice.get("kind", ""))} · {text(notice.get("status", ""))}</p>')
    html.append('<h3>从发现到固定收盘的真实记录</h3>')
    outcomes = payload.get('outcomes',[]) if compatible else []
    if not outcomes:
        html.append('<p>样本不足：暂无成熟的 T+1 / T+3 / T+5 结算，不显示模拟胜率。</p>')
    for item in outcomes:
        html.append(f'<p>T+{int(item["horizon"])}：{int(item["samples"])} 条 / {int(item["stocks"])} 只股票；正收益比例 {value(item["positive_pct"])}%；平均收益 {value(item["mean_net_pct"])}%。</p>')
    html.append('<p>口径：首次发现价至第 N 个后续交易日收盘，复权调整，假设总成本 20bp。不是实际成交收益，不是样本外胜率；同股和同期信号可能相关，不能当独立样本。</p>')
    html.append(f'<p>研究池 {int(payload.get("shortlist_size",0))} / 行情 {int(payload.get("universe_rows",0))}；历史结构就绪 {int(payload.get("context_ready",0))}。流动性预筛选不代表全市场召回。</p>')
    html.append(f'<details><summary>运行及消息状态</summary><pre style="white-space:pre-wrap">{text(json.dumps(dict(lifecycle=payload.get("lifecycle",{}),outbox=payload.get("outbox",{})),ensure_ascii=False))}</pre></details>')
    html.append('<p>本卡片属于独立研究链路。工作台其余旧页面和原有微信任务未在此改写，不能由此推断它们已完成真实验证。</p></section>')
    return ''.join(html)


def render_cards(data):
    try:
        payload = json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        payload = {}
    return render_payload(payload)


def install():
    import decision_workspace
    decision_workspace.render_cards = render_cards
