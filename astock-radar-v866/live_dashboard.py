# -*- coding: utf-8 -*-
"""V8.6.6 LIVE/PIT 工作台的只读展示组件。"""
from __future__ import annotations

from html import escape

from live_runtime import sample_message


def _s(value):
    return escape(str(value if value not in (None, "") else "--"))


def mode_header(mode, status):
    colors = {"DEMO": "#f2b84b", "LIVE": "#25d07f", "PIT": "#4bb8ff"}
    source = "程序内模拟数据" if mode == "DEMO" else ("dailyfetch/Tushare 当前真实数据" if mode == "LIVE" else "本地不可变 PIT 快照")
    color = colors.get(mode, "#8794a8")
    return f"<span class='mode-pill' style='border-color:{color};color:{color}'>{mode}</span><span class='source-line'>来源：{escape(source)}｜更新时间：{_s(status.get('updated_at'))}</span>"


def mode_controls(page, active):
    links = []
    for mode in ("DEMO", "LIVE", "PIT"):
        cls = "mode-link active" if mode == active else "mode-link"
        links.append(f"<a class='{cls}' href='/?page={escape(page)}&mode={mode}'>{mode}</a>")
    return "<div class='mode-controls'>" + "".join(links) + "</div>"


def data_notice(mode, status):
    message = "当前只使用程序内模拟数据。真实接口即使已配置，也不会混入本页数值。" if mode == "DEMO" else "当前仅验证和展示真实数据，不下单、不调参、不晋级 Challenger、不自动开启 A+。"
    return (
        "<div class='card w4'><div class='panel-title'>数据边界</div>"
        f"<p><b>{mode}</b>｜{escape(message)}</p>"
        f"<p>Token：{'已配置' if status['token']['configured'] else '未配置'}｜镜像：{_s(status['mirror'].get('status'))}｜PIT 快照：{'可用' if status.get('pit_ready') else '未建立'}</p>"
        "<p class='muted'>真实数据必须先经过字段标准化、缓存、质量检查和 PIT 落库，策略层禁止直接调用 Tushare。</p></div>"
    )


def render_sources(status, mode):
    rows = []
    for name, item in status["interfaces"].items():
        state = str(item.get("status", "NOT_TESTED"))
        cls = "green" if state in ("OK", "CACHED") else ("amber" if state in ("EMPTY", "NOT_TESTED") else "red")
        rows.append(f"<tr><td><code>{escape(name)}</code></td><td>{_s(item.get('permission'))}</td><td>{_s(item.get('last_call_time'))}</td><td>{_s(item.get('latency_ms'))} ms</td><td>{_s(item.get('rows'))}</td><td>{_s(item.get('fields'))}</td><td class='{cls}'>{escape(state)}</td><td class='error-cell'>{_s(item.get('last_error'))}</td></tr>")
    token_text = "已配置（密钥不显示）" if status["token"]["configured"] else "未配置"
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>Tushare Token</p>"
        f"<p class='kpi'>{escape(token_text)}</p><p class='muted'>来源：{_s(status['token'].get('source'))}</p></div>"
        "<div class='card'><p class='panel-title'>dailyfetch 镜像</p>"
        f"<p class='kpi'>{_s(status['mirror'].get('status'))}</p><p class='muted'>{_s(status['mirror'].get('url'))}</p></div>"
        "<div class='card'><p class='panel-title'>真实数据行</p>"
        f"<p class='kpi'>{_s(status.get('live_rows'))}</p><p class='muted'>最近验证的可识别行数</p></div>"
        "<div class='card'><p class='panel-title'>PIT 样本</p>"
        f"<p class='kpi'>{_s(status.get('real_sample_n'))}</p><p class='muted'>signal_snapshot + outcome 配对样本</p></div>"
        "<div class='card w4'><p class='panel-title'>付费接口探测明细</p><div class='table-scroll'><table><tr><th>接口</th><th>权限</th><th>最近调用</th><th>延迟</th><th>行数</th><th>字段</th><th>状态</th><th>最近错误</th></tr>"
        + "".join(rows) + "</table></div></div>"
        "<div class='card w4'><p class='panel-title'>下一步</p><p>双击 <code>VERIFY_LIVE_DATA.bat</code> 重新探测。正式验证必须先持续写入 <code>signal_snapshot</code>，再补齐 T+1/T+3/T+5、MFE、MAE 和 Triple Barrier。</p><p class='warn'>NO_PERMISSION、ERROR、TIMEOUT 不会静默回退，也不会阻止软件打开。</p></div>"
        + data_notice(mode, status) + "</div>"
    )


