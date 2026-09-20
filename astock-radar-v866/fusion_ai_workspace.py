"""Read-only AI evidence panel; no network calls from page rendering."""
import json
from pathlib import Path
from html import escape
from functools import wraps

ROOT=Path(__file__).resolve().parent


def render():
    try:
        state=json.loads((ROOT/'data/fusion_ai_status.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        state={}
    labels={'WAITING_MARKET_SESSION':'等待交易时段','WAITING_FRESH_CANDIDATES':'等待新鲜候选证据','WAITING_HEALTHY_BASELINE':'等待健康的早发现数据','BUDGET_PAUSED':'预算保护暂停','PRICING_RECHECK_REQUIRED':'报价超过7天，暂停并等待重新核价','RUNNING':'分析任务运行中','LAST_REVIEW_FAILED':'上次分析失败，不自动重试','DISABLED':'自动分析关闭','PAUSED_ERROR':'异常暂停'}
    e=lambda x:escape(str(x),quote=True)
    out=['<section class="pd-panel" id="fusion-ai-evidence"><h2>AI 证据复核</h2>',
         '<p>'+e(labels.get(state.get('status'),'尚未就绪'))+' | 最近更新：'+e(state.get('checked_at','未提供'))+'</p>',
         '<p>日预算 '+e(state.get('daily_budget_cny',5))+' 元 / 月预算 '+e(state.get('monthly_budget_cny',50))+' 元；单次保守占用0.25元，实际扣费尚未自动对账。AI不改变原选股、风控与执行状态。</p>']
    for row in state.get('reviews',[])[:5]:
        out.append('<details><summary>'+e(row.get('code',''))+' | '+e(row.get('status',''))+' | 原发现 '+e(row.get('first_seen_at',''))+'</summary>')
        try:
            result=json.loads(row.get('result') or '{}')
        except (ValueError,TypeError):
            result={}
        out.append('<p>AI生成摘要（非事实核验结论）：'+e(result.get('summary','暂无完整结果'))+'</p><p>缺失证据：'+e('；'.join(result.get('missing',[])))+'</p><p>生成时间：'+e(row.get('created_at',''))+'；微信状态：'+e(row.get('notice_status',''))+'</p></details>')
    if not state.get('reviews'):
        out.append('<p>暂无AI复核结果，不使用演示数字填充。</p>')
    out.append('<p>只分析新鲜早发现记录；没有联网核实公告，不提供胜率或买入保证。页面历史摘要不代表当前行情。</p></section>')
    return ''.join(out)


def install():
    import operator_workspace
    import cloud_product_workbench
    original=operator_workspace.operator_panel
    if getattr(original,'_fusion_ai_workspace',False): return
    @wraps(original)
    def enhanced(*args,**kwargs):
        result=original(*args,**kwargs)
        try: return result+render()
        except Exception: return result+'<p>AI证据面板暂不可用，原雷达不受影响。</p>'
    enhanced._fusion_ai_workspace=True
    operator_workspace.operator_panel=enhanced
    if getattr(cloud_product_workbench,'operator_panel',None) is original:
        cloud_product_workbench.operator_panel=enhanced
