"""Server-rendered action funnel; all text escaped by the host helper."""
from decision_workspace import text


def render_action_center(data):
    data=data or {}; low=data.get('low_position') or []; wash=data.get('possible_washout') or []
    positions=data.get('portfolio_items') or []
    out=['<section id="fusion-action-center" style="margin:20px 0;padding:20px;border:1px solid #b68a38;border-radius:15px;background:#fffaf0">',
         '<small>ACTION FUNNEL / RESEARCH ONLY</small><h2>今日行动与重点复核</h2>',
         f'<p><b>行动标的：{int(data.get("actionable_count",0))}只</b> · 执行资格未开放</p>',
         f'<p>低位研究 {int(data.get("low_position_count",0))}只 · 疑似回踩收复 {int(data.get("pullback_count",0))}只 · 破位/风险 {int(data.get("breakdown_count",0))}只</p>']
    if not low: out.append('<p>没有满足条件且数据新鲜的低位候选，不用关联池凑数。</p>')
    for row in low[:5]:
        out.append(f'<p><b>{text(row.get("name") or "")} {text(row.get("ts_code") or "")}</b> · {text(row.get("state") or "研究观察")}<br>后续：{text(row.get("next_condition") or "等待确认")}</p>')
    out.append('<h3>疑似洗盘／回踩收复</h3>')
    if not wash: out.append('<p>当前无完整回踩收复序列。不能把普通下跌称为洗盘。</p>')
    for row in wash[:5]: out.append(f'<p>{text(row.get("name") or "")} {text(row.get("ts_code") or "")} · 只确认价格序列，不确认主力意图。</p>')
    out.append('<h3>盘中持仓复核</h3>')
    if not positions: out.append('<p>未配置新版真实持仓；系统不会猜测持仓、成本或数量。</p>')
    for row in positions:
        pnl = f"{row['pnl_pct']:.2f}%" if row.get('pnl_pct') is not None else '未确认'
        out.append(f'<p><b>{text(row.get("name") or "")} {text(row.get("ts_code") or "")}</b> · {text(row.get("stage") or "UNKNOWN")}<br>数量 {text(row.get("quantity") if row.get("quantity") is not None else "未确认")} · 可用 {text(row.get("available_qty") if row.get("available_qty") is not None else "未确认")} · 成本 {text(row.get("cost_price") if row.get("cost_price") is not None else "未确认")} · 现价 {text(row.get("price") if row.get("price") is not None else "未确认")} · 盈亏 {text(pnl)}<br>{text(row.get("risk_note") or "")}</p>')
    out.append('<p>疑似洗盘不是买入理由；只有新鲜行情、风险、板块、资金和业务证据齐全后才能进一步人工复核。</p></section>')
    return ''.join(out)