def render_health(status, mode):
    failed = sum(1 for item in status["interfaces"].values() if item.get("status") in ("ERROR", "NO_PERMISSION", "TIMEOUT"))
    return (
        "<div class='grid'><div class='card'><p class='panel-title'>当前模式</p>"
        f"<p class='kpi'>{mode}</p><p>LIVE：{'READY' if status.get('live_ready') else 'NOT READY'}</p><p>PIT：{'READY' if status.get('pit_ready') else 'NOT READY'}</p></div>"
        "<div class='card'><p class='panel-title'>异常接口</p>"
        f"<p class='kpi'>{failed}</p><p class='muted'>失败被隔离，不影响其他页面</p></div>"
        "<div class='card w2'><p class='panel-title'>统计资格</p>"
        f"<p class='warn'>{escape(sample_message(status))}</p><p>样本外验证与模拟盘人工批准前，交易权限保持关闭。</p></div>"
        + data_notice(mode, status) + "</div>"
    )


def render_real_page(page, status, mode, code=""):
    title = {"overview": "真实数据总览", "a_exec": "A级执行", "sniper": "A+狙击门槛", "meta": "Meta-Label", "conformal": "Conformal", "cpcv": "CPCV 验证", "factor": "因子实验室", "limitup": "连板生态", "sim": "执行仿真", "mc": "Monte Carlo", "drift": "漂移 / DSR", "stock_detail": f"股票详情 {_s(code)}"}.get(page, page)
    message = sample_message(status)
    if page == "sniper":
        gates = ("Primary Signal 已保存", "Meta 样本达标", "CPCV 样本外通过", "Conformal 校准完成", "风险否决通过", "人工批准 A+")
        rows = "".join(f"<tr><td>{escape(g)}</td><td class='red'>未通过</td><td>真实样本不足 / 未人工批准</td></tr>" for g in gates)
        content = f"<table><tr><th>门槛</th><th>状态</th><th>原因</th></tr>{rows}</table>"
    elif page == "a_exec":
        content = "<p class='warn'>尚无经过完整质量门和风险否决的真实 Primary Signal。</p><p>只有保存 decision_time、全部当时特征和来源后才会展示动作；当前不发出买卖指令。</p>"
    elif page in ("meta", "conformal", "cpcv", "factor", "mc", "drift"):
        methods = {
            "meta": ("Meta Label", "概率校准", "Bootstrap 置信区间"),
            "conformal": ("Conformal 校准集", "预测集合", "覆盖率检验"),
            "cpcv": ("Purged CV", "CPCV", "样本唯一性", "样本外 Precision"),
            "factor": ("Factor Lab", "Alpha Rank", "因子稳定性", "Bootstrap"),
            "mc": ("Monte Carlo", "路径收益分布", "最大回撤分布"),
            "drift": ("PSI 漂移", "性能漂移", "Deflated Sharpe / DSR"),
        }[page]
        rows = "".join(f"<tr><td>{escape(name)}</td><td class='amber'>SAMPLE_INSUFFICIENT</td><td>{status.get('real_sample_n', 0)} / {status.get('min_samples', 600)}</td></tr>" for name in methods)
        content = f"<p class='warn'>{escape(message)}</p><table><tr><th>统计模块</th><th>状态</th><th>真实样本</th></tr>{rows}</table><p>来源：{mode} 真实快照｜更新时间：{_s(status.get('updated_at'))}</p><p>DEMO 百分比已隐藏，不用于真实结果。</p>"
    elif page == "limitup":
        content = f"<p>PIT 涨停池快照：{status['pit_counts'].get('limitup_pool', 0)} 行</p><p class='warn'>跨日晋级标签补齐前不生成真实连板胜率。</p>"
    elif page == "sim":
        content = "<p>真实行情只进入只读执行仿真入口。</p><p class='warn'>自动下单关闭；参数自动修改关闭；Champion/Challenger 自动切换关闭。</p>"
    elif page == "stock_detail":
        content = "<p class='warn'>当前没有该股票的已验证 signal_snapshot，拒绝用 DEMO 详情代替。</p>"
    else:
        content = f"<p>已识别真实接口数据 {status.get('live_rows', 0)} 行，PIT 数据库已建立：{'是' if status.get('pit_ready') else '否'}。</p><p class='warn'>{escape(message)}</p>"
    return (
        "<div class='grid'><div class='card w4'><p class='panel-title'>" + escape(title) + "</p>" + content + "</div>"
        "<div class='card w2'><p class='panel-title'>数据血缘</p>"
        f"<p>source：{mode}</p><p>observed_at：{_s(status.get('updated_at'))}</p><p>data_quality：以接口审计为准</p></div>"
        "<div class='card w2'><p class='panel-title'>安全状态</p><p class='green'>仅验证和展示</p><p>交易：关闭｜自动调参：关闭｜自动晋级：关闭</p></div>"
        + data_notice(mode, status) + "</div>"
    )
