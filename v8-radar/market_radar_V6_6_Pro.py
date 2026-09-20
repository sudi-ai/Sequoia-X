# A股机会雷达 V6.6 Pro 次日选股闭环测试版
# 保留V6.5全部盘中功能，新增收盘预测、候选池、晚间/盘前修正、次日真实跟踪与胜率

# V5.4_FINAL_WECHAT_BUILD
# -*- coding: utf-8 -*-
"""
A股机会雷达 V6.6 Pro Sentiment Shadow Test｜WeCom Group 3 Only
数据源：
1) 同花顺行业实时一览：AKShare stock_board_industry_summary_ths
2) 同花顺量价齐升：AKShare stock_rank_ljqs_ths
3) 腾讯行情：候选股实时价格复核
4) 新浪全A快照：沪深京约5000+股票批量异动扫描

用途：发现“板块刚开始加速 + 资金/广度同步 + 板块内有量价齐升个股”的候选机会。
注意：信号仅供盯盘和人工复核，不会自动下单，也不代表确定盈利。
"""

import csv
import json
import os
import time
import re
import threading
from datetime import datetime, timedelta
from collections import defaultdict, deque

import akshare as ak
import requests
import tempfile
import base64
import hashlib
from pathlib import Path

try:
    from radar_stats_v2 import (
        record_candidate as stats_record_candidate,
        update_evaluation as stats_update_evaluation,
        update_tracking as stats_update_tracking,
        migrate_legacy_once as stats_migrate_legacy,
        generate_reports as stats_generate_reports,
    )
    STATS_V2_AVAILABLE = True
except Exception as _stats_import_error:
    STATS_V2_AVAILABLE = False
    print("[统计V2] 模块未加载：", repr(_stats_import_error))

try:
    from radar_v64_stats import (
        record_discovery as v64_record_discovery,
        record_decision as v64_record_decision,
        update_outcomes as v64_update_outcomes,
        generate_reports as v64_generate_reports,
    )
    V64_STATS_AVAILABLE = True
except Exception as _v64_stats_error:
    V64_STATS_AVAILABLE = False
    print("[V6.4统计] 模块未加载：", repr(_v64_stats_error))

try:
    from v66_market_sentiment import calculate_market_sentiment, sentiment_state
    from v66_regulatory_risk import regulatory_risk_score
    from v66_trend_structure import analyze_trend_structure
    V66_SHADOW_AVAILABLE = True
except Exception as _v66_shadow_error:
    V66_SHADOW_AVAILABLE = False
    print("[V6.6影子辅助] 模块未加载：", repr(_v66_shadow_error))

try:
    import v66_closed_loop_db as v66_closed_db
    from v66_closed_loop_runtime import (
        STAGE_CLOSE as V66_STAGE_CLOSE,
        STAGE_EVENING as V66_STAGE_EVENING,
        STAGE_PREOPEN_0900 as V66_STAGE_PREOPEN_0900,
        STAGE_PREOPEN_0925 as V66_STAGE_PREOPEN_0925,
        STAGE_TRACKING as V66_STAGE_TRACKING,
        initialize as v66_closed_loop_initialize,
        capture_market_snapshot as v66_capture_market_snapshot,
        run_close_selection as v66_run_close_selection,
        track_untracked_candidates as v66_track_untracked_candidates,
        apply_evening_revision as v66_apply_evening_revision,
        apply_preopen_revision as v66_apply_preopen_revision,
        build_close_message as v66_build_close_message,
        build_evening_message as v66_build_evening_message,
        build_preopen_message as v66_build_preopen_message,
        get_job_state as v66_get_job_state,
        mark_stage_push as v66_mark_stage_push,
    )
    from v66_data_sources import fetch_evening_evidence, fetch_preopen_evidence
    V66_CLOSED_LOOP_AVAILABLE = True
except Exception as _v66_closed_loop_error:
    V66_CLOSED_LOOP_AVAILABLE = False
    print("[V6.6次日闭环] 模块未加载：", repr(_v66_closed_loop_error))

# V7 双引擎影子框架：默认关闭，失败时绝不影响 V6 正式逻辑。
try:
    from v7.shadow_runtime import (
        capture_parallel_signals as v7_capture_parallel_signals,
        capture_candidate_batch as v7_capture_candidate_batch,
    )
    from v7.performance_tracker import observe_quotes as v7_observe_quotes
    from v7.daily_maintenance import run_after_close_if_due as v7_run_after_close_if_due
    from v7.v66_message_adapter import handle_v66_intraday_event as v7_handle_v66_intraday_event
    from v7.wework_shadow_push import send_message as v7_send_unified_message
    V7_SHADOW_AVAILABLE = True
except Exception as _v7_shadow_import_error:
    V7_SHADOW_AVAILABLE = False
    v7_capture_parallel_signals = None
    v7_capture_candidate_batch = None
    v7_observe_quotes = None
    v7_run_after_close_if_due = None
    v7_handle_v66_intraday_event = None
    v7_send_unified_message = None
    print("[V7影子] 模块未加载：", repr(_v7_shadow_import_error))

# V8 is an isolated sidecar over this copied discovery engine.  It consumes
# structured candidates only; failures must never change legacy discovery,
# scoring, return values, or the original V6/V7 routes.
try:
    from v8.legacy_adapter import handle_v8_legacy_event as v8_handle_legacy_event
    V8_SIDECAR_AVAILABLE = True
except Exception as _v8_sidecar_import_error:
    V8_SIDECAR_AVAILABLE = False
    v8_handle_legacy_event = None
    print("[V8旁路] 模块未加载：", repr(_v8_sidecar_import_error))

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

SCAN_SECONDS = 20
ALERT_COOLDOWN = 600  # 同一板块 10 分钟内不重复强提醒
TOP_SECTORS = 6
TOP_STOCKS_PER_SECTOR = 3
LOG_FILE = "market_radar_log.csv"

WEWORK_WEBHOOK_ENV = "WEWORK_WEBHOOK_URL_V66_GROUP3"
WEWORK_WEBHOOK_FILE = "wework_webhook_v66_group3.txt"
WEWORK_PUSH_LOG = "wework_push_log.txt"
WEWORK_MESSAGE_ARCHIVE_FILE = "wework_message_archive.jsonl"
WEWORK_PENDING_FILE = "wework_pending.jsonl"
WEWORK_TEXT_MAX_BYTES = 1800
WEWORK_MIN_INTERVAL = 3.2
WEWORK_PENDING_MAX_AGE = 2 * 60 * 60

# 统一版接管原 V6.3 的盘中管理、复盘和持仓跟踪，同时保留 V6.4 深度确认。
# 此开关关闭后，原有稳定提醒也会发送；push_message 会统一替换为 V6.5 标识。
V64_PARALLEL_MODE = False
V64_START_STAGGER_SECONDS = 8

_wework_last_send_at = 0.0
_wework_last_pending_retry = 0.0


def _load_cn_font(size, bold=False):
    """加载 Windows 常见中文字体；失败时回退到 PIL 默认字体。"""
    if not PIL_AVAILABLE:
        return None

    candidates = []
    if bold:
        candidates += [
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
        ]
    candidates += [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]

    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        except Exception:
            pass

    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _is_rich_alert(message):
    """只有真正的交易/风险提醒生成彩色卡片，启动与心跳仍发普通文字。"""
    keys = (
        "全A独立异动",
        "全A异动",
        "集合竞价",
        "高质量候选",
        "高质量精选",
        "板块共振",
        "持仓强风险",
        "持仓风险观察",
        "持仓强势",
        "持仓诊断",
        "持仓转强",
        "事件影响雷达",
        "大盘风险升高",
        "大盘同步转强",
        "T+1跟踪",
        "主升潜力",
        "买点触发",
        "支撑后转强",
        "信号失效",
        "持仓防守",
        "7日结案",
        "潜伏观察",
        "启动候选",
        "强势洗盘",
        "下跌待确认",
        "洗盘减弱",
        "诱多风险",
        "派发风险",
        "趋势破坏",
        "洗盘结束转强",
        "主升确认",
    )
    return any(k in str(message) for k in keys)


def _line_color(line):
    """按照内容类别分配醒目颜色。"""
    s = line.strip()

    # 红：风险 / 涨幅 / 失效
    if any(k in s for k in ("🔴", "❌", "风险", "失效", "涨幅：+", "当日：-")):
        return (225, 45, 45)

    # 绿：关注区 / 强势 / 正向
    if any(k in s for k in ("🟢", "📍", "关注区", "强势", "相对成本：+")):
        return (24, 145, 74)

    # 橙：操作 / 等待 / 试仓
    if any(k in s for k in ("💡", "操作", "等待确认", "试仓", "🟡", "🟠")):
        return (230, 145, 0)

    # 蓝：股票 / 触发 / 市场信息
    if any(k in s for k in ("🎯", "触发", "📊", "⚡", "🔥", "⭐", "现价")):
        return (20, 90, 190)

    return (45, 45, 45)


def _wrap_cn(draw, text, font, max_width):
    """按像素宽度自动换行，中英文和数字都可用。"""
    text = str(text)
    if not text:
        return [""]

    lines = []
    current = ""

    for ch in text:
        trial = current + ch
        try:
            box = draw.textbbox((0, 0), trial, font=font)
            width = box[2] - box[0]
        except Exception:
            width = len(trial) * 20

        if width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = ch

    if current:
        lines.append(current)

    return lines


def _render_alert_card(message):
    """
    把交易提醒渲染成彩色 PNG 卡片，用于企业微信图片推送。
    彩色卡片用于突出买点、风险、触发条件和目标位。
    """
    if not PIL_AVAILABLE:
        return None

    raw_lines = str(message).splitlines()

    width = 1080
    margin = 42
    content_width = width - margin * 2

    title_font = _load_cn_font(38, bold=True)
    main_font = _load_cn_font(29, bold=False)
    bold_font = _load_cn_font(30, bold=True)
    small_font = _load_cn_font(24, bold=False)

    # 先测算高度
    probe = Image.new("RGB", (width, 200), "white")
    probe_draw = ImageDraw.Draw(probe)

    rendered = []
    total_h = 38

    for idx, line in enumerate(raw_lines):
        if idx == 0:
            font = title_font
            max_w = content_width
            gap = 12
        elif line.strip() == "━━━━━━━━━━━━":
            rendered.append(("separator", "", None))
            total_h += 24
            continue
        elif not line.strip():
            rendered.append(("blank", "", None))
            total_h += 15
            continue
        else:
            font = bold_font if any(
                k in line for k in ("操作：", "关注区：", "触发：", "失效：", "风险评分：")
            ) else main_font
            max_w = content_width
            gap = 8

        wrapped = _wrap_cn(probe_draw, line, font, max_w)
        for sub in wrapped:
            rendered.append(("text", sub, font))
            try:
                bb = probe_draw.textbbox((0, 0), sub or " ", font=font)
                line_h = max(34, bb[3] - bb[1] + 10)
            except Exception:
                line_h = 40
            total_h += line_h
        total_h += gap

    total_h += 58

    img = Image.new("RGB", (width, max(total_h, 340)), (248, 250, 253))
    draw = ImageDraw.Draw(img)

    # 外框
    draw.rounded_rectangle(
        (16, 16, width - 16, img.height - 16),
        radius=28,
        fill=(255, 255, 255),
        outline=(220, 225, 232),
        width=2,
    )

    y = 42

    for kind, line, font in rendered:
        if kind == "blank":
            y += 8
            continue

        if kind == "separator":
            y += 6
            draw.line(
                (margin, y, width - margin, y),
                fill=(218, 224, 232),
                width=2,
            )
            y += 18
            continue

        color = _line_color(line)

        # 重点行增加浅色背景条，让手机上更像“卡片”
        bg = None
        if "操作：" in line:
            bg = (255, 248, 224)
        elif "关注区：" in line:
            bg = (234, 249, 239)
        elif "触发：" in line:
            bg = (234, 243, 255)
        elif "失效：" in line or "风险" in line:
            bg = (255, 237, 237)

        try:
            bb = draw.textbbox((0, 0), line or " ", font=font)
            line_h = max(34, bb[3] - bb[1] + 10)
        except Exception:
            line_h = 40

        if bg is not None:
            draw.rounded_rectangle(
                (margin - 10, y - 5, width - margin + 10, y + line_h + 3),
                radius=12,
                fill=bg,
            )

        draw.text((margin, y), line, fill=color, font=font)
        y += line_h + 8

    # 底部说明
    footer = "仅供行情监测与交易辅助，请结合盘口、公告和仓位纪律独立判断。"
    draw.line((margin, img.height - 70, width - margin, img.height - 70),
              fill=(225, 228, 234), width=1)
    draw.text(
        (margin, img.height - 56),
        footer,
        fill=(105, 110, 120),
        font=small_font,
    )

    tmp = tempfile.NamedTemporaryFile(prefix="astock_v53_", suffix=".png", delete=False)
    tmp.close()
    img.save(tmp.name, "PNG", optimize=True)
    return tmp.name



def _compress_card_for_wework(image_path):
    """企业微信 Webhook 图片要求较小；超过约1.8MB时自动缩小。"""
    if not image_path or not os.path.exists(image_path):
        return image_path
    try:
        if os.path.getsize(image_path) <= 1800 * 1024:
            return image_path
        if not PIL_AVAILABLE:
            return image_path

        img = Image.open(image_path).convert("RGB")
        width, height = img.size
        scale = 0.82
        while os.path.getsize(image_path) > 1800 * 1024 and width > 620:
            width = int(width * scale)
            height = int(height * scale)
            img = img.resize((width, height))
            img.save(image_path, "JPEG", quality=84, optimize=True)
        return image_path
    except Exception as e:
        print("企业微信图片压缩失败：", e)
        return image_path


def _wework_base_dir():
    return Path(__file__).resolve().parent


def _valid_wework_webhook(value):
    return bool(re.fullmatch(
        r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]+",
        str(value or "").strip(),
        flags=re.IGNORECASE,
    ))


def _read_wework_webhook_file(path):
    for encoding in ("utf-8-sig", "utf-16", "gbk"):
        try:
            text = path.read_text(encoding=encoding)
        except (UnicodeError, OSError):
            continue
        for line in text.splitlines():
            value = line.strip().lstrip("\ufeff")
            if _valid_wework_webhook(value):
                return value
    return ""


def resolve_wework_webhook():
    """环境变量优先；否则自动从程序目录及上三级目录读取永久配置。"""
    value = os.environ.get(WEWORK_WEBHOOK_ENV, "").strip()
    if _valid_wework_webhook(value):
        return value

    base = _wework_base_dir()
    candidates = [base / WEWORK_WEBHOOK_FILE, Path.cwd() / WEWORK_WEBHOOK_FILE]
    parent = base
    for _ in range(3):
        parent = parent.parent
        candidates.append(parent / WEWORK_WEBHOOK_FILE)

    seen = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            value = _read_wework_webhook_file(path)
            if value:
                os.environ[WEWORK_WEBHOOK_ENV] = value
                return value
    return ""


def _wework_log(status, detail, message=""):
    try:
        first_line = str(message).splitlines()[0][:100] if message else ""
        safe_detail = str(detail).replace("\r", " ").replace("\n", " ")[:500]
        with (_wework_base_dir() / WEWORK_PUSH_LOG).open("a", encoding="utf-8") as f:
            f.write(
                f"{datetime.now():%Y-%m-%d %H:%M:%S}\t{status}\t"
                f"{safe_detail}\t{first_line}\n"
            )
        if status == "成功" and message:
            with (_wework_base_dir() / WEWORK_MESSAGE_ARCHIVE_FILE).open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "message": str(message),
                }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _split_wework_text(message, max_bytes=WEWORK_TEXT_MAX_BYTES):
    """按 UTF-8 字节安全分段，避免中文消息超过企业微信文本限制。"""
    text = str(message).strip()
    if not text:
        return []
    chunks = []
    current = ""
    for char in text:
        if len((current + char).encode("utf-8")) <= max_bytes:
            current += char
        else:
            if current:
                chunks.append(current.rstrip())
            current = char
    if current:
        chunks.append(current.rstrip())
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"【{index}/{total}】\n{chunk}" for index, chunk in enumerate(chunks, 1)]
    return chunks


def _compact_summary_lines(header, blocks, max_bytes=WEWORK_TEXT_MAX_BYTES):
    """在企业微信单条文本上限内装入尽可能多的信息块，并返回省略数量。"""
    lines = list(header)
    shown = 0
    reserve_bytes = 120
    for index, block in enumerate(blocks, 1):
        candidate = lines + ["", f"{index}. {block[0]}", *block[1:]]
        if len("\n".join(candidate).encode("utf-8")) + reserve_bytes > max_bytes:
            break
        lines = candidate
        shown += 1
    omitted = max(0, len(blocks) - shown)
    if omitted:
        lines.extend(["", f"其余{omitted}项暂无紧急变化，已保留后台跟踪。"])
    return lines


def _queue_wework_chunk(message):
    try:
        record = {"ts": time.time(), "message": str(message)}
        with (_wework_base_dir() / WEWORK_PENDING_FILE).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        _wework_log("已暂存", "等待网络恢复后重发", message)
    except Exception as exc:
        _wework_log("暂存失败", repr(exc), message)


def _wait_wework_rate_limit():
    global _wework_last_send_at
    wait_seconds = WEWORK_MIN_INTERVAL - (time.monotonic() - _wework_last_send_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _post_wework_text(webhook, message):
    """直连优先；失败时允许系统代理回退，共三轮重试。"""
    global _wework_last_send_at
    payload = {"msgtype": "text", "text": {"content": str(message)}}
    last_error = "未知错误"

    for attempt in range(1, 4):
        _wait_wework_rate_limit()
        modes = ("直连", "系统网络") if attempt == 1 else ("直连",)
        for mode in modes:
            try:
                if mode == "直连":
                    session = requests.Session()
                    session.trust_env = False
                    response = session.post(webhook, json=payload, timeout=(8, 20))
                else:
                    response = requests.post(webhook, json=payload, timeout=(8, 20))
                response.raise_for_status()
                data = response.json()
                if data.get("errcode") == 0:
                    _wework_last_send_at = time.monotonic()
                    _wework_log("成功", mode, message)
                    return True
                last_error = f"{mode} errcode={data.get('errcode')} errmsg={data.get('errmsg')}"
            except Exception as exc:
                last_error = f"{mode} {type(exc).__name__}: {exc}"
        if attempt < 3:
            time.sleep(attempt * 2)

    _wework_log("失败", last_error, message)
    print("企业微信推送失败：", last_error)
    return False


def send_wework(message, image_path=None, queue_on_failure=True):
    """企业微信唯一可靠出口：永久配置、纯文字、限速、重试和失败暂存。"""
    webhook = resolve_wework_webhook()
    if not webhook:
        print(f"企业微信未配置：请把完整 Webhook 保存到 {WEWORK_WEBHOOK_FILE}")
        _wework_log("未配置", f"没有找到 {WEWORK_WEBHOOK_FILE}", message)
        if queue_on_failure:
            for chunk in _split_wework_text(message):
                _queue_wework_chunk(chunk)
        return False

    chunks = _split_wework_text(message)
    if not chunks:
        return True
    all_ok = True
    for chunk in chunks:
        if not _post_wework_text(webhook, chunk):
            all_ok = False
            if queue_on_failure:
                _queue_wework_chunk(chunk)
    return all_ok


def retry_pending_wework_messages(force=False):
    """网络短时异常时补发两小时内未送达的消息；成功后自动清除。"""
    global _wework_last_pending_retry
    now = time.time()
    if not force and now - _wework_last_pending_retry < 60:
        return
    _wework_last_pending_retry = now

    pending_path = _wework_base_dir() / WEWORK_PENDING_FILE
    if not pending_path.is_file() or not resolve_wework_webhook():
        return
    try:
        records = []
        for line in pending_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if now - float(item.get("ts", 0)) <= WEWORK_PENDING_MAX_AGE:
                    records.append(item)
            except Exception:
                continue

        remaining = []
        for item in records[:50]:
            if not send_wework(item.get("message", ""), queue_on_failure=False):
                remaining.append(item)
        remaining.extend(records[50:])

        temporary_path = pending_path.with_suffix(".tmp")
        if remaining:
            temporary_path.write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in remaining),
                encoding="utf-8",
            )
            os.replace(temporary_path, pending_path)
        else:
            pending_path.unlink(missing_ok=True)
            temporary_path.unlink(missing_ok=True)
    except Exception as exc:
        _wework_log("补发异常", repr(exc))




def simplify_wechat_message(message):
    """
    V6.3 微信精简输出层：
    后台继续计算评分/均线/资金/行为；
    微信只保留执行相关信息。
    """
    text = str(message)
    keep = []
    skip_keywords = (
        "买点质量", "主升潜力", "行为分", "行为评分",
        "风险收益比", "量比", "新增成交", "个股资金",
        "MA5", "MA10", "MA20", "MA30", "MA60",
        "评分", "资金轨迹"
    )
    action_keywords = (
        "现价", "操作", "买入", "关注区", "触发",
        "防守", "失效", "目标", "止损",
        "现在怎么做", "结论", "持有", "减仓",
        "加仓", "等待", "不追"
    )

    for line in text.splitlines():
        if any(k in line for k in skip_keywords):
            continue
        if line.strip() == "━━━━━━━━━━━━":
            continue
        if line.strip():
            keep.append(line)

    # 控制同一类重复信息
    result = "\n".join(keep)

    # 限制过长空白
    result = re.sub(r"\n{3,}", "\n\n", result)

    return result


def push_message(message):
    """企业微信唯一推送出口：强制使用清晰文字，不再生成小尺寸图片卡片。"""
    if V64_PARALLEL_MODE and "V6.4" not in str(message):
        print("[V6.4并行模式] 已抑制V6.3重复类型消息，仅保留后台统计。")
        return False
    # 对外统一品牌；内部沿用稳定函数名，但微信3群只显示 V6.6 Pro。
    message = str(message).replace("A股机会雷达 V6.3 Final", "A股机会雷达 V6.6 Pro")
    message = message.replace("A股机会雷达 V6.4", "A股机会雷达 V6.6 Pro")
    message = message.replace("A股机会雷达 V6.5 统一版", "A股机会雷达 V6.6 Pro")
    message = message.replace("A股机会雷达｜", "A股机会雷达 V6.6 Pro｜")
    # V8 runs a copied V6.6 discovery engine with both legacy and V7 routes
    # disabled.  Mirror the four high-value discovery messages into the V8
    # group before the legacy suppression branch, otherwise they are silently
    # acknowledged but never delivered.  The V8 sender provides persistent
    # de-duplication and has no authority over discovery/scoring decisions.
    v8_enabled = str(os.getenv("V8_ENABLE", "false")).strip().lower() in {"1", "true", "yes", "on"}
    v66_key_message = any(key in message for key in (
        "早期预警观察", "可建仓·深度确认", "深度确认候选",
        "深度确认汇总", "深度确认精选",
    ))
    if v8_enabled and v66_key_message:
        try:
            import hashlib
            from v8.push import send_once as v8_send_once
            digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:24]
            ok, detail = v8_send_once(f"V66_MIRROR|{datetime.now():%Y-%m-%d}|{digest}", message)
            return bool(ok or detail == "deduped")
        except Exception as exc:
            print("[V8 6.6消息镜像] 推送失败：", type(exc).__name__)
            return False
    # 统一模式稳定后可只关闭V6.6原群推送；扫描、评分、候选登记仍照常运行。
    # 返回True使原有去重状态正常落盘，避免关闭推送后同一提醒反复生成。
    if str(os.getenv("V66_ORIGINAL_PUSH_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
        route_enabled = str(os.getenv("V71_ROUTE_V66_MESSAGES", "true")).strip().lower() not in {"0", "false", "no", "off"}
        if route_enabled and v7_send_unified_message is not None:
            # The structured V7 adapter already sends these two candidate tiers;
            # routing their legacy text as well would create duplicate messages.
            if any(key in message for key in ("早期预警观察", "深度确认候选")):
                return True
            try:
                ok, _detail = v7_send_unified_message(message)
                return bool(ok)
            except Exception:
                return False
        return True
    # 次日闭环消息中的买入区间、目标和防守是数据库验收字段，不能被旧版
    # 手机简化器按关键词整行删除；其他盘中消息继续沿用原简化规则。
    closed_loop_message = any(
        key in message for key in ("15:05次日初选", "19:30复核", "09:00盘前策略", "09:25盘前策略")
    )
    if not closed_loop_message:
        message = simplify_wechat_message(message)
    return send_wework(message, image_path=None)


# 早期异动阈值：刻意不等到板块已经大涨才提醒
MIN_SECTOR_PCT = 0.60
MIN_ACCEL = 0.25
MIN_BREADTH = 1.35
MIN_NET_INFLOW = 0.0

# 大盘环境：用于避免弱市里把普通反抽误判成建仓机会
MARKET_CODES = {
    "上证指数": "000001",
    "深证成指": "399001",
    "创业板指": "399006",
}
MARKET_ALERT_COOLDOWN = 900

# V3 Pro：交易时段、二次确认、全市场精选与持仓监控
CONFIRM_SCANS = 2
MAX_DAILY_PICKS = 3
PICK_SCORE_MIN = 5.2
POSITION_ALERT_COOLDOWN = 600

# V4 Fusion：全A批量异动通道
# 新浪全A接口不适合高频重复调用，因此采用约3分钟一次的批量快照。
FULL_MARKET_SECONDS = 180
FULL_MARKET_TOP = 3
FULL_MARKET_MOVE_MIN = 1.20       # 两次快照之间的价格变化门槛(%)
FULL_MARKET_AMOUNT_DELTA_MIN = 30000000  # 两次快照之间新增成交额至少3000万元
FULL_MARKET_TOTAL_AMOUNT_MIN = 100000000 # 当日累计成交额至少1亿元
FULL_MARKET_ALERT_COOLDOWN = 900

full_market_prev = {}
full_market_prev_at = 0.0
last_full_market_scan = 0.0
last_full_market_alert = 0.0
last_full_market_signature = None


# ===== V5：校准/买卖点评分/事件影响框架 =====
CALIBRATION_CSV = "signal_calibration.csv"
CALIBRATION_STATE = "signal_calibration_state.json"
EVENT_LOG_CSV = "event_impact_log.csv"

EVAL_MINUTES = (5, 15, 30, 60)
EVENT_SCAN_SECONDS = 300
EVENT_ALERT_COOLDOWN = 1800

pending_signal_evals = {}
last_event_scan = 0.0
seen_events = set()
event_watch_codes = set()

# ===== V6.1：交易决策 / 主升潜力 / 关键位 / 7日跟踪 =====
V6_HISTORY_FILE = "v6_candidate_history.json"
V6_T1_STATE_FILE = "v6_t1_state.json"
V6_T1_CHECK_SECONDS = 600
V6_AUCTION_STATE_FILE = "v6_auction_state.json"
V6_MAX_TRACK_DAYS = 7
V61_TRACK_STATE_FILE = "v61_track_state.json"
# V6.4 两级提醒状态：同一股票同一交易日、同一提醒级别最多推送一次。
# 独立保存，避免程序重启后重复刷群，也不与 V6.3 的状态文件混用。
V64_INTRADAY_ALERT_STATE_FILE = "v64_intraday_alert_state.json"
V66_SHADOW_CONTEXT_FILE = "v66_shadow_context.json"
V66_SHADOW_ONLY = True
V66_CLOSED_LOOP_RETRY_SECONDS = 300
V66_CLOSE_CANDIDATE_LIMIT = 36
V66_DEGRADED_NOTICE_FILE = os.path.join("data_v66", "v66_degraded_notice_state.json")
_v66_runtime_info = {}
_v66_last_stage_attempt = defaultdict(float)
V61_BUY_WATCH_SECONDS = 60
_v6_flow_cache = {}
_last_t1_check = 0.0
_last_v61_buy_watch = 0.0

POSITIVE_EVENT_WORDS = (
    "中标", "订单", "合同", "回购", "增持", "业绩预增", "扭亏",
    "重大突破", "获批", "合作", "签署", "分红", "股权激励", "扩产",
)
NEGATIVE_EVENT_WORDS = (
    "减持", "立案", "调查", "处罚", "风险提示", "亏损", "业绩预减",
    "终止", "诉讼", "冻结", "质押", "退市", "ST", "问询函",
)


# ===== V5.1：决策层 =====
SIGNAL_REPEAT_COOLDOWN = 1800       # 同一股票同类信号30分钟内不重复登记
BUY_PUSH_MIN_SCORE = 72.0           # 手机“买点候选”最低质量分
BUY_STRONG_SCORE = 82.0             # 强候选分界
SELL_WARN_SCORE = 60.0              # 持仓卖出/风险预警
SELL_STRONG_SCORE = 78.0

signal_register_last = {}
position_price_history = defaultdict(lambda: deque(maxlen=240))
position_session_high = {}
position_session_low = {}


# 当前重点持仓/观察。数量只用于展示，不参与自动下单。
POSITIONS = {
    "002837": {"name": "英维克", "shares": 600, "cost": 59.50},
    "002155": {"name": "湖南黄金", "shares": 100, "cost": None},
}


# V5：POSITIONS 定义完成后再加入事件监控池
event_watch_codes.update(POSITIONS.keys())
sector_streak = defaultdict(int)
last_position_alert = defaultdict(float)
last_pick_signature = None

previous_sector_pct = {}
SECTOR_REPORT_FILE = "v63_sector_report.json"
_last_sector_snapshot_at = 0.0
last_alert = defaultdict(float)
previous_market_pct = {}
last_market_alert = 0.0


def to_float(value, default=0.0):
    if value is None:
        return default
    s = str(value).strip().replace("%", "").replace(",", "")
    if s in ("", "-", "--", "nan", "None"):
        return default
    try:
        return float(s)
    except Exception:
        return default


def symbol_for_tencent(code):
    code = str(code).zfill(6)
    if code.startswith(("5", "6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def get_tencent_quote(code):
    symbol = symbol_for_tencent(code)
    url = "https://qt.gtimg.cn/q=" + symbol
    r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    r.encoding = "gbk"
    text = r.text
    if '"' not in text:
        return None

    data = text.split('"')[1].split("~")
    if len(data) < 6:
        return None

    price = to_float(data[3])
    prev_close = to_float(data[4])
    pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0

    return {
        "name": data[1],
        "code": data[2],
        "price": price,
        "pct": pct,
        "open": to_float(data[5]) if len(data) > 5 else 0.0,
        "volume": to_float(data[6]) if len(data) > 6 else 0.0,
        # 腾讯字段原样保留，供09:25候选池竞价复核；不在这里猜测金额单位。
        "amount_raw": to_float(data[37]) if len(data) > 37 else None,
        "quote_time": str(data[30]) if len(data) > 30 else "",
    }






def safe_write_json(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def load_calibration_state():
    global pending_signal_evals
    try:
        if os.path.exists(CALIBRATION_STATE):
            with open(CALIBRATION_STATE, "r", encoding="utf-8") as f:
                pending_signal_evals = json.load(f)
    except Exception:
        pending_signal_evals = {}


def append_csv(path, header, row):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(header)
        writer.writerow(row)


def register_signal(source, code, name, price, score, context):
    """登记信号，后续自动评价 5/15/30/60 分钟收益；自动去重。"""
    if price <= 0:
        return False

    dedupe_key = f"{source}|{code}"
    now_ts = time.time()
    if now_ts - signal_register_last.get(dedupe_key, 0) < SIGNAL_REPEAT_COOLDOWN:
        return False
    signal_register_last[dedupe_key] = now_ts

    key = f"{source}|{code}|{int(now_ts)}"
    pending_signal_evals[key] = {
        "source": source,
        "code": code,
        "name": name,
        "entry_price": float(price),
        "score": float(score),
        "context": context,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "done": [],
    }
    safe_write_json(CALIBRATION_STATE, pending_signal_evals)
    return True


def evaluate_pending_signals():
    """把真实盘后表现写入 CSV，用几天实盘数据校准阈值。"""
    if not pending_signal_evals:
        return

    remove_keys = []
    changed = False

    for key, item in list(pending_signal_evals.items()):
        age_minutes = (time.time() - float(item["ts"])) / 60.0
        done = set(item.get("done", []))

        due = [m for m in EVAL_MINUTES if age_minutes >= m and m not in done]
        if not due:
            continue

        try:
            q = get_tencent_quote(item["code"])
        except Exception:
            q = None

        if not q or q["price"] <= 0:
            continue

        for m in due:
            ret = (q["price"] / float(item["entry_price"]) - 1) * 100
            append_csv(
                CALIBRATION_CSV,
                [
                    "信号时间", "来源", "名称", "代码", "信号价", "信号分",
                    "上下文", "评估分钟", "评估价", "收益率%"
                ],
                [
                    item["time"], item["source"], item["name"], item["code"],
                    round(float(item["entry_price"]), 3), item["score"],
                    item["context"], m, round(q["price"], 3), round(ret, 3)
                ],
            )
            if STATS_V2_AVAILABLE:
                stats_update_evaluation(
                    item["source"], item["code"], item["time"], m,
                    q["price"], ret,
                )
            done.add(m)
            changed = True

        item["done"] = sorted(done)
        if all(m in done for m in EVAL_MINUTES):
            remove_keys.append(key)

    for key in remove_keys:
        pending_signal_evals.pop(key, None)
        changed = True

    if changed:
        safe_write_json(CALIBRATION_STATE, pending_signal_evals)
        if STATS_V2_AVAILABLE:
            stats_generate_reports()


def buy_score(candidate, item=None, regime="NORMAL", source="SECTOR"):
    """0~100 买点质量分：用于排序/校准，不是收益保证。"""
    pct = float(candidate.get("pct", 0))
    score = 50.0

    if source == "SECTOR" and item:
        score += min(max(float(item.get("accel", 0)), 0), 1.5) * 8
        score += min(max(float(item.get("breadth", 1)) - 1, 0), 3) * 4
        # V5.7 主力资金确认权重
        net = float(item.get("net", 0))
        if net > 50000000:
            score += 12
        elif net > 10000000:
            score += 8
        elif net > 0:
            score += 4
        else:
            score -= 8

        score += min(float(candidate.get("score", 0)), 15) * 0.8
    else:
        score += min(max(float(candidate.get("move", 0)), 0), 3) * 7
        score += min(float(candidate.get("amount_delta", 0)) / 100000000, 5) * 2

    # 更偏好 1%~5.5% 的启动区，追高扣分
    if 1.0 <= pct <= 5.5:
        score += 8
    elif pct > 7.0:
        score -= 12
    elif pct < 0:
        score -= 8

    if regime == "WEAK":
        score -= 8
    elif regime == "STRONG":
        score += 4

    return max(0, min(100, round(score, 1)))



def buy_signal_label(score, pct):
    """把分数翻译成易懂的行动级别。"""
    if pct >= 6.5:
        return "🔴 不追高"
    if score >= BUY_STRONG_SCORE:
        return "🟢 A级候选"
    if score >= BUY_PUSH_MIN_SCORE:
        return "🟡 B级候选"
    return "⚪ 观察"


def v53_signal_label(score, pct, move=0.0, amount_delta=0.0, sector_confirm=False):
    """
    V5.3 双通道信号：
    1) 🚀 主升捕捉：避免过早错过启动段
    2) 🔥 强确认：等待更高确定性
    """
    if pct >= 7.0:
        return "🔴 不追高"

    if score >= BUY_STRONG_SCORE and (sector_confirm or move >= 1.5):
        return "🔥 强确认"

    if score >= BUY_PUSH_MIN_SCORE and move >= 1.2 and amount_delta >= 30000000:
        return "🚀 主升捕捉"

    if score >= BUY_PUSH_MIN_SCORE:
        return "🟡 B级候选"

    if score >= 65:
        return "🟡 观察"

    return "⚪ 观察"


def update_position_path(code, price):
    """记录程序运行期间的持仓价格路径，用于检测冲高回落/急跌。"""
    if price <= 0:
        return {"move_1m": 0.0, "drawdown_high": 0.0}

    now_ts = time.time()
    h = position_price_history[code]
    h.append((now_ts, price))

    high = max(position_session_high.get(code, price), price)
    low = min(position_session_low.get(code, price), price)
    position_session_high[code] = high
    position_session_low[code] = low

    old_price = None
    for ts, p in h:
        if ts <= now_ts - 60:
            old_price = p
        else:
            break

    move_1m = ((price / old_price - 1) * 100) if old_price else 0.0
    drawdown_high = ((price / high - 1) * 100) if high > 0 else 0.0

    return {
        "move_1m": move_1m,
        "drawdown_high": drawdown_high,
        "session_high": high,
        "session_low": low,
    }


def sell_risk_score(q, pos, path=None):
    """0~100 风险分，越高越需要人工检查减仓/止损条件。"""
    score = 15.0
    daily = float(q.get("pct", 0))
    price = float(q.get("price", 0))
    path = path or {}

    if daily <= -3:
        score += 20
    if daily <= -5:
        score += 25
    if daily >= 5:
        score -= 10

    # 盘中急跌和冲高回落是卖点的重要补充。
    move_1m = float(path.get("move_1m", 0))
    drawdown_high = float(path.get("drawdown_high", 0))
    if move_1m <= -1.2:
        score += 18
    if move_1m <= -2.0:
        score += 15
    if drawdown_high <= -2.5:
        score += 15
    if drawdown_high <= -4.0:
        score += 15

    cost = pos.get("cost")
    if cost and price > 0:
        pnl = (price / float(cost) - 1) * 100
        if pnl <= -5:
            score += 15
        if pnl <= -10:
            score += 20
        if pnl >= 8:
            score -= 5

    return max(0, min(100, round(score, 1)))


def classify_event(title):
    title = str(title)
    positive = [w for w in POSITIVE_EVENT_WORDS if w in title]
    negative = [w for w in NEGATIVE_EVENT_WORDS if w in title]

    if positive and not negative:
        return "偏利好", "、".join(positive[:3])
    if negative and not positive:
        return "偏利空", "、".join(negative[:3])
    if positive and negative:
        return "复杂/需复核", "、".join((positive + negative)[:4])
    return "中性/未知", ""


def event_monitor():
    """
    V5 事件框架 Phase 1：
    对持仓 + 最新候选检查个股新闻。
    若东方财富新闻接口临时不可用，只记错误、不影响行情雷达。
    """
    global last_event_scan

    now_ts = time.time()
    if now_ts - last_event_scan < EVENT_SCAN_SECONDS:
        return
    last_event_scan = now_ts

    codes = list(event_watch_codes)[:12]
    if not codes:
        return

    today = datetime.now().strftime("%Y-%m-%d")

    for code in codes:
        try:
            df = ak.stock_news_em(symbol=code)
        except Exception as e:
            print(f"[事件雷达] {code} 新闻源暂不可用：{e}")
            continue

        if df is None or df.empty:
            continue

        for _, row in df.head(8).iterrows():
            title = str(row.get("新闻标题", "")).strip()
            pub = str(row.get("发布时间", "")).strip()
            source = str(row.get("文章来源", "")).strip()

            if not title:
                continue

            # 只关注当天新闻；无法解析时间的条目先跳过。
            if today not in pub and datetime.now().strftime("%Y/%m/%d") not in pub:
                continue

            event_key = f"{code}|{title}"
            if event_key in seen_events:
                continue
            seen_events.add(event_key)

            bias, keywords = classify_event(title)

            # 用实时价格反应做“市场是否认可事件”的第一层确认。
            market_confirm = "未确认"
            quote_pct = None
            try:
                eq = get_tencent_quote(code)
                if eq:
                    quote_pct = float(eq.get("pct", 0))
                    if bias == "偏利好" and quote_pct >= 1.0:
                        market_confirm = "价格正向确认"
                    elif bias == "偏利空" and quote_pct <= -1.0:
                        market_confirm = "价格负向确认"
                    elif abs(quote_pct) < 0.8:
                        market_confirm = "价格暂未明显反应"
                    else:
                        market_confirm = "事件与价格反应不一致/需复核"
            except Exception:
                pass

            append_csv(
                EVENT_LOG_CSV,
                ["发现时间", "代码", "标题", "发布时间", "来源", "初步影响", "关键词", "当日涨跌%", "市场确认"],
                [
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    code, title, pub, source, bias, keywords,
                    "" if quote_pct is None else round(quote_pct, 2),
                    market_confirm,
                ],
            )

            # 只对明显利好/利空事件主动推送；中性信息留在日志。
            if bias in ("偏利好", "偏利空", "复杂/需复核"):
                icon = "🟢" if bias == "偏利好" else ("🔴" if bias == "偏利空" else "🟡")
                push_message(
                    f"{icon} 事件影响雷达｜{bias}\n"
                    f"股票：{code}\n"
                    f"标题：{title}\n"
                    f"关键词：{keywords or '需人工判断'}\n"
                    f"价格反应：{'' if quote_pct is None else f'{quote_pct:+.2f}%'}｜{market_confirm}\n"
                    f"来源：{source}\n"
                    "说明：这是事件初筛 + 价格确认，不等同于事实核验；重大事件仍需查看公告原文。"
                )


def normalize_sina_code(raw):
    s = str(raw).strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s[2:]
    return s[-6:].zfill(6)


def full_market_plan(c):
    """给全A独立异动候选生成更保守的执行条件。"""
    price = c["price"]
    pct = c["pct"]
    move = c["move"]

    if pct >= 6.5:
        return {
            "action": "不追高",
            "zone": f"{price * 0.975:.2f}-{price * 0.990:.2f}",
            "trigger": "等待回踩承接后重新转强，且不能出现放量冲高回落",
            "invalid": f"{price * 0.965:.2f}附近或涨速快速转负",
        }

    if move >= 2.0:
        return {
            "action": "等待确认",
            "zone": f"{price * 0.988:.2f}-{price * 1.002:.2f}",
            "trigger": "下一次分时回踩不破、成交继续放大，再考虑小仓试错",
            "invalid": f"{price * 0.970:.2f}附近或新增成交额明显衰减",
        }

    return {
        "action": "观察/试仓",
        "zone": f"{price * 0.995:.2f}-{price * 1.008:.2f}",
        "trigger": "价格维持强势且下一轮仍在全A异动前列",
        "invalid": f"{price * 0.975:.2f}附近或涨速转负",
    }


def scan_full_market_if_due(regime_cn):
    """
    V4 独立全A通道：
    - 一次批量取得沪深京A股快照；
    - 与上一次快照比较价格变化和新增成交额；
    - 只把最强1~3只推送到手机。
    """
    global last_full_market_scan, full_market_prev, full_market_prev_at
    global last_full_market_alert, last_full_market_signature

    now_ts = time.time()
    if now_ts - last_full_market_scan < FULL_MARKET_SECONDS:
        return

    last_full_market_scan = now_ts
    print("\n[V4全A] 正在批量读取沪深京A股快照...")

    try:
        df = _v63_realtime_spot_dataframe()
    except Exception as e:
        print("[V4全A] 统一批量行情失败：", repr(e))
        return
    if df is None:
        return

    baseline_stale = bool(full_market_prev) and (
        full_market_prev_at <= 0 or now_ts - full_market_prev_at > 10 * 60
    )

    current = {}
    candidates = []

    for _, row in df.iterrows():
        raw_code = row.get("代码", "")
        code = normalize_sina_code(raw_code)
        name = str(row.get("名称", "")).strip()

        price = to_float(row.get("最新价"))
        pct = to_float(row.get("涨跌幅"))
        amount = to_float(row.get("成交额"))
        volume = to_float(row.get("成交量"))

        if not code or price <= 0:
            continue

        current[code] = {
            "name": name,
            "price": price,
            "pct": pct,
            "amount": amount,
            "volume": volume,
        }

        old = None if baseline_stale else full_market_prev.get(code)
        if not old:
            continue

        # 过滤ST/退市风险、极低价、已经过度追高的股票
        bad_name = ("ST" in name.upper()) or ("退" in name)
        if bad_name or price < 2.0:
            continue
        if pct < 0.3 or pct > 8.5:
            continue
        if amount < FULL_MARKET_TOTAL_AMOUNT_MIN:
            continue

        old_price = old.get("price", 0)
        if old_price <= 0:
            continue

        move = (price - old_price) / old_price * 100
        amount_delta = max(amount - old.get("amount", 0), 0)

        # 弱市要求更强的独立涨速
        move_gate = 1.55 if regime_cn == "偏弱" else FULL_MARKET_MOVE_MIN
        if move < move_gate:
            continue
        if amount_delta < FULL_MARKET_AMOUNT_DELTA_MIN:
            continue

        # 评分：更重视短时涨速 + 新增成交额，同时惩罚过度追高。
        score = move * 2.2
        score += min(amount_delta / 100000000, 5.0) * 0.7
        if 1.0 <= pct <= 5.5:
            score += 1.2
        elif pct > 7.0:
            score -= 1.0
        if regime_cn == "偏弱":
            score -= 0.4

        candidates.append({
            "name": name,
            "code": code,
            "price": price,
            "pct": pct,
            "move": move,
            "amount_delta": amount_delta,
            "score": round(score, 2),
        })

    # 只有可用实时报价仍达到完整全A门槛，才更新情绪与三分钟比较基线。
    # 这样上游半截数据不会把市场宽度、涨跌家数和后续异动判断一起污染。
    current_coverage = _v63_market_coverage(current.keys())
    if len(current) < V63_REALTIME_MIN_ROWS or not current_coverage["required_complete"]:
        _v63_spot_log(
            "usable_rows_incomplete",
            f"[V4全A] 有效实时记录{len(current)}条、市场覆盖{current_coverage['counts']}；"
            "不满足完整全A条件，不更新情绪或异动基线。",
            cooldown=300,
        )
        return

    # 仅更新V6.6情绪影子上下文；不改变V6.5原有市场环境和候选门槛。
    v66_update_market_shadow(current)
    # V7 Performance Tracker 只读取本轮已经取得的全A快照，不新增任何行情请求。
    # 用于记录D0点位观察；失败时完全忽略，不影响V6。
    if V7_SHADOW_AVAILABLE and v7_observe_quotes is not None:
        try:
            v7_observe_quotes(current, source="V6全A共享快照")
        except Exception as _v7_perf_error:
            print("[V7统计] D0观察写入失败，已忽略：", repr(_v7_perf_error))
    full_market_prev = current
    full_market_prev_at = now_ts

    if baseline_stale:
        print(f"[V4全A] 上一份比较基线已超过10分钟，本轮仅用{len(current)}条完整行情重建基线，不计算三分钟异动。")
        return

    if not candidates:
        print(f"[V4全A] 快照完成：{len(current)}只；暂无达到独立异动条件的个股。")
        return

    candidates.sort(key=lambda x: (x["score"], x["move"], x["amount_delta"]), reverse=True)
    if V64_STATS_AVAILABLE:
        for c in candidates:
            v64_record_discovery(c, "全A独立异动", scan_score=c.get("score"), market_regime=regime_cn)
    raw_picks = candidates[:FULL_MARKET_TOP]
    regime_code = "WEAK" if regime_cn == "偏弱" else ("STRONG" if regime_cn == "偏强" else "NORMAL")
    picks = []
    for c in raw_picks:
        deep = v64_deep_confirm(c, regime=regime_code, source="全A独立异动")
        c["_v64"] = deep
        # 全A通道不发宽松观察，只要深度确认通过就立即单股提醒，
        # 不再等待后续的汇总签名和冷却时间。
        v64_push_intraday_tier(c, deep, "全A独立异动", regime_cn=regime_cn)
        if deep["confirmed"]:
            picks.append(c)

    if STATS_V2_AVAILABLE:
        for c in candidates:
            stats_record_candidate(c, "全A独立异动", "初级候选",
                                   scan_score=c.get("score"), market_regime=regime_cn)
        for c in picks:
            stats_record_candidate(c, "全A独立异动", "最终候选",
                                   scan_score=c.get("score"), market_regime=regime_cn)

    # 全A独立异动也进入同一Signal Lab；使用已经完成深度确认的raw_picks做公平A/B快照。
    if V7_SHADOW_AVAILABLE and v7_capture_candidate_batch is not None:
        try:
            v7_counts = v7_capture_candidate_batch(
                raw_picks, regime_cn, source="全A独立异动",
                context={"score": 50, "level": "INDEPENDENT", "breadth": 0, "accel": 0},
            )
            if v7_counts.get("enabled") and (v7_counts.get("v6") or v7_counts.get("v7")):
                print(f"[V7影子-全A] 新增记录 V6={v7_counts.get('v6', 0)} / V7={v7_counts.get('v7', 0)}")
        except Exception as _v7_full_error:
            print("[V7影子-全A] 并行记录失败，已忽略且不影响V6：", repr(_v7_full_error))

    print(f"[V6.4全A] 扫描 {len(current)} 只，发现 {len(candidates)} 只异动，深度通过 {len(picks)}/{len(raw_picks)} 只。")
    if not picks:
        print("[V6.4全A] 本轮原始异动均未通过深度确认，仅写入影子统计。")
        return
    for c in picks:
        print(
            f'  {c["name"]} {c["code"]} | 现价:{c["price"]:.2f} | '
            f'当日:{c["pct"]:+.2f}% | 快照涨速:{c["move"]:+.2f}% | '
            f'新增成交:{c["amount_delta"]/100000000:.2f}亿 | 分:{c["score"]}'
        )

    signature = tuple(x["code"] for x in picks)
    if signature == last_full_market_signature:
        return
    if now_ts - last_full_market_alert < FULL_MARKET_ALERT_COOLDOWN:
        return

    last_full_market_signature = signature
    last_full_market_alert = now_ts

    lines = [
        "⚡ A股机会雷达 V6.4｜全A深度确认",
        "",
        f"📊 市场：{regime_cn}",
        f"🎯 本轮精选：{len(picks)}只",
    ]

    for i, c in enumerate(picks, 1):
        plan = full_market_plan(c)
        deep = c.get("_v64") or v64_deep_confirm(c, regime=regime_code, source="全A独立异动")
        bscore, tech, flow, force, trade = deep["buy_score"], deep["tech"], deep["flow"], deep["force"], deep["trade"]
        rise = {"score": deep["trend_score"], "stage": "V6.4个股趋势确认"}
        register_v6_candidate(c, bscore, rise, force, trade, "全A独立异动")
        event_watch_codes.add(c["code"])
        registered = register_signal(
            "V6.4全A深度确认",
            c["code"],
            c["name"],
            c["price"],
            bscore,
            f"3分钟变化{c['move']:+.2f}%|新增成交{c['amount_delta']/100000000:.2f}亿",
        )
        label = v53_signal_label(bscore, c["pct"], c.get("move", 0), c.get("amount_delta", 0))
        lines += [
            "",
            "━━━━━━━━━━━━",
            f"{i}. {c['name']} {c['code']}",
            "",
            f"💰 现价：{c['price']:.2f}   涨幅：{c['pct']:+.2f}%",
            f"⚡ 3分钟：{c['move']:+.2f}%   新增成交：{c['amount_delta']/100000000:.2f}亿",
            f"⭐ {label}   买点质量：{bscore:.1f}/100｜个股独立：{deep['individual_score']:.1f}",
            f"🚀 主升潜力：{rise['score']:.1f}/100｜{rise['stage']}",
            f"🧠 主力状态：{force['label']}（行为分{force['score']:.0f}）",
            f"💵 个股资金：{_fmt_money(flow.get('net'))}｜{flow.get('label')}",
            "",
            f"🟢 买入计划：{trade['zone']}｜{trade['position']}",
            f"🎯 确认：{trade['trigger']}",
            f"🛡 防守位：{trade['defense'] if trade['defense'] else '-'}",
            f"🎁 第一目标：{trade['target1'] if trade['target1'] else '-'}｜趋势目标：{trade['trend_target'] if trade['trend_target'] else '-'}",
            f"⚖ ATR：{deep['atr']:.3f}｜风险收益比：{trade['rr']}:1",
        ]

    lines += [
        "",
        "⚠️ 独立异动风险高于板块共振，请复核公告、板块归属和分时承接。",
    ]
    pushed_ok = push_message("\n".join(lines))
    if STATS_V2_AVAILABLE and pushed_ok:
        for c in picks:
            stats_record_candidate(c, "V6.4全A深度确认", "已推送", market_regime=regime_cn)




# ===== V5.5 集合竞价雷达 =====
# 09:15-09:25 使用轻量模式扫描；不直接买入，仅生成关注/观察/微参考

AUCTION_START = (9, 15)
AUCTION_END = (9, 25)

def auction_status():
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 9 * 60 + 25


def auction_grade(score, pct, amount):
    if score >= 80:
        return "🟢 主机会"
    if score >= 65:
        return "🟡 观察池"
    return "⚪ 微参考（不参与）"


def auction_score(pct, amount, sector_strength=0, position=0):
    score = 0
    if 2 <= pct <= 6:
        score += 20
    elif pct > 8:
        score -= 10

    if amount >= 50000000:
        score += 25
    elif amount >= 10000000:
        score += 15

    score += min(max(sector_strength, 0), 30)
    score += min(max(position, 0), 15)

    return max(0, min(100, score))


def auction_radar_message(items):
    if not items:
        return None

    lines = [
        "⚡ A股机会雷达 V6.3 Final｜集合竞价扫描",
        "",
        "⏰ 时间：09:25",
        "（竞价结果仅作为开盘观察，不作为直接买入依据）",
    ]

    for i, x in enumerate(items[:6], 1):
        score = auction_score(
            x.get("pct", 0),
            x.get("amount", 0),
            x.get("sector_strength", 0),
            x.get("position", 0),
        )

        lines += [
            "",
            "━━━━━━━━━━━━",
            f"{i}. {x.get('name','')} {x.get('code','')}",
            f"📈 竞价：{x.get('pct',0):+.2f}%",
            f"💰 竞价金额：{x.get('amount',0)/10000:.0f}万",
            f"⭐ 竞价评分：{score}/100",
            f"📌 分类：{auction_grade(score,x.get('pct',0),x.get('amount',0))}",
        ]

    return "\n".join(lines)


def trading_session():
    """A股交易时段。盘前/午休/收盘后不做高频行情请求。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return "CLOSED"

    hm = now.hour * 60 + now.minute
    if 9 * 60 + 15 <= hm < 9 * 60 + 30:
        return "PREOPEN"
    if 9 * 60 + 30 <= hm <= 11 * 60 + 30:
        return "OPEN"
    if 13 * 60 <= hm <= 15 * 60:
        return "OPEN"
    return "CLOSED"


_v66_trade_calendar_cache = {"loaded_on": "", "days": set(), "error": ""}


def v66_confirmed_trade_day(day=None):
    """用真实交易日历确认日期；接口失败时返回 False，不按普通工作日猜测。"""
    target = day or datetime.now().date()
    target_text = target.strftime("%Y-%m-%d") if hasattr(target, "strftime") else str(target)[:10]
    loaded_on = datetime.now().strftime("%Y-%m-%d")
    if _v66_trade_calendar_cache.get("loaded_on") != loaded_on:
        try:
            frame = ak.tool_trade_date_hist_sina()
            values = set()
            if frame is not None and not frame.empty:
                column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
                for value in frame[column].tolist():
                    text_value = str(value)[:10]
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text_value):
                        values.add(text_value)
            if not values:
                raise ValueError("交易日历为空")
            _v66_trade_calendar_cache.update({
                "loaded_on": loaded_on,
                "days": values,
                "error": "",
            })
        except Exception as exc:
            _v66_trade_calendar_cache.update({
                "loaded_on": loaded_on,
                "days": set(),
                "error": repr(exc),
            })
            print("[V6.6闭环] 交易日历获取失败，固定时点任务本轮不执行：", repr(exc))
    return target_text in _v66_trade_calendar_cache.get("days", set())


def position_monitor():
    """V6.3持仓诊断：数据横排、动作竖排；持仓一直监控到从POSITIONS移除。"""
    now_ts = time.time()
    for code, pos in POSITIONS.items():
        try:
            q = get_tencent_quote(code)
            if not q: continue
            price=float(q["price"]); pct=float(q["pct"]); key=code
            path=update_position_path(code, price); risk_score=sell_risk_score(q,pos,path)
            tech=get_stock_tech_info(code); flow=get_individual_main_flow(code)
            force=v6_main_force_state({"price":price,"pct":pct},tech=tech,flow=flow)
            trade=v6_trade_plan({"price":price,"pct":pct}, max(60,100-risk_score), {"score":max(55,100-risk_score)}, tech=tech)
            levels=v61_key_levels(price,tech,trade)
            s1,s2,r1,r2=[levels.get(k) for k in ("support1","support2","resist1","resist2")]
            reason=None
            if risk_score>=SELL_STRONG_SCORE: reason=f"🔴 A股机会雷达 V6.3 Final｜持仓防守"
            elif risk_score>=SELL_WARN_SCORE: reason=f"🟡 A股机会雷达 V6.3 Final｜持仓诊断"
            elif pct>=5.0: reason=f"🟢 A股机会雷达 V6.3 Final｜持仓转强"
            if reason and now_ts-last_position_alert[key]>=POSITION_ALERT_COOLDOWN:
                last_position_alert[key]=now_ts
                pnl=(price/float(pos["cost"])-1)*100 if pos.get("cost") else None
                lines=[reason,"",f"{pos['name']} {code}",f"💰 {price:.2f}｜今日{pct:+.2f}%", f"💼 {pos.get('shares','-')}股｜成本{pos.get('cost','-')}｜盈亏{pnl:+.2f}%" if pnl is not None else f"💼 {pos.get('shares','-')}股｜成本未设置", f"📊 风险{risk_score:.0f}分｜💵主力{_fmt_money(flow.get('net'))}｜{force['label']}", "", "📈 均线", f"MA5 {tech.get('ma5','-')}｜MA10 {tech.get('ma10','-')}｜MA20 {tech.get('ma20','-')}", f"MA30 {tech.get('ma30','-')}｜MA60 {tech.get('ma60','-')}", "", "🎯 关键价格", f"支撑{s1:.2f}｜强支撑{s2:.2f}" if s1 and s2 else "支撑待确认", f"压力{r1:.2f}｜强压力{r2:.2f}" if r1 and r2 else "压力待确认", "", "👉 现在怎么做"]
                if s1 and s2 and r1 and r2:
                    lines += [f"🟡 {s1:.2f}附近不破 → 继续持有观察，暂不补仓", f"🔴 跌破{s1:.2f} → 风险增加，重点看{s2:.2f}", f"❌ 跌破{s2:.2f} → 检查减仓/止损，不补仓摊低成本", f"🟢 重新站上{r1:.2f} → 短线开始恢复", f"🚀 放量站上{r2:.2f} → 重新转强，可评估加仓"]
                conclusion="🔴 风险较高，优先防守" if risk_score>=SELL_STRONG_SCORE else ("🟡 暂时观察，不急补仓" if risk_score>=SELL_WARN_SCORE else "🟢 持仓偏强，继续观察")
                lines += ["", f"📌 结论：{conclusion}"]
                push_message("\n".join(lines))
        except Exception:
            continue

def signal_quality(item, regime):
    """V3 统一质量分：优先早启动、资金/广度同步、且有合格候选。"""
    score = float(item.get("score", 0))
    score += min(max(item.get("accel", 0), 0), 1.2) * 1.6
    score += min(max(item.get("breadth", 0) - 1.0, 0), 3.0) * 0.45
    if item.get("net", 0) > 0:
        score += 0.5
    if item.get("candidates"):
        score += min(len(item["candidates"]), 3) * 0.35
    if regime == "WEAK":
        score -= 0.6
    elif regime == "STRONG":
        score += 0.3
    return round(score, 2)




def get_daily_kline(code, days=100):
    """腾讯前复权日线，统一支持沪/深/北；失败返回空列表。"""
    try:
        symbol = symbol_for_tencent(code)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{int(days)},qfq"
        data = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
        block = data.get("data", {}).get(symbol, {})
        ks = block.get("qfqday") or block.get("day") or []
        rows = []
        for x in ks:
            if len(x) < 6:
                continue
            rows.append({
                "date": str(x[0]), "open": to_float(x[1]), "close": to_float(x[2]),
                "high": to_float(x[3]), "low": to_float(x[4]), "volume": to_float(x[5])
            })
        return rows
    except Exception:
        return []


def get_daily_kline_raw(code, days=120):
    """腾讯不复权日线，仅供 V6.6 次日真实跟踪。

    候选价、目标位和防守位都来自实时原始价格，因此不能拿前复权日线直接
    判定是否触发。函数只返回接口实际给出的日期与 OHLC，不填补缺失交易日。
    """
    try:
        symbol = symbol_for_tencent(code)
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{int(days)},"
        data = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
        block = data.get("data", {}).get(symbol, {})
        ks = block.get("day") or []
        rows = []
        for x in ks:
            if len(x) < 6:
                continue
            row = {
                "date": str(x[0]),
                "open": to_float(x[1]),
                "close": to_float(x[2]),
                "high": to_float(x[3]),
                "low": to_float(x[4]),
                "volume": to_float(x[5]),
            }
            if (
                row["open"] > 0 and row["close"] > 0
                and row["low"] > 0 and row["high"] > 0
                and row["low"] <= min(row["open"], row["close"])
                and row["high"] >= max(row["open"], row["close"])
            ):
                rows.append(row)
        return rows
    except Exception:
        return []


def get_stock_tech_info(code):
    """V6.3技术模块：MA5/10/20/30/60、30/60日高低点、量能、趋势位置。"""
    ks = get_daily_kline(code, 100)
    closes = [x["close"] for x in ks if x["close"] > 0]
    lows = [x["low"] for x in ks if x.get("low", 0) > 0]
    if len(closes) < 20:
        return {}
    def ma(n):
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None
    vols = [x["volume"] for x in ks if x["volume"] > 0]
    volume_ratio = None
    if len(vols) >= 6:
        base = sum(vols[-6:-1]) / 5
        if base > 0:
            volume_ratio = round(vols[-1] / base, 2)
    def period_change(period):
        if len(closes) <= period or closes[-period - 1] <= 0:
            return None
        return round((closes[-1] / closes[-period - 1] - 1) * 100, 3)

    ma20_value = ma(20)
    deviation_ma20 = (
        round((closes[-1] / ma20_value - 1) * 100, 3)
        if ma20_value and ma20_value > 0 else None
    )
    limit_up_count = 0
    recent = closes[-21:]
    for previous, current in zip(recent[:-1], recent[1:]):
        if previous > 0 and (current / previous - 1) * 100 >= 9.5:
            limit_up_count += 1

    return {
        "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma30": ma(30), "ma60": ma(60),
        "high30": round(max(closes[-30:]), 2) if len(closes) >= 30 else round(max(closes), 2),
        "high60": round(max(closes[-60:]), 2) if len(closes) >= 60 else round(max(closes), 2),
        "low20": round(min(lows[-20:]), 2) if len(lows) >= 20 else round(min(closes[-20:]), 2),
        "low60": round(min(lows[-60:]), 2) if len(lows) >= 60 else round(min(lows or closes), 2),
        "volume_ratio": volume_ratio,
        "last_close": closes[-1],
        "change_5d": period_change(5),
        "change_10d": period_change(10),
        "change_20d": period_change(20),
        "deviation_ma20": deviation_ma20,
        "recent_limit_up_count": limit_up_count,
    }


def _clean_levels(values):
    out = []
    for v in values:
        try:
            f = round(float(v), 2)
        except Exception:
            continue
        if f > 0 and all(abs(f - old) / max(old, 0.01) > 0.003 for old in out):
            out.append(f)
    return out


def v61_key_levels(price, tech=None, trade=None):
    """把均线、近期高低点和交易计划压缩为两档支撑/压力。仅作规则参考。"""
    tech, trade = tech or {}, trade or {}
    price = float(price or 0)
    if price <= 0:
        return {"support1": None, "support2": None, "resist1": None, "resist2": None,
                "gap_support": None, "gap_resist": None}

    below = []
    above = []
    for key in ("ma5", "ma10", "ma20", "ma30", "ma60", "low20", "low60"):
        v = tech.get(key)
        if v and float(v) < price * 1.003:
            below.append(float(v))
        elif v and float(v) > price * 1.003:
            above.append(float(v))
    for key in ("high30", "high60"):
        v = tech.get(key)
        if v and float(v) > price * 1.003:
            above.append(float(v))
    if trade.get("defense"):
        below.append(float(trade["defense"]))
    for key in ("target1", "trend_target"):
        if trade.get(key) and float(trade[key]) > price * 1.003:
            above.append(float(trade[key]))

    below = sorted(_clean_levels(below), reverse=True)
    above = sorted(_clean_levels(above))
    s1 = below[0] if below else round(price * 0.985, 2)
    s2 = next((x for x in below[1:] if x < s1 * 0.995), None) or round(min(s1 * 0.975, price * 0.96), 2)
    r1 = above[0] if above else round(price * 1.025, 2)
    r2 = next((x for x in above[1:] if x > r1 * 1.005), None) or round(max(r1 * 1.035, price * 1.06), 2)
    return {
        "support1": round(s1, 2), "support2": round(s2, 2),
        "resist1": round(r1, 2), "resist2": round(r2, 2),
        "gap_support": round((s1 / price - 1) * 100, 2),
        "gap_resist": round((r1 / price - 1) * 100, 2),
    }


def v61_position_label(price, levels, bscore=0, rise_score=0):
    r1 = levels.get("resist1")
    s1 = levels.get("support1")
    if r1 and (r1 / price - 1) * 100 <= 1.0:
        return "🟡 等一等", "股票偏强，但离上方压力较近，先不追。"
    if s1 and 0 <= (price / s1 - 1) * 100 <= 1.5 and bscore >= 72:
        return "🟢 可观察买点", "已靠近支撑，等重新上涨确认。"
    if bscore >= 82 and rise_score >= 80:
        return "🟢 偏强", "位置尚可，等触发条件再行动。"
    return "🟡 等一等", "先等更明确的价格确认。"


def v61_action_lines(levels, position_text="可考虑1成试仓"):
    s1, s2, r1, r2 = (levels.get(k) for k in ("support1","support2","resist1","resist2"))
    return [
        "👉 现在怎么做",
        "",
        f"🟡 {s1:.2f}附近不破 → 重新上涨后，{position_text}" if s1 else "🟡 等支撑确认后再考虑",
        f"🔴 跌破{s1:.2f} → 暂不买，继续看{s2:.2f}" if s1 and s2 else "🔴 跌破支撑 → 暂不买",
        f"❌ 跌破{s2:.2f} → 本次机会取消" if s2 else "",
        f"🟢 放量站上{r1:.2f} → 转强，可重新评估建仓" if r1 else "",
        f"🚀 放量突破{r2:.2f} → 强势突破，持仓继续观察" if r2 else "",
    ]

def get_short_change(code):
    """V6短周期表现。"""
    ks = get_daily_kline(code, 10)
    closes = [x["close"] for x in ks if x["close"] > 0]
    if len(closes) >= 6:
        return {
            "pct3": round((closes[-1] / closes[-4] - 1) * 100, 2),
            "pct5": round((closes[-1] / closes[-6] - 1) * 100, 2),
        }
    return {}


def get_individual_main_flow(code):
    """读取个股主力资金。接口异常时静默降级，不影响主扫描。"""
    now = time.time()
    cached = _v6_flow_cache.get(code)
    if cached and now - cached[0] < 180:
        return cached[1]
    result = {"net": None, "ratio": None, "label": "⚪ 个股资金待确认"}
    try:
        market = "sh" if str(code).startswith(("5", "6", "9")) else ("bj" if str(code).startswith(("4", "8")) else "sz")
        df = ak.stock_individual_fund_flow(stock=str(code).zfill(6), market=market)
        if df is not None and not df.empty:
            row = df.iloc[-1]
            net = None
            ratio = None
            for col in ("主力净流入-净额", "主力净流入净额", "主力净流入"):
                if col in df.columns:
                    net = to_float(row.get(col), None)
                    break
            for col in ("主力净流入-净占比", "主力净流入净占比", "主力净占比"):
                if col in df.columns:
                    ratio = to_float(row.get(col), None)
                    break
            result["net"] = net
            result["ratio"] = ratio
            if net is not None:
                if net >= 50000000:
                    result["label"] = "🟢 个股主力强流入"
                elif net > 0:
                    result["label"] = "🟡 个股主力流入"
                elif net <= -50000000:
                    result["label"] = "🔴 个股主力强流出"
                elif net < 0:
                    result["label"] = "🟠 个股主力流出"
    except Exception:
        pass
    _v6_flow_cache[code] = (now, result)
    return result


def _fmt_money(v):
    if v is None:
        return "-"
    if abs(v) >= 100000000:
        return f"{v/100000000:+.2f}亿"
    return f"{v/10000:+.0f}万"


def v6_main_force_state(c, item=None, tech=None, flow=None):
    """规则化主力行为标签。是行为评估，不宣称能识别真实账户身份。"""
    tech = tech or {}
    flow = flow or {}
    price, pct = float(c.get("price", 0)), float(c.get("pct", 0))
    ma5, ma20 = tech.get("ma5"), tech.get("ma20")
    vr = tech.get("volume_ratio")
    net = flow.get("net")
    sector_net = float(item.get("net", 0)) if item else 0.0
    score = 50.0
    if net is not None:
        score += 12 if net >= 50000000 else (7 if net > 0 else (-12 if net <= -50000000 else -6))
    if sector_net > 0:
        score += min(sector_net, 5) * 2
    if ma20 and price >= ma20:
        score += 7
    elif ma20 and price < ma20:
        score -= 9
    if ma5 and price >= ma5:
        score += 5
    if vr is not None:
        if 1.05 <= vr <= 2.2 and pct > 0:
            score += 8
        elif vr >= 2.8 and pct <= 1:
            score -= 8
        elif vr <= 0.9 and pct < 0 and (not ma20 or price >= ma20):
            score += 4
    if pct >= 1.0:
        score += 5
    if pct <= -4.0:
        score -= 10
    score = max(0, min(100, round(score, 1)))
    if pct < 0 and (vr is None or vr <= 0.95) and (not ma20 or price >= ma20) and (net is None or net > -50000000):
        label = "🟡 洗盘整理倾向"
    elif score >= 75 and pct >= 1:
        label = "🔥 攻击拉升倾向"
    elif score >= 62:
        label = "🟢 吸筹/蓄势倾向"
    elif (net is not None and net < 0 and ma20 and price < ma20) or score < 40:
        label = "🔴 派发/撤退风险"
    else:
        label = "⚪ 多空待确认"
    return {"score": score, "label": label}


def v6_main_rise_score(c, bscore, item=None, tech=None, flow=None, regime="NORMAL"):
    """主升潜力分：趋势/资金/量价/板块/位置的综合潜力，不等于上涨概率。"""
    tech, flow = tech or {}, flow or {}
    price, pct = float(c.get("price", 0)), float(c.get("pct", 0))
    score = 35 + float(bscore) * 0.35
    for key, pts in (("ma5", 5), ("ma10", 4), ("ma20", 6), ("ma60", 4)):
        m = tech.get(key)
        if m and price >= m:
            score += pts
    vr = tech.get("volume_ratio")
    if vr is not None:
        if 1.0 <= vr <= 2.2:
            score += 7
        elif vr > 3.0:
            score -= 5
    net = flow.get("net")
    if net is not None:
        score += 8 if net > 0 else -8
    if item:
        if float(item.get("accel", 0)) > 0:
            score += min(float(item.get("accel", 0)), 1.0) * 5
        if float(item.get("breadth", 1)) >= 1.5:
            score += 4
    high30 = tech.get("high30")
    if high30 and price > 0:
        gap = (high30 / price - 1) * 100
        if 2 <= gap <= 12:
            score += 5
        elif gap < 1 and pct > 6:
            score -= 6
    if pct > 7:
        score -= 10
    elif 1 <= pct <= 5.5:
        score += 4
    if regime == "WEAK":
        score -= 7
    elif regime == "STRONG":
        score += 4
    score = max(0, min(100, round(score, 1)))
    if score >= 85:
        stage = "启动/主升候选"
    elif score >= 75:
        stage = "启动观察"
    elif score >= 65:
        stage = "蓄势观察"
    else:
        stage = "暂未形成主升结构"
    return {"score": score, "stage": stage}


def v6_trade_plan(c, bscore, rise, tech=None, regime="NORMAL", base_plan=None):
    """生成买点、仓位、止盈、止损和风险收益比。"""
    tech = tech or {}
    base_plan = base_plan or candidate_plan(c)
    price = float(c.get("price", 0))
    ma10, ma20 = tech.get("ma10"), tech.get("ma20")
    support_candidates = [price * 0.965]
    for m in (ma10, ma20):
        if m and m < price:
            support_candidates.append(float(m) * 0.995)
    defense = max(support_candidates) if price > 0 else 0
    defense = min(defense, price * 0.985) if price > 0 else defense
    high30 = tech.get("high30")
    target1 = price * 1.06
    if high30 and high30 > price * 1.015:
        target1 = min(max(target1, high30), price * 1.10)
    trend_target = max(target1 * 1.04, price * 1.10)
    risk = max(price - defense, price * 0.01)
    reward = max(target1 - price, 0)
    rr = round(reward / risk, 2) if risk > 0 else 0
    if bscore >= 88 and rise.get("score", 0) >= 82 and rr >= 1.5 and regime != "WEAK":
        position = "试仓15%-20%，确认后总仓不超过30%"
    elif bscore >= 78 and rise.get("score", 0) >= 72 and rr >= 1.3:
        position = "试仓10%-15%"
    else:
        position = "观察为主，最多试仓5%-10%"
    if float(c.get("pct", 0)) >= 6.5 or rr < 1.2:
        position = "不追高/暂不建仓"
    return {
        "zone": base_plan.get("zone", "-"),
        "trigger": base_plan.get("trigger", "等待确认"),
        "defense": round(defense, 2) if defense else None,
        "target1": round(target1, 2) if target1 else None,
        "trend_target": round(trend_target, 2) if trend_target else None,
        "rr": rr, "position": position,
    }


# ===== V6.4：独立深度确认层（不重复使用板块分） =====
_v64_deep_cache = {}


def v64_atr(code, periods=14):
    """用信号当时可取得的日线计算ATR，不使用未来数据。"""
    ks = get_daily_kline(code, max(35, periods + 5))
    if len(ks) < periods + 1:
        return None
    values = []
    for i in range(1, len(ks)):
        high = float(ks[i].get("high", 0) or 0)
        low = float(ks[i].get("low", 0) or 0)
        prev = float(ks[i - 1].get("close", 0) or 0)
        if high > 0 and low > 0 and prev > 0:
            values.append(max(high - low, abs(high - prev), abs(low - prev)))
    return round(sum(values[-periods:]) / periods, 4) if len(values) >= periods else None


def v64_individual_scores(c, tech, flow):
    """只评价个股自身，避免板块涨幅、广度和资金重复加分。"""
    price, pct = float(c.get("price", 0) or 0), float(c.get("pct", 0) or 0)
    individual = 35.0
    trend = 35.0
    for key, points in (("ma5", 7), ("ma10", 8), ("ma20", 12), ("ma30", 8)):
        value = tech.get(key)
        if value:
            if price >= float(value):
                individual += points * 0.55
                trend += points
            else:
                trend -= points * 0.8
    vr = tech.get("volume_ratio")
    if vr is not None:
        if 1.0 <= float(vr) <= 2.4:
            individual += 12
        elif float(vr) > 3.2:
            individual -= 8
    net = flow.get("net")
    if net is not None:
        individual += 12 if float(net) > 0 else -12
    if 0.5 <= pct <= 5.5:
        individual += 8
    elif pct >= 6.5:
        individual -= 15
    high30 = tech.get("high30")
    if high30 and price > 0:
        room = (float(high30) / price - 1) * 100
        if 2 <= room <= 12:
            trend += 8
        elif 0 <= room < 1:
            trend -= 6
    return round(max(0, min(100, individual)), 1), round(max(0, min(100, trend)), 1)


def v64_trade_plan(c, tech, atr):
    """以结构支撑加ATR缓冲生成防守位；风险不合适时由确认层直接拦截。"""
    price = float(c.get("price", 0) or 0)
    if price <= 0:
        return {"zone": "-", "trigger": "等待确认", "defense": None, "target1": None,
                "trend_target": None, "position": "仅观察", "rr": 0, "risk_pct": 99}
    supports = [float(x) for x in (tech.get("ma5"), tech.get("ma10"), tech.get("ma20"), tech.get("low20")) if x and float(x) < price]
    support = max(supports) if supports else price * 0.975
    buffer = float(atr or price * 0.02) * 0.35
    defense = max(0.01, support - buffer)
    risk = price - defense
    risk_pct = risk / price * 100
    high30 = float(tech.get("high30") or 0)
    structural_target = high30 if high30 > price * 1.015 else price + risk * 2
    target = min(max(structural_target, price + risk * 1.5), price * 1.10)
    rr = (target - price) / risk if risk > 0 else 0
    base = candidate_plan(c)
    return {"zone": base.get("zone", "-"), "trigger": "深度条件通过后仍等待回踩或二次转强",
            "defense": round(defense, 2), "target1": round(target, 2),
            "trend_target": round(max(target * 1.03, price * 1.08), 2),
            "position": "V6.4确认候选；仓位由使用者自行决定",
            "rr": round(rr, 2), "risk_pct": round(risk_pct, 2)}


def v64_deep_confirm(c, item=None, regime="NORMAL", source="板块共振"):
    """发现后再确认；返回结果既控制V6.4候选，也写入独立对照统计。"""
    code = str(c.get("code", "")).zfill(6)
    cache_key = (source, code, regime)
    cached = _v64_deep_cache.get(cache_key)
    if cached and time.time() - cached[0] < 180:
        return cached[1]
    tech = get_stock_tech_info(code)
    flow = get_individual_main_flow(code)
    atr = v64_atr(code)
    bscore = buy_score(c, item=item, regime=regime, source="FULL" if "全A" in source else "SECTOR")
    force = v6_main_force_state(c, item=item, tech=tech, flow=flow)
    individual_score, trend_score = v64_individual_scores(c, tech, flow)
    trade = v64_trade_plan(c, tech, atr)
    price, pct = float(c.get("price", 0) or 0), float(c.get("pct", 0) or 0)
    reasons = []
    if not tech or not tech.get("ma20") or atr is None:
        reasons.append("深度行情不完整")
    if pct >= 6.5:
        reasons.append("进入追高区")
    if tech.get("ma20") and price < float(tech["ma20"]):
        reasons.append("价格低于MA20")
    if flow.get("net") is not None and float(flow["net"]) <= -50000000:
        reasons.append("个股资金明显流出")
    if float(force.get("score", 0) or 0) < 55 or "派发" in str(force.get("label", "")) or "撤退" in str(force.get("label", "")):
        reasons.append("主力行为未通过")
    min_individual = 78 if "全A" in source else 70
    min_trend = 75 if "全A" in source else 68
    if individual_score < min_individual:
        reasons.append(f"个股独立分不足{min_individual}")
    if trend_score < min_trend:
        reasons.append(f"趋势结构分不足{min_trend}")
    if trade["rr"] < 1.5:
        reasons.append("风险收益比不足1.5")
    if trade["risk_pct"] < 1.0 or trade["risk_pct"] > 6.0:
        reasons.append("防守距离不合理")
    if regime == "WEAK" and ("全A" in source or individual_score < 82 or trend_score < 78):
        reasons.append("弱市确认不足")
    confirmed = not reasons
    metrics = {
        "individual_score": individual_score, "trend_score": trend_score,
        "force_score": force.get("score"), "force_label": force.get("label"),
        "buy_score": bscore, "rr": trade.get("rr"), "atr": atr,
        "stop_price": trade.get("defense"), "target_price": trade.get("target1"),
        "market_regime": regime, "sector": item.get("sector") if item else "",
    }
    if V64_STATS_AVAILABLE:
        v64_record_decision(c, source, confirmed, reasons or ["全部深度条件通过"], metrics)
    result = {"confirmed": confirmed, "reasons": reasons, "tech": tech, "flow": flow,
              "force": force, "individual_score": individual_score, "trend_score": trend_score,
              "buy_score": bscore, "trade": trade, "atr": atr}
    _v64_deep_cache[cache_key] = (time.time(), result)
    return result


def _load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _v66_limit_threshold(code, name):
    """按板块给出近似涨跌停阈值；只用于情绪影子统计，不参与选股。"""
    if "ST" in str(name).upper():
        return 4.8
    code = str(code)
    if code.startswith(("300", "301", "688", "689")):
        return 19.5
    if code.startswith(("4", "8")):
        return 29.5
    return 9.5


def v66_update_market_shadow(current):
    """用全A真实快照生成部分情绪分；缺失指标不再用0代替。"""
    if not V66_SHADOW_AVAILABLE or not current:
        return None
    try:
        rows = list(current.items())
        limit_up = 0
        limit_down = 0
        up_count = 0
        down_count = 0
        valid = 0
        for code, row in rows:
            pct = float(row.get("pct", 0) or 0)
            if pct > 0:
                up_count += 1
            elif pct < 0:
                down_count += 1
            valid += 1
            threshold = _v66_limit_threshold(code, row.get("name", ""))
            if pct >= threshold:
                limit_up += 1
            elif pct <= -threshold:
                limit_down += 1
        directional = up_count + down_count
        breadth_score = (up_count / directional * 100) if directional else 50.0
        limit_total = limit_up + limit_down
        limit_balance = 50.0
        if limit_total:
            limit_balance += (limit_up - limit_down) / limit_total * 50.0
        score = round(max(0.0, min(100.0, breadth_score * 0.70 + limit_balance * 0.30)), 1)
        context = {
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sample": valid,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "up_count": up_count,
            "down_count": down_count,
            "score_partial": score,
            "state_partial": sentiment_state(score),
            "score_formula": "上涨家数占比70%+涨跌停平衡30%",
            "data_complete": False,
            "missing": ["炸板率", "连板高度", "晋级率"],
        }
        safe_write_json(V66_SHADOW_CONTEXT_FILE, context)
        return context
    except Exception as exc:
        print("[V6.6情绪影子] 计算失败：", repr(exc))
        return None


def v66_candidate_shadow_annotation(c, deep):
    """在原V6.5已完成判断后追加影子标签；不改评分、排序、确认和推送触发。"""
    if not V66_SHADOW_AVAILABLE:
        return "V6.6辅助：数据不足"
    try:
        code = str(c.get("code", "")).zfill(6)
        price = float(c.get("price", 0) or 0)
        tech = (deep or {}).get("tech") or {}
        ks = get_daily_kline(code, 100)
        closes = [float(x.get("close", 0) or 0) for x in ks if float(x.get("close", 0) or 0) > 0]

        def period_change(period):
            if len(closes) <= period or closes[-period - 1] <= 0:
                return 0.0
            return (closes[-1] / closes[-period - 1] - 1) * 100

        change_5 = period_change(5)
        change_10 = period_change(10)
        change_20 = period_change(20)
        change_60 = period_change(60)
        ma20 = float(tech.get("ma20", 0) or 0)
        ma_bias = (price / ma20 - 1) * 100 if price > 0 and ma20 > 0 else 0.0
        high30 = float(tech.get("high30", 0) or 0)
        volume_ratio = float(tech.get("volume_ratio", 0) or 0)

        limit_up_count = 0
        threshold = _v66_limit_threshold(code, c.get("name", ""))
        for prev, cur in zip(closes[-21:-1], closes[-20:]):
            if prev > 0 and (cur / prev - 1) * 100 >= threshold:
                limit_up_count += 1

        trend = analyze_trend_structure(
            price_change_20=change_20,
            price_change_60=change_60,
            volume_expand=volume_ratio >= 1.2,
            high_position=bool(high30 and price >= high30 * 0.95),
            momentum_weaken=bool(change_5 > 5 and volume_ratio < 1.0),
        )
        heat = regulatory_risk_score({
            "change_5d": change_5,
            "change_10d": change_10,
            "change_20d": change_20,
            "limit_up_count": limit_up_count,
            "deviation_ma20": ma_bias,
            # 获利盘数据当前没有可靠来源，影子展示中明确标注为部分数据。
        })
        market = _load_json_file(V66_SHADOW_CONTEXT_FILE, {})
        market_text = market.get("state_partial", "情绪数据不足")
        return (
            f"V6.6影子辅助：{trend.get('state', '趋势待确认')}"
            f"｜短线过热{heat.get('level', '待确认')}"
            f"｜市场{market_text}（部分数据）"
        )
    except Exception as exc:
        print("[V6.6个股影子] 计算失败：", repr(exc))
        return "V6.6辅助：数据不足"


def _v66_market_sentiment_payload(regime_cn, market_avg):
    """读取当日真实全A情绪截面；缺失时只标记缺失，不伪造完整情绪。"""
    context = _load_json_file(V66_SHADOW_CONTEXT_FILE, {})
    updated = str(context.get("updated", ""))
    is_today = updated.startswith(datetime.now().strftime("%Y-%m-%d"))
    score = context.get("score_partial") if is_today else None
    return {
        "score": score,
        "confidence": 55.0 if score is not None else 0.0,
        "state": context.get("state_partial") if is_today else "数据不足",
        "data_complete": False,
        "missing": context.get("missing", ["全A情绪截面"]),
        "market_regime": regime_cn,
        "market_index_average_pct": round(float(market_avg or 0), 3),
        "source": "新浪全A实际快照+腾讯三大指数实际行情" if is_today else "missing",
    }


def v66_close_risk_context(deep):
    """把已取得的真实技术风险证据显式交给收盘评分，不使用文字标签代替。"""
    tech = (deep or {}).get("tech") or {}
    trade = (deep or {}).get("trade") or {}
    context = {
        "change_5d": tech.get("change_5d"),
        "change_10d": tech.get("change_10d"),
        "change_20d": tech.get("change_20d"),
        "deviation_ma20": tech.get("deviation_ma20"),
        "recent_limit_up_count": tech.get("recent_limit_up_count"),
        "volume_ratio": tech.get("volume_ratio"),
        "rr": trade.get("rr"),
        "regulatory_data_complete": False,
        "regulatory_note": "15:05尚未完成晚间公告复核；监管公告在19:30阶段处理",
        "data_source": "腾讯实际日线派生技术风险+V6.4交易计划",
    }
    return {key: value for key, value in context.items() if value is not None}


def _v66_accumulate_daily_radar(radar, captured_at):
    """按交易日累计真实发现候选；当前轮为空时不抹掉全天已发现记录。"""
    current_rows = [dict(item) for item in (radar or []) if isinstance(item, dict)]
    previous_rows = []
    snapshot_path = str(_v66_runtime_info.get("snapshot_file") or "")
    if snapshot_path:
        previous = _load_json_file(snapshot_path, {})
        if str(previous.get("capture_date", "")) == captured_at.strftime("%Y-%m-%d"):
            previous_rows = [
                dict(item) for item in previous.get("radar", []) if isinstance(item, dict)
            ]

    by_sector = {
        str(item.get("sector", "")).strip(): item
        for item in previous_rows if str(item.get("sector", "")).strip()
    }
    for current in current_rows:
        sector = str(current.get("sector", "")).strip()
        if not sector:
            continue
        old = by_sector.get(sector, {})
        merged = dict(old)
        merged.update(current)
        candidates = {}
        for candidate in old.get("_v66_raw_candidates", []) or []:
            if not isinstance(candidate, dict):
                continue
            code = str(candidate.get("code", "")).zfill(6)
            if code:
                candidates[code] = dict(candidate)
        for candidate in current.get("_v66_raw_candidates", []) or []:
            if not isinstance(candidate, dict):
                continue
            code = str(candidate.get("code", "")).zfill(6)
            if not code:
                continue
            fresh = dict(candidate)
            first_seen = (candidates.get(code) or {}).get("_v66_first_seen_at")
            fresh["_v66_first_seen_at"] = first_seen or captured_at.isoformat(timespec="seconds")
            fresh["_v66_last_seen_at"] = captured_at.isoformat(timespec="seconds")
            candidates[code] = fresh
        merged["_v66_raw_candidates"] = list(candidates.values())
        by_sector[sector] = merged
    return list(by_sector.values())


def v66_capture_closed_loop_snapshot(sector_df, radar, regime_cn, market_avg):
    """保存最新板块截面并累计全天真实候选；失败不影响V6.5盘中扫描。"""
    if not V66_CLOSED_LOOP_AVAILABLE:
        return None
    try:
        captured_at = datetime.now()
        accumulated_radar = _v66_accumulate_daily_radar(radar, captured_at)
        return v66_capture_market_snapshot(
            _sector_rank_rows(sector_df),
            accumulated_radar,
            _v66_market_sentiment_payload(regime_cn, market_avg),
            regime_cn,
            captured_at=captured_at.isoformat(timespec="seconds"),
        )
    except Exception as exc:
        print("[V6.6闭环] 保存真实行情截面失败：", repr(exc))
        return None


def v64_push_intraday_tier(c, deep, source, item=None, regime_cn="正常"):
    """V6.4 两级盘中提醒：先观察、确认后立即提醒，且当天同级别去重。"""
    code = str(c.get("code", "")).zfill(6)
    if not code or not deep:
        return False

    tech = deep.get("tech") or {}
    flow = deep.get("flow") or {}
    force = deep.get("force") or {}
    trade = deep.get("trade") or {}
    price = float(c.get("price", 0) or 0)
    pct = float(c.get("pct", 0) or 0)
    individual = float(deep.get("individual_score", 0) or 0)
    trend = float(deep.get("trend_score", 0) or 0)
    force_score = float(force.get("score", 0) or 0)
    net = flow.get("net")
    ma20 = tech.get("ma20")

    # A/B 级板块中的早期启动，才发“观察”；全 A 异动仍必须通过深度确认。
    board_observation = (
        source == "板块共振" and item and item.get("level") in ("A", "B")
        and 1.0 <= pct < 6.5 and individual >= 60 and trend >= 55
        and force_score >= 55 and (not ma20 or price >= float(ma20))
        and (net is None or float(net) > -50000000)
    )
    fast_breakout = (
        source == "全A独立异动"
        and 0.8 <= float(c.get("move", 0) or 0)
        and float(c.get("amount_delta", 0) or 0) >= 20000000
        and force_score >= 70
        and 1.0 <= pct <= 6.5
    )
    if deep.get("confirmed"):
        tier = "confirm"
    elif board_observation:
        tier = "observe"
    elif fast_breakout:
        tier = "fast"
    else:
        return False

    # Feed every eligible structured discovery into V8 Research Shadow before
    # legacy message de-duplication.  The adapter persists its own decision and
    # has no authority to mutate the copied V6.6 candidate or return path.
    if V8_SIDECAR_AVAILABLE and v8_handle_legacy_event is not None:
        try:
            v8_handle_legacy_event(
                candidate=dict(c), deep=dict(deep), tier=tier, source=source,
                sector_item=dict(item or {}), market_regime=regime_cn,
            )
        except Exception as _v8_sidecar_error:
            print("[V8旁路] 候选评估降级，不影响发现扫描：", type(_v8_sidecar_error).__name__)

    # V7.1统一通道读取结构化数据，不解析最终微信文字。任何异常只影响
    # Shadow融合提示，绝不改变V6.6原评分、候选和推送逻辑。
    if V7_SHADOW_AVAILABLE and v7_handle_v66_intraday_event is not None:
        try:
            v7_handle_v66_intraday_event(
                candidate=c, deep=deep, tier=tier, source=source,
                sector_item=item or {}, market_regime=regime_cn,
            )
        except Exception as _v7_fusion_error:
            print("[V7.1统一盘中通道] 已降级，不影响V6.6：", type(_v7_fusion_error).__name__)

    # 快速突破由V7独立通道负责展示，避免被旧版“深度确认”排版误导。
    if tier == "fast":
        return True

    today = datetime.now().strftime("%Y-%m-%d")
    state = _load_json_file(V64_INTRADAY_ALERT_STATE_FILE, {})
    key = f"{today}|{tier}|{source}|{code}"
    if state.get(key):
        return False

    sector = (item or {}).get("sector") or "-"
    if tier == "confirm":
        title = "🟢 A股机会雷达 V6.6 Pro｜可建仓·深度确认"
        action = "允许测试仓建仓；当前价作为系统模拟成本。若看到消息时已明显拉升，请等待回踩，不追高。"
    else:
        title = "🟡 A股机会雷达 V6.4｜早期预警观察"
        action = "仅观察，尚未达到买点确认；等待深度确认，不追高。"

    lines = [
        title,
        "",
        f"{c.get('name', '')} {code}｜{sector}",
        f"现价 {price:.2f}｜涨幅 {pct:+.2f}%｜市场 {regime_cn}",
        f"个股 {individual:.0f}｜趋势 {trend:.0f}｜主力 {force_score:.0f}",
        f"资金 {_fmt_money(net)}｜盈亏比 {trade.get('rr', '-')}:1",
        f"防守 {trade.get('defense', '-')}｜目标 {trade.get('target1', '-')}",
        v66_candidate_shadow_annotation(c, deep),
        "",
        "【系统状态：首次确认·建立持仓跟踪】" if tier == "confirm" else "【系统状态：未建仓观察】",
        f"【模拟成本：{price:.2f}｜持有日：D0】" if tier == "confirm" else "尚未出现深度确认，不作为买入信号。",
        f"👉 操作：{action}",
        (f"🛡 退出防守：{trade.get('defense', '-')}｜🎯 参考目标：{trade.get('target1', '-')}" if tier == "confirm" else ""),
        "📌 后续洗盘、转弱、失效和止盈提醒将关联本次信号。" if tier == "confirm" else "",
    ]
    if not push_message("\n".join(lines)):
        return False

    state[key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 只保留近七天的去重记录。
    keep_prefixes = {(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)}
    state = {k: v for k, v in state.items() if k.split("|", 1)[0] in keep_prefixes}
    safe_write_json(V64_INTRADAY_ALERT_STATE_FILE, state)
    print(f"[V6.4盘中提醒] {tier} 已推送：{c.get('name', '')} {code}")
    return True


def register_v6_candidate(c, bscore, rise, force, trade, source):
    """登记候选，并保存V6.1关键位，供D0~T+7跟踪。"""
    if STATS_V2_AVAILABLE:
        stats_record_candidate(
            c, source, "最终候选", buy_score=bscore,
            rise_score=rise.get("score"), force_label=force.get("label"),
            defense=trade.get("defense"), target1=trade.get("target1"),
        )
    data = _load_json_file(V6_HISTORY_FILE, [])
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{today}|{c.get('code')}|{source}"
    if any(x.get("key") == key for x in data):
        return
    tech = get_stock_tech_info(str(c.get("code", "")))
    levels = v61_key_levels(float(c.get("price", 0)), tech, trade)
    data.append({
        "key": key, "date": today, "time": datetime.now().strftime("%H:%M:%S"),
        "code": c.get("code"), "name": c.get("name"), "source": source,
        "entry": float(c.get("price", 0)), "buy_score": bscore,
        "rise_score": rise.get("score"), "force": force.get("label"),
        "defense": trade.get("defense"), "target1": trade.get("target1"),
        "trend_target": trade.get("trend_target"), "levels": levels,
        "closed": False,
    })
    data = data[-500:]
    safe_write_json(V6_HISTORY_FILE, data)


def _trade_day_age(date_text, today_text=None):
    """优先使用A股交易日历；接口不可用时退化为工作日计数。"""
    from datetime import datetime as _dt, timedelta as _td
    today_text = today_text or datetime.now().strftime("%Y-%m-%d")
    try:
        cal = ak.tool_trade_date_hist_sina()
        ds = {str(x)[:10] for x in cal["trade_date"].astype(str).tolist()}
        a = _dt.strptime(date_text, "%Y-%m-%d").date()
        b = _dt.strptime(today_text, "%Y-%m-%d").date()
        return sum(1 for d in ds if a < _dt.strptime(d, "%Y-%m-%d").date() <= b)
    except Exception:
        a = _dt.strptime(date_text, "%Y-%m-%d").date()
        b = _dt.strptime(today_text, "%Y-%m-%d").date()
        n = 0
        d = a + _td(days=1)
        while d <= b:
            if d.weekday() < 5:
                n += 1
            d += _td(days=1)
        return n


def v61_candidate_watch_if_due():
    """盘中持续盯候选；只在买点触发、突破或失效时推送，避免刷屏。"""
    global _last_v61_buy_watch
    now_ts = time.time()
    if now_ts - _last_v61_buy_watch < V61_BUY_WATCH_SECONDS:
        return
    _last_v61_buy_watch = now_ts
    history = _load_json_file(V6_HISTORY_FILE, [])
    state = _load_json_file(V61_TRACK_STATE_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    notices = []
    for x in history[-100:]:
        code = str(x.get("code", ""))
        if not code or code in POSITIONS or x.get("closed"):
            continue
        age = _trade_day_age(x.get("date", today), today)
        if age > V6_MAX_TRACK_DAYS:
            continue
        try:
            q = get_tencent_quote(code)
            if not q or q.get("price",0) <= 0:
                continue
            price = float(q["price"]); pct = float(q.get("pct",0))
            key = x.get("key")
            st = state.get(key, {}) if isinstance(state.get(key, {}), dict) else {}
            st["min"] = min(float(st.get("min", price)), price)
            st["max"] = max(float(st.get("max", price)), price)
            prev = float(st.get("prev", price))
            levels = x.get("levels") or v61_key_levels(price, get_stock_tech_info(code), x)
            s1,s2,r1,r2 = [levels.get(k) for k in ("support1","support2","resist1","resist2")]
            flow = get_individual_main_flow(code); net = flow.get("net")
            status = st.get("status", "")
            if STATS_V2_AVAILABLE:
                stats_update_tracking(x.get("source", ""), code, x.get("date", today), price, age, status)
            if r1 and price < float(r1) * 0.999:
                st["breakout_confirm"] = 0
            new_status = status
            title = None
            body = []
            if s2 and price < float(s2):
                new_status = "INVALID"
                if status != new_status:
                    title = f"❌ A股机会雷达 V6.3 Final｜信号失效"
                    body = [f"{x.get('name','')} {code}", f"💰 信号{x.get('entry',0):.2f}｜现价{price:.2f}｜{(price/float(x.get('entry') or price)-1)*100:+.2f}%",
                            f"🔴 已跌破强支撑{s2:.2f}｜💵主力{_fmt_money(net)}", "", "👉 现在怎么做", "未买 → ❌ 取消本次建仓", "已买 → 🔴 检查减仓/止损，暂不补仓", "", "📌 本轮信号结束跟踪。"]
            elif r1 and price >= float(r1) * 1.002 and (net is None or net > 0):
                # V6.3 二次确认：连续2轮站上压力，且诱多/派发风险不过高，才触发买点。
                st["breakout_confirm"] = int(st.get("breakout_confirm", 0)) + 1
                behavior = v63_behavior_diagnosis(code, price, pct, {"high":st.get("max",price),"low":st.get("min",price),"prev":prev,"r1":r1,"s1":s1,"s2":s2})
                if st["breakout_confirm"] >= 2 and behavior.get("trap",0) < 70 and behavior.get("distribution",0) < 75:
                    new_status = "BUY_TRIGGER"
                else:
                    new_status = status
                    # FilterStats：只有已经满足“连续两轮突破”，却因诱多/派发风险被拦下时才登记。
                    if st["breakout_confirm"] >= 2:
                        if behavior.get("trap",0) >= 70:
                            _v63_register_filtered_candidate(x, price, behavior, "诱多过滤")
                        elif behavior.get("distribution",0) >= 75:
                            _v63_register_filtered_candidate(x, price, behavior, "派发过滤")
                if status != new_status and new_status == "BUY_TRIGGER":
                    if STATS_V2_AVAILABLE:
                        stats_record_candidate(
                            {"code": code, "name": x.get("name", ""), "price": price},
                            x.get("source", ""), "买点触发",
                            buy_score=x.get("buy_score"), rise_score=x.get("rise_score"),
                        )
                    title = "🟢 A股机会雷达 V6.3 Final｜买点触发"
                    body = [f"{x.get('name','')} {code}", f"💰 {price:.2f}｜今日{pct:+.2f}%｜⭐{x.get('buy_score','-')}｜🚀主升{x.get('rise_score','-')}",
                            f"✅ 放量/价格站上{r1:.2f}附近｜💵主力{_fmt_money(net)}", "", "👉 操作", "🟢 可考虑小仓试错，先控制在1成左右", f"🛡 防守{s2:.2f}" if s2 else "", f"🎯 下一压力{r2:.2f}" if r2 else "", "", "📌 结论：买点条件已触发，仍需结合盘口确认。"]
            elif s1 and st["min"] <= float(s1)*1.005 and price >= float(s1)*1.012 and price > prev and (net is None or net > -50000000):
                new_status = "SUPPORT_REBOUND"
                if status != new_status:
                    title = "🟢 A股机会雷达 V6.3 Final｜支撑后转强"
                    body = [f"{x.get('name','')} {code}", f"💰 {price:.2f}｜今日{pct:+.2f}%", f"✅ {s1:.2f}附近未破，价格重新上涨", f"💵 主力{_fmt_money(net)}", "", "👉 操作", "🟢 可考虑1成试仓", f"🛡 跌破{s2:.2f}则取消" if s2 else "", f"🎯 先看{r1:.2f}" if r1 else ""]
            st["prev"] = price
            st["status"] = new_status
            state[key] = st
            if title:
                if title.startswith("❌"):
                    push_message("\n".join([title, ""] + [z for z in body if z != ""]))
                else:
                    kind = "买点确认" if new_status == "BUY_TRIGGER" else "支撑后转强"
                    notices.append([
                        f"🟢 {x.get('name','')} {code}｜{kind}",
                        f"现价{price:.2f}｜今日{pct:+.2f}%｜主力{_fmt_money(net)}",
                        f"支撑{s1 if s1 else '-'}｜压力{r1 if r1 else '-'}｜防守{s2 if s2 else '-'}",
                        "👉 可考虑小仓试错，先结合盘口确认。",
                    ])
                changed = True
        except Exception:
            continue
    if notices:
        lines = _compact_summary_lines(
            ["🟢 A股机会雷达 V6.3 Final｜盘中机会汇总", f"本轮{len(notices)}只｜{datetime.now():%H:%M}"],
            notices,
        )
        push_message("\n".join(lines))
    if changed or state:
        safe_write_json(V61_TRACK_STATE_FILE, state)

def v6_t1_diagnosis_if_due():
    """V6.1候选D0~T+7闭环：风险即时提醒，普通复核同轮汇总。"""
    global _last_t1_check
    now_ts = time.time()
    if now_ts - _last_t1_check < V6_T1_CHECK_SECONDS:
        return
    _last_t1_check = now_ts
    history = _load_json_file(V6_HISTORY_FILE, [])
    state = _load_json_file(V61_TRACK_STATE_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    notices = []
    for x in history[-100:]:
        code = str(x.get("code", ""))
        if not code or x.get("date") >= today or code in POSITIONS:
            continue
        age = _trade_day_age(x.get("date", today), today)
        if age <= 0 or age > V6_MAX_TRACK_DAYS:
            continue
        key = x.get("key"); st = state.get(key, {}) if isinstance(state.get(key, {}), dict) else {}
        if st.get("closed"):
            continue
        # 已完成当天T节点的候选由盘中候选监控负责风险检查；这里不再重复请求深度数据。
        if age in (1, 3, 5, 7) and st.get(f"T{age}"):
            continue
        try:
            q = get_tencent_quote(code)
            if not q or q.get("price",0) <= 0: continue
            tech = get_stock_tech_info(code); flow = get_individual_main_flow(code)
            price=float(q["price"]); pct=float(q.get("pct",0)); entry=float(x.get("entry") or price)
            pnl=(price/entry-1)*100 if entry>0 else 0
            if STATS_V2_AVAILABLE:
                stats_update_tracking(x.get("source", ""), code, x.get("date", today), price, age, st.get("status", ""))
            st["min"] = min(float(st.get("min", price)), price); st["max"] = max(float(st.get("max", price)), price)
            levels=x.get("levels") or v61_key_levels(price, tech, x)
            s1,s2,r1,r2=[levels.get(k) for k in ("support1","support2","resist1","resist2")]
            net=flow.get("net"); vr=tech.get("volume_ratio")
            invalid = (s2 and price < float(s2)) or (x.get("defense") and price < float(x.get("defense"))*0.995)
            if invalid and not st.get("closed"):
                msg=["❌ A股机会雷达 V6.3 Final｜信号失效", "", f"{x.get('name','')} {code}", f"信号{entry:.2f}｜现价{price:.2f}｜收益{pnl:+.2f}%", f"🔴 跌破关键防守｜💵主力{_fmt_money(net)}", "", "👉 操作：未买取消；已买检查减仓/止损，不补仓。", "📌 本轮信号提前结束。"]
                push_message("\n".join(msg)); st["closed"]=True; st["result"]="invalid"; changed=True
            elif age in (1,3,5,7) and not st.get(f"T{age}"):
                if pnl >= 3 or (r1 and price >= r1):
                    label="🟢 趋势延续"; action=f"{s1:.2f}以上不破 → 继续持有观察" if s1 else "继续持有观察"
                elif pct < 0 and (net is None or net > -50000000):
                    label="🟡 调整观察"; action=f"重点看{s1:.2f}，不破先观察，不急补仓" if s1 else "先观察，不急补仓"
                else:
                    label="🟡 待确认"; action="等价格和资金重新同步转强"
                if age < 7:
                    notices.append([
                        f"{label} {x.get('name','')} {code}｜T+{age}",
                        f"收益{pnl:+.2f}%｜现价{price:.2f}｜信号{entry:.2f}",
                        f"支撑{s1 if s1 else '-'}｜压力{r1 if r1 else '-'}｜主力{_fmt_money(net)}",
                        f"👉 {action}",
                    ])
                else:
                    maxp=float(st.get("max",price)); minp=float(st.get("min",price))
                    maxret=(maxp/entry-1)*100; dd=(minp/entry-1)*100
                    result="✅ 成功信号" if pnl>0 else "❌ 未盈利信号"
                    notices.append([
                        f"📋 {x.get('name','')} {code}｜T+7结案｜{result}",
                        f"最终{pnl:+.2f}%｜现价{price:.2f}｜信号{entry:.2f}",
                        f"最高{maxp:.2f}({maxret:+.2f}%)｜最低{minp:.2f}({dd:+.2f}%)",
                    ])
                    st["closed"]=True; st["result"]="success" if pnl>0 else "failed"
                st[f"T{age}"]=True; changed=True
            state[key]=st
        except Exception:
            continue
    if notices:
        priority = {"❌": 0, "🟡": 1, "🟢": 2, "📋": 3}
        notices.sort(key=lambda block: priority.get(block[0][:1], 9))
        lines = _compact_summary_lines(
            ["🔎 A股机会雷达 V6.3 Final｜T+跟踪汇总", f"本轮{len(notices)}只｜{datetime.now():%m-%d %H:%M}"],
            notices,
        )
        push_message("\n".join(lines))
    if changed or state:
        safe_write_json(V61_TRACK_STATE_FILE, state)

def scan_auction_if_due():
    """9:25附近盘前快照：只做观察池，不作为直接买入指令。每日一次。"""
    now = datetime.now()
    hm = now.hour * 60 + now.minute
    if not (9 * 60 + 24 <= hm <= 9 * 60 + 29):
        return
    state = _load_json_file(V6_AUCTION_STATE_FILE, {})
    key = now.strftime("%Y-%m-%d")
    if state.get(key):
        return
    try:
        df = _v63_realtime_spot_dataframe()
        if df is None:
            return
        items = []
        for _, row in df.iterrows():
            code = normalize_sina_code(row.get("代码", "")); name = str(row.get("名称", "")).strip()
            pct = to_float(row.get("涨跌幅")); amount = to_float(row.get("成交额")); price = to_float(row.get("最新价"))
            if not code or price <= 0 or "ST" in name.upper() or "退" in name:
                continue
            if 1.0 <= pct <= 7.0 and amount >= 10000000:
                score = auction_score(pct, amount) + min(amount / 100000000, 5) * 5
                items.append({"code": code, "name": name, "pct": pct, "amount": amount, "price": price, "score": score})
        items.sort(key=lambda z: (z["score"], z["amount"]), reverse=True)
        picks = items[:6]
        if picks:
            lines = ["⚡ A股机会雷达 V6.3 Final｜9:25集合竞价观察", "", "竞价/盘前快照仅用于开盘观察，不直接追价。"]
            for i, x in enumerate(picks, 1):
                lines += ["", "━━━━━━━━━━━━", f"{i}. {x['name']} {x['code']}", f"📈 盘前涨幅：{x['pct']:+.2f}%", f"💰 当前成交额：{x['amount']/10000:.0f}万", f"⭐ 观察评分：{min(100, round(x['score'],1))}/100"]
            push_message("\n".join(lines))
        state[key] = True
        safe_write_json(V6_AUCTION_STATE_FILE, state)
    except Exception as e:
        print("[V6竞价] 盘前快照失败：", repr(e))


def push_daily_top_picks(radar, regime_cn, market_avg):
    """V6.3高质量精选：数据横排、操作竖排，手机第一屏先看结论。"""
    global last_pick_signature
    pool=[]
    for item in radar:
        if sector_streak[item["sector"]] < CONFIRM_SCANS: continue
        quality=signal_quality(item,"WEAK" if regime_cn=="偏弱" else ("STRONG" if regime_cn=="偏强" else "NORMAL"))
        if quality<PICK_SCORE_MIN: continue
        for c in item.get("candidates",[]): pool.append((quality+c["score"]*0.08,item,c))
    pool.sort(key=lambda x:x[0],reverse=True); picks=pool[:MAX_DAILY_PICKS]
    if not picks: return
    signature=tuple((x[1]["sector"],x[2]["code"]) for x in picks)
    if signature==last_pick_signature: return
    last_pick_signature=signature
    lines=["⭐ A股机会雷达 V6.4｜深度确认精选", f"市场{regime_cn}｜本轮{len(picks)}只"]
    for i,(_,item,c) in enumerate(picks,1):
        regime="WEAK" if regime_cn=="偏弱" else ("STRONG" if regime_cn=="偏强" else "NORMAL")
        plan=candidate_plan(c); deep=c.get("_v64") or v64_deep_confirm(c,item=item,regime=regime,source="板块共振")
        bscore,tech,flow,force,trade=deep["buy_score"],deep["tech"],deep["flow"],deep["force"],deep["trade"]
        rise={"score":deep["trend_score"],"stage":"V6.4个股趋势确认"}; levels=v61_key_levels(c["price"],tech,trade)
        status,note=v61_position_label(c["price"],levels,bscore,rise.get("score",0))
        register_v6_candidate(c,bscore,rise,force,trade,"板块共振"); event_watch_codes.add(c["code"])
        register_signal("V6.4板块深度确认",c["code"],c["name"],c["price"],bscore,f"{item['sector']}|个股{deep['individual_score']:.0f}|趋势{deep['trend_score']:.0f}|RR{trade['rr']}")
        s1,s2,r1,r2=[levels.get(k) for k in ("support1","support2","resist1","resist2")]
        lines += ["","━━━━━━━━━━━━",f"{i}. {c['name']} {c['code']}｜{item['sector']}",f"💰 {c['price']:.2f}｜{c['pct']:+.2f}%｜买点{bscore:.0f}｜个股{deep['individual_score']:.0f}｜趋势{deep['trend_score']:.0f}",f"💵 主力{_fmt_money(flow.get('net'))}｜{force['label']}｜量能{tech.get('volume_ratio','-')}",f"【当前判断】{status}｜{note}","", "📈 均线",f"MA5 {tech.get('ma5','-')}｜MA10 {tech.get('ma10','-')}｜MA20 {tech.get('ma20','-')}",f"MA30 {tech.get('ma30','-')}｜MA60 {tech.get('ma60','-')}","", "🎯 关键价格",f"支撑{s1:.2f}｜强支撑{s2:.2f}",f"压力{r1:.2f}｜强压力{r2:.2f}",f"距支撑{levels['gap_support']:+.2f}%｜距压力{levels['gap_resist']:+.2f}%｜ATR {deep['atr']:.3f}",""]
        lines += [z for z in v61_action_lines(levels,"可考虑1成试仓") if z!=""]
        lines += ["",f"🛡 防守{s2:.2f}｜🎯目标{r2:.2f}/{trade.get('trend_target','-')}",f"⚖ 盈亏比{trade['rr']}:1",f"📌 一句话：{note}"]
    pushed_ok = push_message("\n".join(lines))
    if STATS_V2_AVAILABLE and pushed_ok:
        for _, item, c in picks:
            stats_record_candidate(c, "板块共振", "已推送",
                                   sector=item.get("sector"), market_regime=regime_cn)

def get_market_snapshot():
    """读取主要指数，给板块信号增加大盘环境过滤。"""
    out = {}
    for name, code in MARKET_CODES.items():
        symbol = ("sh" + code) if code.startswith("0") else ("sz" + code)
        try:
            url = "https://qt.gtimg.cn/q=" + symbol
            r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            r.encoding = "gbk"
            data = r.text.split('"')[1].split("~")
            price = to_float(data[3])
            prev_close = to_float(data[4])
            pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
            old = previous_market_pct.get(name)
            accel = 0.0 if old is None else pct - old
            previous_market_pct[name] = pct
            out[name] = {"price": price, "pct": pct, "accel": accel}
        except Exception:
            continue
    return out


def market_regime(snapshot):
    """返回 STRONG / NORMAL / WEAK，弱市提高板块触发门槛。"""
    if not snapshot:
        return "NORMAL", 0.0

    pcts = [x["pct"] for x in snapshot.values()]
    avg = sum(pcts) / len(pcts)

    if avg <= -1.20 or sum(1 for x in pcts if x <= -1.0) >= 2:
        return "WEAK", avg
    if avg >= 0.80 and sum(1 for x in pcts if x >= 0.6) >= 2:
        return "STRONG", avg
    return "NORMAL", avg


def candidate_plan(c):
    """把候选股变成可执行的观察区间 + 触发条件 + 失效条件。"""
    price = c["price"]
    pct = c["pct"]

    if price <= 0:
        return {
            "action": "仅观察",
            "zone": "-",
            "trigger": "等待同花顺确认价格与量能",
            "invalid": "-",
        }

    # 不追明显加速后的高位价格；更偏回踩承接或小幅突破确认。
    if pct >= 5.8:
        action = "不追高"
        zone = f"{price * 0.975:.2f}-{price * 0.990:.2f}"
        trigger = "回落后重新站稳分时均价线，且板块仍保持强势"
    elif pct >= 2.0:
        action = "观察/试仓"
        zone = f"{price * 0.990:.2f}-{price * 1.005:.2f}"
        trigger = "放量突破当前价附近后回踩不破，或分时二次转强"
    else:
        action = "观察/试仓"
        zone = f"{price * 0.995:.2f}-{price * 1.010:.2f}"
        trigger = "价格翻红并放量，板块加速度与广度继续增强"

    invalid = f"{price * 0.970:.2f}附近或板块异动明显退潮"
    return {
        "action": action,
        "zone": zone,
        "trigger": trigger,
        "invalid": invalid,
    }


def maybe_push_market_alert(snapshot, regime, avg_pct):
    """只推送明显的大盘风险/转强，不制造日常噪音。"""
    global last_market_alert
    if not snapshot:
        return

    max_accel = max((x["accel"] for x in snapshot.values()), default=0.0)
    min_accel = min((x["accel"] for x in snapshot.values()), default=0.0)
    trigger = None

    if regime == "WEAK" and min_accel <= -0.35:
        trigger = "⚠️ 大盘风险升高"
    elif regime == "STRONG" and max_accel >= 0.30:
        trigger = "📈 大盘同步转强"

    if not trigger:
        return

    now_ts = time.time()
    if now_ts - last_market_alert < MARKET_ALERT_COOLDOWN:
        return
    last_market_alert = now_ts

    lines = [
        trigger,
        "",
        f"📊 三大指数均值：{avg_pct:+.2f}%",
    ]
    for name, x in snapshot.items():
        lines.append(f"{name}：{x['pct']:+.2f}%   本轮：{x['accel']:+.2f}%")
    lines += [
        "",
        "💡 用途：调整进攻/防守级别，不作为单独买入依据。",
    ]
    push_message("\n".join(lines))

def beep():
    try:
        import winsound
        winsound.Beep(1350, 450)
        winsound.Beep(1650, 450)
    except Exception:
        print("\a", end="")


def save_log(level, sector, sector_pct, accel, net_inflow, breadth, candidates):
    new_file = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow([
                "时间", "等级", "板块", "板块涨跌幅", "较上次加速",
                "净流入亿元", "上涨/下跌比", "候选股"
            ])

        names = "；".join(
            f'{x["name"]}({x["code"]}) {x["pct"]:+.2f}%'
            for x in candidates
        )

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            level, sector, round(sector_pct, 2), round(accel, 2),
            round(net_inflow, 2), round(breadth, 2), names
        ])


def score_sector(row, accel):
    pct = to_float(row.get("涨跌幅"))
    net = to_float(row.get("净流入"))
    up = max(to_float(row.get("上涨家数")), 0.0)
    down = max(to_float(row.get("下跌家数")), 0.0)
    breadth = up / max(down, 1.0)
    leader_pct = to_float(row.get("领涨股-涨跌幅"))
    amount = to_float(row.get("总成交额"))

    score = 0

    # 板块本身不是已经大涨才算，重点看“加速度”
    if pct >= MIN_SECTOR_PCT:
        score += 1
    if accel >= MIN_ACCEL:
        score += 2
    if accel >= 0.50:
        score += 1

    if net > MIN_NET_INFLOW:
        score += 1
    if net >= 2.0:
        score += 1

    if breadth >= MIN_BREADTH:
        score += 1
    if breadth >= 2.0:
        score += 1

    if 2.0 <= leader_pct <= 9.8:
        score += 1

    if amount >= 10.0:
        score += 1

    if score >= 7:
        level = "A"
    elif score >= 5:
        level = "B"
    elif score >= 4:
        level = "C"
    else:
        level = "-"

    return {
        "score": score,
        "level": level,
        "pct": pct,
        "net": net,
        "breadth": breadth,
        "leader": str(row.get("领涨股", "")),
        "leader_pct": leader_pct,
        "amount": amount,
    }


def build_candidates(ljqs_df, sector, sector_score):
    if ljqs_df is None or ljqs_df.empty:
        return []

    x = ljqs_df[ljqs_df["所属行业"].astype(str) == str(sector)].copy()
    if x.empty:
        return []

    result = []

    for _, row in x.iterrows():
        code = str(row.get("股票代码", "")).zfill(6)
        name = str(row.get("股票简称", ""))
        days = int(to_float(row.get("量价齐升天数"), 99))
        stage = to_float(row.get("阶段涨幅"))
        turnover = to_float(row.get("累计换手率"))

        # 更偏“刚启动”，不追已经连续涨了很多天/阶段涨幅过大的票
        if days > 3:
            continue
        if stage < -1.0 or stage > 15.0:
            continue
        if turnover > 45.0:
            continue

        candidate_score = sector_score

        if days == 1:
            candidate_score += 3
        elif days == 2:
            candidate_score += 2
        elif days == 3:
            candidate_score += 1

        if 0.5 <= stage <= 8.0:
            candidate_score += 2
        elif 8.0 < stage <= 12.0:
            candidate_score += 1

        if 2.0 <= turnover <= 20.0:
            candidate_score += 1

        quote = None
        try:
            quote = get_tencent_quote(code)
        except Exception:
            quote = None

        if quote:
            pct = quote["pct"]
            price = quote["price"]
            # 避免已经明显追高
            if 0.3 <= pct <= 6.5:
                candidate_score += 2
            elif -0.8 <= pct < 0.3:
                candidate_score += 1
        else:
            pct = stage
            price = to_float(row.get("最新价"))

        result.append({
            "code": code,
            "name": name,
            "price": price,
            "pct": pct,
            "days": days,
            "stage": stage,
            "turnover": turnover,
            "score": candidate_score,
        })

    result.sort(key=lambda d: d["score"], reverse=True)
    return result[:TOP_STOCKS_PER_SECTOR]


def _sector_rank_rows(sector_df):
    """把真实板块表标准化；供温度计展示和V6.6收盘预测共同使用。"""
    rows = []
    for _, row in sector_df.iterrows():
        sector = str(row.get("板块", "")).strip()
        if not sector:
            continue
        info = score_sector(row, 0.0)
        up = max(to_float(row.get("上涨家数")), 0.0)
        down = max(to_float(row.get("下跌家数")), 0.0)
        limit_up_raw = row.get("涨停家数")
        limit_up_count = None
        if limit_up_raw is not None and str(limit_up_raw).strip() not in ("", "-", "--", "nan", "None"):
            limit_up_count = int(max(to_float(limit_up_raw), 0))
        rows.append({
            "sector": sector,
            "pct": round(info["pct"], 2),
            "net": round(info["net"], 2),
            "breadth": round(info["breadth"], 2),
            "up": int(up),
            "down": int(down),
            "amount": round(float(info.get("amount", 0) or 0), 2),
            "leader": str(info.get("leader", "")),
            "leader_pct": round(float(info.get("leader_pct", 0) or 0), 2),
            # 行情源没有该列时必须记为 None，不能用 0 冒充“确实没有涨停”。
            "limit_up_count": limit_up_count,
            "constituent_count": int(up + down) if up + down > 0 else None,
        })
    return rows


def update_and_maybe_push_sector_report(sector_df, regime_cn):
    """保存最新板块快照，并在09:40后发送一次早盘温度计。"""
    global _last_sector_snapshot_at
    now = datetime.now()
    rows = _sector_rank_rows(sector_df)
    if not rows:
        return
    if time.time() - _last_sector_snapshot_at >= 60:
        data = _load_json_file(SECTOR_REPORT_FILE, {})
        data["latest"] = {"time": now.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}
        data.setdefault(now.strftime("%Y-%m-%d"), {})["latest"] = data["latest"]
        safe_write_json(SECTOR_REPORT_FILE, data)
        _last_sector_snapshot_at = time.time()

    day = now.strftime("%Y-%m-%d")
    data = _load_json_file(SECTOR_REPORT_FILE, {})
    day_state = data.setdefault(day, {})
    report_window = (now.hour == 9 and now.minute >= 40) or (10 <= now.hour < 15)
    if not report_window or day_state.get("morning_pushed"):
        return

    strongest = sorted(rows, key=lambda x: (x["pct"], x["net"], x["breadth"]), reverse=True)[:3]
    weakest = sorted(rows, key=lambda x: (x["pct"], x["net"]))[:3]
    inflow = sorted(rows, key=lambda x: x["net"], reverse=True)[:3]
    outflow = sorted(rows, key=lambda x: x["net"])[:3]
    report_name = "早盘板块温度计" if now.hour < 11 else "盘中板块温度计"
    lines = [f"🌅 A股机会雷达｜{report_name}", f"市场{regime_cn}｜{now:%m-%d %H:%M}", "", "🔥 最强板块"]
    for i, x in enumerate(strongest, 1):
        lines.append(f"{i}. {x['sector']} {x['pct']:+.2f}%｜资金{x['net']:+.2f}亿｜广度{x['breadth']:.2f}")
    lines.extend(["", "❄️ 最弱板块"])
    for i, x in enumerate(weakest, 1):
        lines.append(f"{i}. {x['sector']} {x['pct']:+.2f}%｜资金{x['net']:+.2f}亿｜广度{x['breadth']:.2f}")
    lines.extend(["", "💰 流入前三｜" + "｜".join(f"{x['sector']} {x['net']:+.1f}亿" for x in inflow)])
    lines.append("💸 流出前三｜" + "｜".join(f"{x['sector']} {x['net']:+.1f}亿" for x in outflow))
    lines.append("📌 仅作早盘观察，不直接作为买入信号。")
    if push_message("\n".join(lines)):
        day_state["morning_pushed"] = True
        day_state["morning"] = {"time": now.strftime("%H:%M:%S"), "rows": rows}
        data[day] = day_state
        safe_write_json(SECTOR_REPORT_FILE, data)


def scan_once():
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 92)
    print("A股机会雷达扫描：", now_text)
    print("正在读取同花顺行业强弱与量价齐升数据...")

    market = get_market_snapshot()
    regime, market_avg = market_regime(market)
    maybe_push_market_alert(market, regime, market_avg)

    regime_cn = {"STRONG": "偏强", "NORMAL": "中性", "WEAK": "偏弱"}.get(regime, regime)
    print(f"大盘环境：{regime_cn}｜三大指数平均 {market_avg:+.2f}%")
    scan_full_market_if_due(regime_cn)

    sector_df = ak.stock_board_industry_summary_ths()
    ljqs_df = ak.stock_rank_ljqs_ths()
    update_and_maybe_push_sector_report(sector_df, regime_cn)

    radar = []

    for _, row in sector_df.iterrows():
        sector = str(row.get("板块", ""))
        pct = to_float(row.get("涨跌幅"))

        old_pct = previous_sector_pct.get(sector)
        accel = 0.0 if old_pct is None else pct - old_pct
        previous_sector_pct[sector] = pct

        # 第一次扫描没有“加速度”，先建立基线
        if old_pct is None:
            continue

        info = score_sector(row, accel)

        # 弱市提高门槛：只抓真正逆势突出的板块；强市保持早发现。
        pct_gate = 0.90 if regime == "WEAK" else MIN_SECTOR_PCT
        accel_gate = 0.35 if regime == "WEAK" else MIN_ACCEL
        breadth_gate = 1.60 if regime == "WEAK" else MIN_BREADTH

        if (
            info["pct"] >= pct_gate
            and accel >= accel_gate
            and info["breadth"] >= breadth_gate
            and info["level"] != "-"
        ):
            candidates = build_candidates(
                ljqs_df,
                sector,
                info["score"]
            )
            if V64_STATS_AVAILABLE:
                for c in candidates:
                    v64_record_discovery(c, "板块共振", sector=sector,
                                         market_regime=regime_cn, sector_score=info.get("score"))
            if STATS_V2_AVAILABLE:
                for c in candidates:
                    stats_record_candidate(c, "板块共振", "初级候选",
                                           sector=sector, market_regime=regime_cn,
                                           sector_score=info.get("score"))

            radar.append({
                "sector": sector,
                "accel": accel,
                **info,
                "candidates": candidates,
            })

    active_sectors = {x["sector"] for x in radar}
    for sector in list(sector_streak.keys()):
        if sector not in active_sectors:
            sector_streak[sector] = 0
    for item in radar:
        sector_streak[item["sector"]] += 1
        item["quality"] = signal_quality(item, regime)

    radar.sort(
        key=lambda d: (d["quality"], d["score"], d["accel"], d["net"]),
        reverse=True
    )

    radar = radar[:TOP_SECTORS]

    if STATS_V2_AVAILABLE:
        for item in radar:
            for c in item.get("candidates", []):
                stats_record_candidate(c, "板块共振", "最终候选",
                                       sector=item.get("sector"), market_regime=regime_cn,
                                       sector_level=item.get("level"), quality=item.get("quality"))

    if not radar:
        # 即使没有候选，也保存真实板块收盘截面；15:05会形成“0候选”记录，
        # 不会为了凑数生成股票。
        v66_capture_closed_loop_snapshot(sector_df, [], regime_cn, market_avg)
        print("本轮没有达到条件的“板块加速”信号。")
        print("这不是坏事：宁可少提醒，也不要满屏噪音。")
        return

    print("\n【本轮异动板块】")

    sector_push_blocks = []
    for item in radar:
        print("-" * 92)
        print(
            f'[{item["level"]}] {item["sector"]} | '
            f'板块:{item["pct"]:+.2f}% | '
            f'较上次加速:{item["accel"]:+.2f}% | '
            f'净流入:{item["net"]:+.2f}亿 | '
            f'上涨/下跌:{item["breadth"]:.2f} | '
            f'领涨:{item["leader"]} {item["leader_pct"]:+.2f}% | 确认:{sector_streak[item["sector"]]}/{CONFIRM_SCANS}'
        )

        if item["candidates"]:
            print("候选观察：")
            for n, c in enumerate(item["candidates"], 1):
                print(
                    f'  {n}. {c["name"]} {c["code"]} | '
                    f'现价:{c["price"]:.2f} | 当日:{c["pct"]:+.2f}% | '
                    f'量价齐升:{c["days"]}天 | 阶段:{c["stage"]:+.2f}% | '
                    f'综合分:{c["score"]}'
                )
        else:
            print("候选观察：暂无满足“未明显追高”过滤的个股。")

        # V6.4：板块通过后再做个股深度确认，未通过者只保留在影子统计。
        regime_code = "WEAK" if regime_cn == "偏弱" else ("STRONG" if regime_cn == "偏强" else "NORMAL")
        confirmed_candidates = []
        raw_candidates = list(item.get("candidates", []))
        for c in raw_candidates:
            deep = v64_deep_confirm(c, item=item, regime=regime_code, source="板块共振")
            c["_v64"] = deep
            c["_v66_risk"] = v66_close_risk_context(deep)
            # 板块共振先给早期观察，达到全部深度条件时立即升级为确认提醒。
            # 该提醒不依赖板块连续扫描次数，减少错过启动段的概率。
            v64_push_intraday_tier(c, deep, "板块共振", item=item, regime_cn=regime_cn)
            if deep["confirmed"]:
                confirmed_candidates.append(c)
        # V6.6次日闭环需要保留真实的原始候选及其深度指标；
        # V6.5原有盘中推送仍继续只使用confirmed_candidates。
        item["_v66_raw_candidates"] = raw_candidates
        item["candidates"] = confirmed_candidates
        if not confirmed_candidates:
            print(f'[V6.4深度确认] {item["sector"]} 原始候选均被拦截，仅记录不推送。')

        # A/B 级且有深度通过个股才声音提醒。
        if item["level"] in ("A", "B") and item["candidates"] and sector_streak[item["sector"]] >= CONFIRM_SCANS:
            key = item["sector"]
            now_ts = time.time()
            if now_ts - last_alert[key] >= ALERT_COOLDOWN:
                last_alert[key] = now_ts
                beep()
                save_log(
                    item["level"],
                    item["sector"],
                    item["pct"],
                    item["accel"],
                    item["net"],
                    item["breadth"],
                    item["candidates"],
                )
                print(
                    f'>>> 【{item["level"]}级机会提醒】'
                    f'{item["sector"]} 出现同步加速，请打开同花顺复核盘口后再决定是否介入。'
                )

                # 没有通过追高过滤的候选股时，不向手机发送“建仓型”提醒。
                if not item["candidates"]:
                    continue

                lines = [
                    f"🚨 A股机会雷达 V6.4｜{item['level']}级深度确认",
                    "",
                    f"📊 市场：{regime_cn}   指数均值：{market_avg:+.2f}%",
                    f"🔥 板块：{item['sector']}   涨幅：{item['pct']:+.2f}%",
                    f"⚡ 加速：{item['accel']:+.2f}%   净流入：{item['net']:+.2f}亿",
                    f"📈 广度：{item['breadth']:.2f}",
                ]
                candidate_summaries = []

                for idx, c in enumerate(item["candidates"], 1):
                    plan = candidate_plan(c)
                    deep = c.get("_v64") or v64_deep_confirm(c, item=item, regime=regime, source="板块共振")
                    bscore, tech, flow, force, trade = deep["buy_score"], deep["tech"], deep["flow"], deep["force"], deep["trade"]
                    rise = {"score": deep["trend_score"], "stage": "V6.4个股趋势确认"}
                    register_v6_candidate(c, bscore, rise, force, trade, "板块共振")
                    if idx <= 2:
                        candidate_summaries.append(
                            f"{c['name']} {c['code']}｜{c['price']:.2f}({c['pct']:+.2f}%)｜"
                            f"买点{bscore:.0f}｜主升{rise['score']:.0f}｜防守{trade['defense'] if trade['defense'] else '-'}"
                        )
                    lines += [
                        "",
                        "━━━━━━━━━━━━",
                        f"{idx}. {c['name']} {c['code']}",
                        f"💰 现价：{c['price']:.2f}   涨幅：{c['pct']:+.2f}%",
                        f"⭐ 买点质量：{bscore:.1f}/100｜个股独立：{deep['individual_score']:.1f}/100",
                        f"🧠 {force['label']}｜💵 {_fmt_money(flow.get('net'))}",
                        f"🟢 买入：{trade['zone']}｜{trade['position']}",
                        f"🎯 确认：{trade['trigger']}",
                        f"🛡 防守：{trade['defense'] if trade['defense'] else '-'}｜🎁 目标：{trade['target1'] if trade['target1'] else '-'}",
                        f"⚖ ATR：{deep['atr']:.3f}｜风险收益比：{trade['rr']}:1",
                    ]

                lines += [
                    "",
                    "⚠️ 当前大盘偏弱，仅按小仓试错，禁止追高。"
                    if regime == "WEAK"
                    else "✅ 先复核分时、量能与板块同步，满足条件再试仓。",
                    f"🕒 时间：{datetime.now().strftime('%H:%M:%S')}",
                ]
                sector_push_blocks.append((item, candidate_summaries))

    # 深度确认结束后保存完整候选、交易计划和风险证据，供15:05闭环读取。
    v66_capture_closed_loop_snapshot(sector_df, radar, regime_cn, market_avg)

    # V7 Shadow 与 V6 使用同一轮已生成的候选快照做并行记录。
    # 默认由 V7_SHADOW_ENABLED=false 关闭；任何异常均 fail-open，不改变 V6。
    if V7_SHADOW_AVAILABLE and v7_capture_parallel_signals is not None:
        try:
            v7_counts = v7_capture_parallel_signals(radar, regime_cn, source="板块共振")
            if v7_counts.get("enabled"):
                print(f"[V7影子] 本轮并行记录 V6={v7_counts.get('v6', 0)} / V7={v7_counts.get('v7', 0)}")
        except Exception as _v7_shadow_error:
            print("[V7影子] 并行记录失败，已忽略且不影响V6：", repr(_v7_shadow_error))

    if sector_push_blocks:
        summary_blocks = []
        for item, summaries in sector_push_blocks:
            summary_blocks.append([
                f"🔥 {item['sector']}｜{item['level']}级｜涨幅{item['pct']:+.2f}%",
                f"资金{item['net']:+.2f}亿｜加速{item['accel']:+.2f}%｜广度{item['breadth']:.2f}",
                *[f"核心：{summary}" for summary in summaries],
            ])
        lines = _compact_summary_lines([
            "🚨 A股机会雷达 V6.4｜深度确认汇总",
            f"本轮{len(sector_push_blocks)}个｜市场{regime_cn}｜指数{market_avg:+.2f}%｜{datetime.now():%H:%M}",
        ], summary_blocks)
        lines.extend(["", "👉 先复核分时与承接，涨幅过高不追。"])
        pushed_ok = push_message("\n".join(lines))
        if STATS_V2_AVAILABLE and pushed_ok:
            for item, _ in sector_push_blocks:
                for c in item["candidates"]:
                    stats_record_candidate(c, "板块共振", "已推送",
                                           sector=item.get("sector"), market_regime=regime_cn)

    # V3 汇总精选：同一轮只推最值得看的 1~3 只。
    push_daily_top_picks(radar, regime_cn, market_avg)



# ===== V6.3：潜伏 / 资金轨迹 / 诱多派发 / 洗盘真跌 =====
V63_PRELAUNCH_SECONDS = 600
V63_BEHAVIOR_SECONDS = 60
V63_PRELAUNCH_FILE = "v63_prelaunch_state.json"
V63_BEHAVIOR_FILE = "v63_behavior_state.json"
V63_STATS_FILE = "v63_daily_stats.json"
# V6.3 FilterStats：仅做后台统计，不改变原选股/推送/交易判断。
V63_FILTER_EVENTS_FILE = "v63_filter_events.json"
V63_FILTER_REPORT_FILE = "v63_filter_effectiveness.json"
V63_FILTER_CHECK_SECONDS = 600
_v63_last_filter_check = 0.0
_v63_last_prelaunch = 0.0
_v63_last_behavior = 0.0
_v63_flow_hist_cache = {}


def _v63_stats_add(key, n=1):
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_json_file(V63_STATS_FILE, {})
    row = data.get(today, {}) if isinstance(data.get(today, {}), dict) else {}
    row[key] = int(row.get(key, 0)) + n
    data[today] = row
    # 只留最近约45天，避免文件无限增长
    for old in sorted(data.keys())[:-45]:
        data.pop(old, None)
    safe_write_json(V63_STATS_FILE, data)


def _v63_register_filtered_candidate(x, price, behavior, reason):
    """记录“本来接近买点、但被诱多/派发过滤器拦下”的事件。

    只做统计，不推微信、不改变候选状态。相同股票同一交易日同一原因只登记一次。
    """
    try:
        code = str(x.get("code", "")).zfill(6)
        if not code or price <= 0:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        reason = str(reason or "风险过滤")
        event_id = f"{today}|{code}|{reason}"
        data = _load_json_file(V63_FILTER_EVENTS_FILE, [])
        if not isinstance(data, list):
            data = []
        if any(str(e.get("event_id")) == event_id for e in data):
            return
        data.append({
            "event_id": event_id,
            "date": today,
            "time": datetime.now().strftime("%H:%M:%S"),
            "code": code,
            "name": x.get("name", ""),
            "source": x.get("source", ""),
            "reason": reason,
            "filter_price": round(float(price), 4),
            "buy_score": x.get("buy_score"),
            "rise_score": x.get("rise_score"),
            "trap_score": behavior.get("trap"),
            "distribution_score": behavior.get("distribution"),
            "max_price": round(float(price), 4),
            "min_price": round(float(price), 4),
            "last_price": round(float(price), 4),
            "max_gain_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "last_return_pct": 0.0,
            "trade_day_age": 0,
            "closed": False,
            "classification": "TRACKING",
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # 防止长期运行无限增大；保留最近1000次过滤事件。
        safe_write_json(V63_FILTER_EVENTS_FILE, data[-1000:])
        if STATS_V2_AVAILABLE:
            stats_record_candidate(
                {"code": code, "name": x.get("name", ""), "price": price},
                x.get("source", ""), "被过滤", filter_reason=reason,
                buy_score=x.get("buy_score"), rise_score=x.get("rise_score"),
            )
    except Exception as e:
        print("[FilterStats] 登记失败：", repr(e))


def _v63_filter_classification(event):
    """T+7后的统计分类。

    - MISSED_WINNER：过滤后7个交易日内最高仍涨>=5%，视作“可能误杀强票”。
    - EFFECTIVE_FILTER：未出现强势上涨，且之后出现>=3%回撤或T+7仍亏>=2%，视作“有效过滤”。
    - NEUTRAL：其余情况。
    分类用于校准阈值，不代表真实交易收益。
    """
    gain = float(event.get("max_gain_pct", 0) or 0)
    dd = float(event.get("max_drawdown_pct", 0) or 0)
    last = float(event.get("last_return_pct", 0) or 0)
    if gain >= 5.0:
        return "MISSED_WINNER"
    if dd <= -3.0 or last <= -2.0:
        return "EFFECTIVE_FILTER"
    return "NEUTRAL"


def _v63_write_filter_report(events):
    closed = [e for e in events if e.get("closed")]
    effective = [e for e in closed if e.get("classification") == "EFFECTIVE_FILTER"]
    missed = [e for e in closed if e.get("classification") == "MISSED_WINNER"]
    neutral = [e for e in closed if e.get("classification") == "NEUTRAL"]

    def avg(rows, key):
        vals = [float(x.get(key, 0) or 0) for x in rows]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    by_reason = {}
    for e in closed:
        r = str(e.get("reason", "其他"))
        row = by_reason.setdefault(r, {"count": 0, "effective": 0, "missed_winner": 0, "neutral": 0})
        row["count"] += 1
        c = e.get("classification")
        if c == "EFFECTIVE_FILTER": row["effective"] += 1
        elif c == "MISSED_WINNER": row["missed_winner"] += 1
        else: row["neutral"] += 1

    report = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tracking_events": sum(1 for e in events if not e.get("closed")),
        "evaluated_events": len(closed),
        "effective_filter_count": len(effective),
        "missed_winner_count": len(missed),
        "neutral_count": len(neutral),
        "effective_filter_rate_pct": round(len(effective) / len(closed) * 100, 1) if closed else 0.0,
        "missed_winner_rate_pct": round(len(missed) / len(closed) * 100, 1) if closed else 0.0,
        "avg_max_gain_after_filter_pct": avg(closed, "max_gain_pct"),
        "avg_max_drawdown_after_filter_pct": avg(closed, "max_drawdown_pct"),
        "avg_t7_return_pct": avg(closed, "last_return_pct"),
        "by_reason": by_reason,
        "definitions": {
            "MISSED_WINNER": "过滤后7个交易日内最高涨幅>=5%",
            "EFFECTIVE_FILTER": "未达到误杀强票条件，且后续最大回撤<=-3%或T+7收益<=-2%",
            "NEUTRAL": "其余情况",
        },
    }
    safe_write_json(V63_FILTER_REPORT_FILE, report)


def v63_filter_stats_if_due():
    """后台跟踪被过滤候选最多7个交易日；不发送企业微信。"""
    global _v63_last_filter_check
    now_ts = time.time()
    if now_ts - _v63_last_filter_check < V63_FILTER_CHECK_SECONDS:
        return
    _v63_last_filter_check = now_ts

    events = _load_json_file(V63_FILTER_EVENTS_FILE, [])
    if not isinstance(events, list) or not events:
        _v63_write_filter_report([])
        return

    today = datetime.now().strftime("%Y-%m-%d")
    changed = False
    for e in events:
        if e.get("closed"):
            continue
        try:
            age = _trade_day_age(str(e.get("date", today)), today)
            e["trade_day_age"] = age
            q = get_tencent_quote(str(e.get("code", "")))
            if q and q.get("price", 0) > 0:
                p = float(q["price"])
                base = float(e.get("filter_price") or p)
                e["max_price"] = round(max(float(e.get("max_price", p)), p), 4)
                e["min_price"] = round(min(float(e.get("min_price", p)), p), 4)
                e["last_price"] = round(p, 4)
                if base > 0:
                    e["max_gain_pct"] = round((float(e["max_price"]) / base - 1) * 100, 2)
                    e["max_drawdown_pct"] = round((float(e["min_price"]) / base - 1) * 100, 2)
                    e["last_return_pct"] = round((p / base - 1) * 100, 2)
                e["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
            if age >= V6_MAX_TRACK_DAYS:
                e["closed"] = True
                e["classification"] = _v63_filter_classification(e)
                e["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
        except Exception:
            continue

    if changed:
        safe_write_json(V63_FILTER_EVENTS_FILE, events[-1000:])
    _v63_write_filter_report(events)


def _v63_pct(a, b):
    try:
        return (float(a) / float(b) - 1) * 100 if float(b) else 0.0
    except Exception:
        return 0.0


def get_fund_flow_history(code, limit=20):
    """最近资金轨迹。公开资金口径仅作行为参考，不代表真实机构账户。"""
    now = time.time()
    cached = _v63_flow_hist_cache.get(code)
    if cached and now - cached[0] < 600:
        return cached[1]
    rows = []
    try:
        market = "sh" if str(code).startswith(("5","6","9")) else ("bj" if str(code).startswith(("4","8")) else "sz")
        df = ak.stock_individual_fund_flow(stock=str(code).zfill(6), market=market)
        if df is not None and not df.empty:
            net_col = next((c for c in ("主力净流入-净额","主力净流入净额","主力净流入") if c in df.columns), None)
            ratio_col = next((c for c in ("主力净流入-净占比","主力净流入净占比","主力净占比") if c in df.columns), None)
            date_col = next((c for c in ("日期","交易日期","date") if c in df.columns), None)
            if net_col:
                for _, r in df.tail(limit).iterrows():
                    rows.append({
                        "date": str(r.get(date_col,"")) if date_col else "",
                        "net": to_float(r.get(net_col), 0.0),
                        "ratio": to_float(r.get(ratio_col), 0.0) if ratio_col else None,
                    })
    except Exception:
        rows = []
    _v63_flow_hist_cache[code] = (now, rows)
    return rows


def v63_money_trajectory(code, ks=None):
    """5/10/20日资金+价格轨迹：只输出倾向分，不声称知道真实主力意图。"""
    ks = ks or get_daily_kline(code, 70)
    flows = get_fund_flow_history(code, 20)
    closes = [x.get("close",0) for x in ks if x.get("close",0)>0]
    vols = [x.get("volume",0) for x in ks if x.get("volume",0)>0]
    if len(closes) < 20:
        return {"score": 0, "label": "⚪ 资金轨迹不足", "net5":0, "net10":0, "net20":0}
    nets=[float(x.get("net",0) or 0) for x in flows]
    net5=sum(nets[-5:]) if nets else 0
    net10=sum(nets[-10:]) if nets else 0
    net20=sum(nets[-20:]) if nets else 0
    p5=_v63_pct(closes[-1],closes[-6]) if len(closes)>=6 else 0
    p10=_v63_pct(closes[-1],closes[-11]) if len(closes)>=11 else 0
    score=35
    if net5>0: score+=10
    if net10>0: score+=12
    if net20>0: score+=8
    # 资金改善而价格没有大涨，更偏“提前进入”特征
    if net10>0 and -3 <= p10 <= 8: score+=10
    # 低点/收盘逐步抬高
    if closes[-1] > sum(closes[-5:])/5 >= sum(closes[-10:])/10: score+=8
    # 回调缩量 / 上涨温和放量近似
    if len(vols)>=10:
        v5=sum(vols[-5:])/5; vprev=sum(vols[-10:-5])/5
        if p5<=1.5 and v5 <= vprev*1.15: score+=5
    score=max(0,min(100,round(score,1)))
    if score>=75: label="🟢 疑似提前埋伏"
    elif score>=62: label="🟡 资金改善"
    else: label="⚪ 轨迹一般"
    return {"score":score,"label":label,"net5":net5,"net10":net10,"net20":net20,"p5":round(p5,2),"p10":round(p10,2)}


def v63_prelaunch_score(code, price, pct, tech=None):
    ks=get_daily_kline(code,70)
    tech=tech or get_stock_tech_info(code)
    if len(ks)<30 or not tech:
        return None
    closes=[x["close"] for x in ks if x.get("close",0)>0]
    highs=[x["high"] for x in ks if x.get("high",0)>0]
    lows=[x["low"] for x in ks if x.get("low",0)>0]
    vols=[x["volume"] for x in ks if x.get("volume",0)>0]
    if len(closes)<30: return None
    traj=v63_money_trajectory(code,ks)
    score=32 + traj["score"]*0.35
    ma5,ma10,ma20,ma30,ma60=[tech.get(k) for k in ("ma5","ma10","ma20","ma30","ma60")]
    # 均线靠拢/中期不坏
    mas=[x for x in (ma5,ma10,ma20) if x]
    if len(mas)==3 and max(mas)/min(mas)-1 <= 0.035: score+=10
    if ma20 and ma30 and ma20>=ma30: score+=7
    if ma30 and ma60 and ma30>=ma60: score+=5
    # 最近波动收窄
    if len(highs)>=10 and len(lows)>=10:
        range5=(max(highs[-5:])-min(lows[-5:]))/max(closes[-1],0.01)*100
        range10=(max(highs[-10:])-min(lows[-10:]))/max(closes[-1],0.01)*100
        if range5 <= range10*0.7: score+=8
    # 不能已经明显拉高
    if pct>2.2: score-=10
    if pct<-2.0: score-=7
    if tech.get("high30") and price>0:
        room=(float(tech["high30"])/price-1)*100
        if room>=3: score+=6
        elif room<1: score-=6
    score=max(0,min(100,round(score,1)))
    return {"score":score,"traj":traj,"tech":tech}


V63_REALTIME_MIN_ROWS = 4500
V63_REALTIME_MARKET_MINIMUMS = {"sh": 1500, "sz": 2000, "bj": 100}
V63_REALTIME_CACHE_FILE = "v63_realtime_code_pool.json"
V63_TENCENT_BATCH_SIZE = 300
V63_TENCENT_MAX_CONSECUTIVE_FAILURES = 3
V63_PRIMARY_API_TIMEOUT = 3.0
V63_CODE_TABLE_TIMEOUT = 2.5
V63_REALTIME_HARD_DEADLINE = 20.0
V63_RECENT_DATAFRAME_TTL = 45.0

_v63_spot_log_state = {}
_v63_spot_api_workers = {}
_v63_spot_cache_lock = threading.Lock()
_v63_recent_spot_lock = threading.Lock()
_v63_recent_spot_cache = {"df": None, "saved_at": 0.0, "source": ""}


def _v63_spot_log(key, message, cooldown=300, logger=None):
    """同类降级日志限频；注入 logger 时交给测试调用方处理。"""
    if callable(logger):
        logger(message)
        return
    now_ts = time.time()
    previous = _v63_spot_log_state.get(key, 0.0)
    if now_ts - previous >= cooldown:
        _v63_spot_log_state[key] = now_ts
        print(message)


def _v63_timeboxed_api_call(api_name, api, timeout_seconds):
    """给第三方 AKShare 接口设置主程序等待上限，超时线程不重复堆积。"""
    running = _v63_spot_api_workers.get(api_name)
    if running and running.get("thread") and running["thread"].is_alive():
        return None, "前一次请求仍在后台结束中，本轮跳过"

    holder = {}

    def _runner():
        try:
            holder["value"] = api()
        except Exception as exc:
            holder["error"] = repr(exc)

    worker = threading.Thread(target=_runner, daemon=True, name=f"v63_{api_name}")
    _v63_spot_api_workers[api_name] = {"thread": worker, "holder": holder}
    worker.start()
    worker.join(max(0.1, float(timeout_seconds)))
    if worker.is_alive():
        return None, f"超过{timeout_seconds:.1f}秒等待上限"
    _v63_spot_api_workers.pop(api_name, None)
    if holder.get("error"):
        return None, holder["error"]
    return holder.get("value"), ""


def _v63_cache_file(cache_path=None):
    if cache_path is not None:
        return Path(cache_path)
    return Path(__file__).resolve().parent / V63_REALTIME_CACHE_FILE


def _v63_valid_a_share_code(code):
    code = normalize_sina_code(code)
    if not re.fullmatch(r"\d{6}", code):
        return False
    return code.startswith((
        "000", "001", "002", "003", "300", "301",
        "600", "601", "603", "605", "688", "689",
        "4", "8", "920",
    ))


def _v63_code_market(code):
    code = normalize_sina_code(code)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    if code.startswith(("4", "8", "920")):
        return "bj"
    return "unknown"


def _v63_market_coverage(codes):
    counts = {"sh": 0, "sz": 0, "bj": 0, "unknown": 0}
    for code in codes:
        market = _v63_code_market(code)
        counts[market] = counts.get(market, 0) + 1
    required_complete = all(
        counts[market] >= minimum
        for market, minimum in V63_REALTIME_MARKET_MINIMUMS.items()
    )
    return {
        "counts": counts,
        "has_sh": counts["sh"] > 0,
        "has_sz": counts["sz"] > 0,
        "has_bj": counts["bj"] > 0,
        "minimums": dict(V63_REALTIME_MARKET_MINIMUMS),
        "required_complete": required_complete,
    }


def _v63_extract_code_name_rows(df):
    """只抽取代码/名称用于行情代码缓存，不携带任何评分字段。"""
    if df is None or not hasattr(df, "iterrows"):
        return []
    columns = set(getattr(df, "columns", []))
    code_field = next((x for x in ("代码", "A股代码", "证券代码", "code") if x in columns), None)
    name_field = next((x for x in ("名称", "A股简称", "证券简称", "name") if x in columns), None)
    if not code_field:
        return []
    rows = []
    seen = set()
    for _, row in df.iterrows():
        code = normalize_sina_code(row.get(code_field, ""))
        if code in seen or not _v63_valid_a_share_code(code):
            continue
        seen.add(code)
        rows.append({"code": code, "name": str(row.get(name_field, "") if name_field else "").strip()})
    return rows


def _v63_load_code_cache(cache_path=None):
    path = _v63_cache_file(cache_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_rows = payload.get("stocks", []) if isinstance(payload, dict) else []
    except Exception:
        return [], {}
    rows = []
    seen = set()
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        code = normalize_sina_code(item.get("code", ""))
        if code in seen or not _v63_valid_a_share_code(code):
            continue
        seen.add(code)
        rows.append({"code": code, "name": str(item.get("name", "")).strip()})
    meta = {
        "updated_at": str(payload.get("updated_at", "")) if isinstance(payload, dict) else "",
        "source": str(payload.get("source", "")) if isinstance(payload, dict) else "",
    }
    return rows, meta


def _v63_atomic_write_code_cache(rows, source, cache_path=None):
    """同目录临时文件 + os.replace 原子更新；缓存不含行情分数或阈值。"""
    cleaned = []
    seen = set()
    for item in rows:
        code = normalize_sina_code(item.get("code", "")) if isinstance(item, dict) else ""
        if code in seen or not _v63_valid_a_share_code(code):
            continue
        seen.add(code)
        cleaned.append({"code": code, "name": str(item.get("name", "")).strip()})
    if len(cleaned) < V63_REALTIME_MIN_ROWS:
        return False
    if not _v63_market_coverage(item["code"] for item in cleaned)["required_complete"]:
        return False
    path = _v63_cache_file(cache_path)
    temporary_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(source),
            "stock_count": len(cleaned),
            "fields": ["code", "name"],
            "stocks": cleaned,
        }
        with _v63_spot_cache_lock:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp", prefix=path.name + ".",
                dir=str(path.parent), delete=False,
            ) as temporary:
                json.dump(payload, temporary, ensure_ascii=False, separators=(",", ":"))
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, path)
        return True
    except Exception:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except Exception:
                pass
        return False


def _v63_add_code_rows(pool, rows, exchange=None):
    for item in rows:
        code = normalize_sina_code(item.get("code", ""))
        if not _v63_valid_a_share_code(code):
            continue
        if exchange == "sz" and not code.startswith(("000", "001", "002", "003", "300", "301")):
            continue
        if exchange == "bj" and not code.startswith(("4", "8", "920")):
            continue
        old = pool.get(code)
        name = str(item.get("name", "")).strip()
        if old is None or (not old.get("name") and name):
            pool[code] = {"code": code, "name": name}


def _v63_build_tencent_code_pool(ak_module=None, cache_path=None):
    """缓存优先；缓存不足时补深/北真实代码表，再枚举允许的沪市前缀。"""
    ak_source = ak_module if ak_module is not None else ak
    pool = {}
    cache_rows, cache_meta = _v63_load_code_cache(cache_path)
    _v63_add_code_rows(pool, cache_rows)
    diagnostics = {
        "cache_count": len(cache_rows),
        "cache_updated_at": cache_meta.get("updated_at", ""),
        "exchange_errors": [],
    }

    # 一份完整缓存已经过腾讯实时报价重新验证，可直接作为优先代码池，避免
    # 每三分钟重复访问交易所代码表。
    cache_coverage = _v63_market_coverage(item["code"] for item in cache_rows)
    if len(cache_rows) >= V63_REALTIME_MIN_ROWS and cache_coverage["required_complete"]:
        diagnostics["pool_source"] = "complete_atomic_cache"
        diagnostics["pool_market_coverage"] = cache_coverage
        return list(pool.values()), diagnostics

    table_specs = (
        ("stock_info_sz_name_code", "sz", lambda fn: fn(symbol="A股列表")),
        ("stock_info_bj_name_code", "bj", lambda fn: fn()),
    )
    for api_name, exchange, caller in table_specs:
        api = getattr(ak_source, api_name, None)
        if not callable(api):
            diagnostics["exchange_errors"].append(f"{api_name}:接口不存在")
            continue
        df, error = _v63_timeboxed_api_call(
            "code_" + api_name,
            lambda api=api, caller=caller: caller(api),
            V63_CODE_TABLE_TIMEOUT,
        )
        if error:
            diagnostics["exchange_errors"].append(f"{api_name}:{error}")
            continue
        rows = _v63_extract_code_name_rows(df)
        _v63_add_code_rows(pool, rows, exchange=exchange)
        diagnostics[api_name + "_count"] = len(rows)

    pool_coverage = _v63_market_coverage(pool.keys())
    # 冷启动时交易所代码表可能超时。此时只枚举明确的A股号码前缀，
    # 最终仍必须由腾讯返回“当日、有效、量价金额一致”的真实记录才会接纳。
    if pool_coverage["counts"]["sz"] < 2000:
        for prefix in ("000", "001", "002", "003", "300", "301"):
            for suffix in range(1000):
                code = f"{prefix}{suffix:03d}"
                pool.setdefault(code, {"code": code, "name": ""})
        diagnostics["sz_prefix_enumeration"] = True
    if pool_coverage["counts"]["bj"] < 50:
        for suffix in range(1000):
            code = f"920{suffix:03d}"
            pool.setdefault(code, {"code": code, "name": ""})
        diagnostics["bj_920_prefix_enumeration"] = True

    # 沪市只枚举指定股票前缀；不存在的号码不会进入DataFrame，因为腾讯
    # 返回后还要逐条校验代码、价格、成交额和当日时间。
    for prefix in ("600", "601", "603", "605", "688", "689"):
        for suffix in range(1000):
            code = f"{prefix}{suffix:03d}"
            pool.setdefault(code, {"code": code, "name": ""})
    diagnostics["pool_source"] = "cache_exchange_tables_and_sh_prefix_enumeration"
    diagnostics["pool_count"] = len(pool)
    diagnostics["pool_market_coverage"] = _v63_market_coverage(pool.keys())
    return list(pool.values()), diagnostics


def _v63_tencent_symbol(code):
    code = normalize_sina_code(code)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code
    if code.startswith(("4", "8", "920")):
        return "bj" + code
    return "sz" + code


def _v63_decode_tencent_response(response):
    content = getattr(response, "content", None)
    if isinstance(content, bytes) and content:
        return content.decode("gb18030", errors="replace")
    return str(getattr(response, "text", "") or "")


def _v63_parse_tencent_spot_text(text, expected_date, expected_symbols=None):
    """解析腾讯A股 ``~`` 协议，并严格拒绝非当日本地日期的行情。"""
    if isinstance(expected_date, datetime):
        expected_day = expected_date.strftime("%Y%m%d")
    else:
        expected_day = str(expected_date).replace("-", "")[:8]
    allowed = set(expected_symbols or [])
    protocol_rows = 0
    accepted = []
    rejected_stale = 0
    rejected_invalid = 0
    rejected_amount_inconsistent = 0
    seen = set()
    raw_text = text.decode("gb18030", errors="replace") if isinstance(text, bytes) else str(text or "")
    for source_symbol, payload in re.findall(r'v_([^=\s]+)="([^"]*)"', raw_text):
        if allowed and source_symbol not in allowed:
            continue
        protocol_rows += 1
        values = payload.split("~")
        if len(values) <= 37:
            rejected_invalid += 1
            continue
        code = normalize_sina_code(values[2] or source_symbol)
        expected_symbol = _v63_tencent_symbol(code) if _v63_valid_a_share_code(code) else ""
        if not expected_symbol or expected_symbol != source_symbol or code in seen:
            rejected_invalid += 1
            continue
        quote_time = str(values[30]).strip()
        if not re.fullmatch(r"\d{14}", quote_time) or quote_time[:8] != expected_day:
            rejected_stale += 1
            continue
        name = str(values[1]).strip()
        price = to_float(values[3], None)
        previous_close = to_float(values[4], None)
        volume = to_float(values[6], None)
        amount_wan = to_float(values[37], None)
        pct_field = to_float(values[32], None)
        if (
            not name or price is None or price <= 0 or previous_close is None or previous_close <= 0
            or amount_wan is None or amount_wan < 0 or volume is None or volume < 0
        ):
            rejected_invalid += 1
            continue
        amount_yuan = amount_wan * 10000.0
        estimated_amount = volume * 100.0 * price
        if estimated_amount <= 0 or amount_yuan <= 0:
            rejected_amount_inconsistent += 1
            continue
        amount_check_ratio = amount_yuan / estimated_amount
        if not (0.5 <= amount_check_ratio <= 1.5):
            rejected_amount_inconsistent += 1
            continue
        pct = pct_field if pct_field is not None else (price / previous_close - 1.0) * 100.0
        seen.add(code)
        accepted.append({
            "代码": code,
            "名称": name,
            "最新价": price,
            "涨跌幅": pct,
            "成交额": amount_yuan,  # 腾讯字段37单位为万元，统一转为元。
            "成交量": volume * 100.0,  # 腾讯字段6为手，标准化为股。
            "昨收": previous_close,
            "今开": to_float(values[5]),
            "最高": to_float(values[33]),
            "最低": to_float(values[34]),
            "报价时间": quote_time,
            "数据源": "Tencent qt.gtimg.cn",
            "成交额量价校验比": amount_check_ratio,
        })
    return accepted, {
        "protocol_rows": protocol_rows,
        "accepted_rows": len(accepted),
        "rejected_stale": rejected_stale,
        "rejected_invalid": rejected_invalid,
        "rejected_amount_inconsistent": rejected_amount_inconsistent,
    }


def _v63_fetch_tencent_spot_dataframe(
    code_rows,
    requests_module=None,
    now=None,
    min_rows=V63_REALTIME_MIN_ROWS,
    batch_size=V63_TENCENT_BATCH_SIZE,
    max_consecutive_failures=V63_TENCENT_MAX_CONSECUTIVE_FAILURES,
    deadline_seconds=V63_REALTIME_HARD_DEADLINE,
):
    """批量请求腾讯；连续网络失败时在固定次数内快速终止。"""
    request_source = requests_module if requests_module is not None else requests
    local_now = now if isinstance(now, datetime) else datetime.now()
    expected_day = local_now.strftime("%Y%m%d")
    symbols = []
    seen_symbols = set()
    for item in code_rows:
        code = normalize_sina_code(item.get("code", "") if isinstance(item, dict) else item)
        if not _v63_valid_a_share_code(code):
            continue
        symbol = _v63_tencent_symbol(code)
        if symbol not in seen_symbols:
            seen_symbols.add(symbol)
            symbols.append(symbol)
    if len(symbols) < int(min_rows):
        return None, {
            "complete": False, "reason": "code_pool_below_minimum",
            "code_pool_count": len(symbols), "accepted_rows": 0,
        }

    accepted_by_code = {}
    consecutive_failures = 0
    network_failures = 0
    protocol_rows = 0
    rejected_stale = 0
    rejected_invalid = 0
    rejected_amount_inconsistent = 0
    batches_requested = 0
    stopped_early = False
    stopped_reason = ""
    request_started = time.monotonic()
    deadline_seconds = max(0.5, float(deadline_seconds))
    batch_size = max(50, min(int(batch_size), 300))
    for start in range(0, len(symbols), batch_size):
        elapsed = time.monotonic() - request_started
        remaining = deadline_seconds - elapsed
        if remaining <= 0.25:
            stopped_early = True
            stopped_reason = "hard_deadline"
            break
        batch = symbols[start:start + batch_size]
        batches_requested += 1
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            response = request_source.get(
                url,
                timeout=max(0.25, min(3.5, remaining)),
                headers={"User-Agent": "Mozilla/5.0"},
            )
            raise_for_status = getattr(response, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()
            text = _v63_decode_tencent_response(response)
            rows, meta = _v63_parse_tencent_spot_text(text, expected_day, expected_symbols=batch)
            protocol_rows += meta["protocol_rows"]
            rejected_stale += meta["rejected_stale"]
            rejected_invalid += meta["rejected_invalid"]
            rejected_amount_inconsistent += meta["rejected_amount_inconsistent"]
            if meta["protocol_rows"] <= 0:
                network_failures += 1
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            for row in rows:
                accepted_by_code[row["代码"]] = row
        except Exception:
            network_failures += 1
            consecutive_failures += 1
        if consecutive_failures >= max(1, int(max_consecutive_failures)):
            stopped_early = True
            stopped_reason = "consecutive_network_failures"
            break

    rows = list(accepted_by_code.values())
    market_coverage = _v63_market_coverage(row["代码"] for row in rows)
    rows_complete = len(rows) >= int(min_rows)
    complete = rows_complete and market_coverage["required_complete"]
    if not rows_complete:
        reason = "accepted_rows_below_minimum"
    elif not market_coverage["required_complete"]:
        reason = "missing_sh_or_sz_market_coverage"
    else:
        reason = "ok"
    status = {
        "complete": complete,
        "reason": reason,
        "code_pool_count": len(symbols),
        "accepted_rows": len(rows),
        "minimum_rows": int(min_rows),
        "protocol_rows": protocol_rows,
        "rejected_stale": rejected_stale,
        "rejected_invalid": rejected_invalid,
        "rejected_amount_inconsistent": rejected_amount_inconsistent,
        "network_failures": network_failures,
        "batches_requested": batches_requested,
        "stopped_early": stopped_early,
        "stopped_reason": stopped_reason,
        "request_elapsed_seconds": round(time.monotonic() - request_started, 3),
        "quote_date": expected_day,
        "amount_conversion": "field37_wan_times_10000_to_yuan",
        "volume_conversion": "field6_lots_times_100_to_shares",
        "market_coverage": market_coverage,
    }
    if not status["complete"]:
        return None, status
    try:
        import pandas as pd
        return pd.DataFrame(rows), status
    except Exception as exc:
        status["complete"] = False
        status["reason"] = f"pandas_dataframe_failed:{exc!r}"
        return None, status


def _v63_primary_dataframe_valid(df, minimum_rows):
    required = {"代码", "名称", "涨跌幅", "成交额", "最新价"}
    if df is None or not hasattr(df, "columns") or not hasattr(df, "__len__"):
        return False, "返回对象不是DataFrame"
    missing = required.difference(set(df.columns))
    if missing:
        return False, "缺少字段:" + ",".join(sorted(missing))
    count = len(df)
    if count < int(minimum_rows):
        return False, f"仅{count}条，低于完整全A要求{minimum_rows}条"
    coverage = _v63_market_coverage(
        normalize_sina_code(row.get("代码", "")) for _, row in df.iterrows()
    )
    if not coverage["required_complete"]:
        return False, f"市场覆盖不完整:{coverage['counts']}"
    return True, ""


def _v63_realtime_spot_dataframe(
    ak_module=None,
    requests_module=None,
    now=None,
    code_pool=None,
    min_rows=V63_REALTIME_MIN_ROWS,
    cache_path=None,
    logger=None,
):
    """统一全A快照：东财 -> 新浪 -> 腾讯批量，任一结果至少4500条。"""
    minimum_rows = max(V63_REALTIME_MIN_ROWS, int(min_rows))
    production_call = (
        ak_module is None and requests_module is None and now is None and code_pool is None
        and cache_path is None and logger is None
    )
    if production_call:
        with _v63_recent_spot_lock:
            recent_df = _v63_recent_spot_cache.get("df")
            recent_age = time.monotonic() - float(_v63_recent_spot_cache.get("saved_at", 0.0))
            if (
                recent_df is not None and recent_age <= V63_RECENT_DATAFRAME_TTL
                and hasattr(recent_df, "__len__") and len(recent_df) >= minimum_rows
                and _v63_market_coverage(
                    normalize_sina_code(row.get("代码", "")) for _, row in recent_df.iterrows()
                )["required_complete"]
            ):
                return recent_df

    helper_started = time.monotonic()
    ak_source = ak_module if ak_module is not None else ak
    errors = []
    for api_name in ("stock_zh_a_spot_em", "stock_zh_a_spot"):
        api = getattr(ak_source, api_name, None)
        if not callable(api):
            errors.append(f"{api_name}:接口不存在")
            continue
        df, error = _v63_timeboxed_api_call(api_name, api, V63_PRIMARY_API_TIMEOUT)
        if error:
            errors.append(f"{api_name}:{error}")
            continue
        valid, reason = _v63_primary_dataframe_valid(df, minimum_rows)
        if valid:
            cache_rows = _v63_extract_code_name_rows(df)
            if len(cache_rows) >= V63_REALTIME_MIN_ROWS:
                _v63_atomic_write_code_cache(cache_rows, api_name, cache_path)
            if production_call:
                with _v63_recent_spot_lock:
                    _v63_recent_spot_cache.update({"df": df, "saved_at": time.monotonic(), "source": api_name})
            return df
        errors.append(f"{api_name}:{reason}")

    short_errors = " | ".join(errors[:2])
    _v63_spot_log(
        "primary_fallback",
        f"[全A行情] 东财/新浪不可用，切换腾讯批量备用源：{short_errors}",
        cooldown=600,
        logger=logger,
    )
    if code_pool is None:
        pool_rows, pool_meta = _v63_build_tencent_code_pool(ak_source, cache_path)
    else:
        pool_rows = [
            item if isinstance(item, dict) else {"code": item, "name": ""}
            for item in code_pool
        ]
        pool_meta = {"pool_source": "injected_code_pool", "pool_count": len(pool_rows), "exchange_errors": []}
    if len(pool_rows) < minimum_rows:
        _v63_spot_log(
            "pool_incomplete",
            f"[全A行情] 腾讯代码池仅{len(pool_rows)}条，低于{minimum_rows}条，拒绝作为全A快照。",
            cooldown=300,
            logger=logger,
        )
        return None

    remaining_deadline = V63_REALTIME_HARD_DEADLINE - (time.monotonic() - helper_started)
    if remaining_deadline <= 0.5:
        _v63_spot_log(
            "helper_deadline",
            "[全A行情] 主源与代码表已用完本轮约20秒总时限，腾讯本轮不再发起，等待下次扫描。",
            cooldown=300,
            logger=logger,
        )
        return None

    df, status = _v63_fetch_tencent_spot_dataframe(
        pool_rows,
        requests_module=requests_module,
        now=now,
        min_rows=minimum_rows,
        deadline_seconds=remaining_deadline,
    )
    if df is None:
        _v63_spot_log(
            "tencent_incomplete",
            "[全A行情] 腾讯备用源不完整，已拒绝本轮："
            f"有效{status.get('accepted_rows', 0)}/{status.get('minimum_rows', minimum_rows)}，"
            f"过期{status.get('rejected_stale', 0)}，金额校验拒绝{status.get('rejected_amount_inconsistent', 0)}，"
            f"网络失败{status.get('network_failures', 0)}，"
            f"市场覆盖{status.get('market_coverage', {}).get('counts', {})}，"
            f"提前终止{'是' if status.get('stopped_early') else '否'}({status.get('reason', '')})。",
            cooldown=300,
            logger=logger,
        )
        return None

    cache_rows = _v63_extract_code_name_rows(df)
    if len(cache_rows) >= V63_REALTIME_MIN_ROWS:
        _v63_atomic_write_code_cache(cache_rows, "Tencent qt.gtimg.cn", cache_path)
    if production_call:
        with _v63_recent_spot_lock:
            _v63_recent_spot_cache.update({
                "df": df, "saved_at": time.monotonic(), "source": "Tencent qt.gtimg.cn",
            })
    _v63_spot_log(
        "tencent_recovered",
        f"[全A行情] 腾讯备用源已接管：{len(df)}条当日有效行情，"
        f"市场覆盖{status.get('market_coverage', {}).get('counts', {})}；成交额万元转元、成交量手转股。",
        cooldown=1800,
        logger=logger,
    )
    return df


def v63_prelaunch_scan_if_due():
    """每10分钟低频扫描潜伏池。只观察，不直接买；避免全A高频日线请求。"""
    global _v63_last_prelaunch
    now=time.time()
    if now-_v63_last_prelaunch < V63_PRELAUNCH_SECONDS: return
    _v63_last_prelaunch=now
    try:
        df=_v63_realtime_spot_dataframe()
        if df is None:
            return
        pool=[]
        for _,r in df.iterrows():
            code=normalize_sina_code(r.get("代码","")); name=str(r.get("名称","")).strip()
            pct=to_float(r.get("涨跌幅")); amount=to_float(r.get("成交额")); price=to_float(r.get("最新价"))
            if not code or price<=0 or "ST" in name.upper() or "退" in name: continue
            if -1.5 <= pct <= 2.0 and amount>=80000000:
                pool.append((amount,code,name,price,pct))
        pool.sort(reverse=True)
        candidates=[]
        for _,code,name,price,pct in pool[:24]:
            s=v63_prelaunch_score(code,price,pct)
            if s and s["score"]>=72:
                candidates.append((s["score"],code,name,price,pct,s))
        candidates.sort(reverse=True)
        picks=candidates[:3]
        if not picks: return
        state=_load_json_file(V63_PRELAUNCH_FILE,{})
        if not isinstance(state, dict):
            state={}
        today=datetime.now().strftime("%Y-%m-%d")
        sig=tuple(x[1] for x in picks)
        last=state.get("last_signature")
        if not isinstance(last, (list, tuple)):
            last=[]
        if tuple(last) == sig:
            return
        state["last_signature"]=list(sig); state["date"]=today
        state["picks"]=[{"code":x[1],"name":x[2],"score":x[0],"price":x[3]} for x in picks]
        safe_write_json(V63_PRELAUNCH_FILE,state)
        lines=["🔎 A股机会雷达 V6.3 Final｜潜伏观察",""]
        for i,(score,code,name,price,pct,s) in enumerate(picks,1):
            tech=s["tech"]; traj=s["traj"]
            lines += [f"{i}. {name} {code}｜💰{price:.2f} {pct:+.2f}%",f"📊 埋伏{score:.0f}｜资金轨迹{traj['score']:.0f}｜MA20/30 {'向上' if tech.get('ma20') and tech.get('ma30') and tech['ma20']>=tech['ma30'] else '待确认'}",f"👉 🟡 只观察，暂不买｜站稳MA5附近后再看启动",""]
        lines += ["📌 潜伏池只做提前观察，不直接触发买入。"]
        push_message("\n".join(lines))
        _v63_stats_add("prelaunch",len(picks))
    except Exception as e:
        print("[V6.3潜伏] 扫描失败：",repr(e))


def v63_behavior_diagnosis(code, price, pct, intraday=None):
    tech=get_stock_tech_info(code); ks=get_daily_kline(code,70); flow=get_individual_main_flow(code)
    if not tech or len(ks)<20:
        return {"state":"UNKNOWN","wash":0,"down":0,"trap":0,"distribution":0,"tech":tech,"flow":flow,"traj":{}}
    traj=v63_money_trajectory(code,ks)
    vr=tech.get("volume_ratio") or 1.0; net=flow.get("net")
    ma20,ma30=tech.get("ma20"),tech.get("ma30")
    wash=30; down=20; trap=10; dist=10
    if ma20 and price>=ma20: wash+=12
    else: down+=12
    if ma30 and price>=ma30: wash+=8
    else: down+=10
    if ma20 and ma30 and ma20>=ma30: wash+=7
    if pct<0 and vr<=1.0: wash+=12
    if pct<=-2 and vr>=1.5: down+=14
    if net is not None:
        if net>=0: wash+=8
        elif net<=-50000000: down+=12; dist+=12
    if traj.get("score",0)>=72: wash+=8
    # 最近日K下跌放量/反弹弱近似
    closes=[x["close"] for x in ks]; vols=[x["volume"] for x in ks]
    if len(closes)>=4 and len(vols)>=4:
        if closes[-1]<closes[-2] and vols[-1]>sum(vols[-4:-1])/3*1.25: down+=10; dist+=6
        if closes[-1]>closes[-2] and vols[-1]>sum(vols[-4:-1])/3*1.10: wash+=5
    # 盘中：冲高回落/突破失败/多次修复
    intraday=intraday or {}
    hi=float(intraday.get("high",price)); lo=float(intraday.get("low",price)); prev=float(intraday.get("prev",price))
    r1=intraday.get("r1"); s1=intraday.get("s1"); s2=intraday.get("s2")
    if hi>0 and price<hi*0.985 and pct>0: trap+=18; dist+=8
    if r1 and hi>=float(r1)*1.002 and price<float(r1): trap+=24
    if vr>=2.5 and abs(pct)<1.5: trap+=12; dist+=12
    if s1 and lo<=float(s1)*1.003 and price>=float(s1)*1.010 and price>prev: wash+=18
    if s2 and price<float(s2): down+=24; wash-=15
    wash=max(0,min(100,round(wash,1))); down=max(0,min(100,round(down,1)))
    trap=max(0,min(100,round(trap,1))); dist=max(0,min(100,round(dist,1)))
    if trap>=70 or dist>=78: state="TRAP" if trap>=dist else "DISTRIBUTION"
    elif down>=75 and down>=wash+12: state="DOWN"
    elif wash>=78 and down<=48: state="STRONG_WASH"
    elif wash>=65 and down<=55: state="WASH"
    elif abs(wash-down)<=12 or max(wash,down)<65: state="UNCERTAIN"
    elif down>wash: state="DOWN_RISK"
    else: state="WASH"
    return {"state":state,"wash":wash,"down":down,"trap":trap,"distribution":dist,"tech":tech,"flow":flow,"traj":traj}


def _v63_tracking_context(code, history, position=None):
    """Return the latest confirmed signal context used by WeCom holding alerts."""
    position = position or {}
    candidates = [x for x in history if str(x.get("code", "")).zfill(6) == str(code).zfill(6) and not x.get("closed")]
    signal = candidates[-1] if candidates else {}
    entry = position.get("cost") or position.get("entry") or signal.get("entry")
    signal_date = signal.get("date")
    day = _trade_day_age(signal_date) if signal_date else None
    return {
        "tracked": bool(position or signal),
        "entry": float(entry) if entry not in (None, "") else None,
        "day": f"D{day}" if day is not None else "D?",
        "signal_date": signal_date or "-",
    }


def _v63_behavior_message(name,code,price,pct,d,levels,tracking=None):
    tracking = tracking or {}
    tracked = bool(tracking.get("tracked"))
    entry = tracking.get("entry")
    pnl = ((price / entry - 1) * 100) if entry and entry > 0 else None
    identity = "【系统状态：已建仓跟踪】" if tracked else "【系统状态：未建仓观察】"
    holding = (f"【模拟成本：{entry:.2f}｜当前收益：{pnl:+.2f}%｜持有：{tracking.get('day','D?')}】"
               if tracked and entry else "尚未关联深度确认信号，不作为持仓操作依据。")
    prefix = [identity, holding]
    state=d["state"]; s1,s2,r1,r2=[levels.get(k) for k in ("support1","support2","resist1","resist2")]
    if state=="STRONG_WASH":
        return [f"🟢 强势洗盘｜{name} {code}",*prefix,f"💰 {price:.2f}｜{pct:+.2f}%｜洗盘{d['wash']:.0f} 真跌{d['down']:.0f}",f"✅ 支撑{s1:.2f}附近有修复｜资金轨迹{d['traj'].get('score',0):.0f}" if s1 else "✅ 趋势结构仍在",f"👉 操作：{'继续持有，暂不加仓' if tracked else '继续观察，不新建仓'}",f"站稳{r1:.2f} → 洗盘结束倾向增强" if r1 else "",f"跌破{s2:.2f} → 🔴 洗盘判断失效" if s2 else "","📌 更像强趋势中的快速清洗，暂不像真跌。"]
    if state=="WASH":
        return [f"🟢 偏洗盘｜{name} {code}",*prefix,f"💰 {price:.2f}｜{pct:+.2f}%｜洗盘{d['wash']:.0f} 真跌{d['down']:.0f}",f"👉 操作：{'继续持有，不加仓' if tracked else '仅观察，不买入'}",f"站回{r1:.2f} → 转强确认" if r1 else "",f"跌破{s2:.2f} → 洗盘判断取消" if s2 else "","📌 当前更偏正常回调。"]
    if state in ("UNCERTAIN","DOWN_RISK"):
        return [f"🟡 下跌待确认｜{name} {code}",*prefix,f"💰 {price:.2f}｜{pct:+.2f}%｜洗盘{d['wash']:.0f} 真跌{d['down']:.0f}",f"👉 操作：{'谨慎持有，禁止加仓' if tracked else '等待确认，暂不买入'}",f"站回{r1:.2f} → 偏洗盘/修复" if r1 else "",f"跌破{s2:.2f} → 偏真跌，准备退出" if s2 else "","📌 方向未确认，等价格给答案。"]
    if state=="TRAP":
        return [f"⚠️ 诱多风险｜{name} {code}",*prefix,f"💰 {price:.2f}｜诱多风险{d['trap']:.0f}","🔴 冲高回落/放量效率偏弱｜资金同步需复核",f"👉 操作：{'保护利润，检查减仓' if tracked else '不买入、不追高'}",f"重新站稳{r1:.2f} + 资金转强 → 再评估" if r1 else "","📌 疑似假突破，先回避。"]
    if state=="DISTRIBUTION":
        return [f"🔴 派发风险｜{name} {code}",*prefix,f"💰 {price:.2f}｜派发风险{d['distribution']:.0f}","🔴 冲高回落/资金走弱/量价效率下降",f"👉 操作：{'减仓并保护利润' if tracked else '不买入'}",f"跌破{s1:.2f} → 执行退出检查" if s1 else "","📌 疑似边拉边兑现。"]
    if state=="DOWN":
        return [f"🔴 趋势破坏｜{name} {code}",*prefix,f"💰 {price:.2f}｜{pct:+.2f}%｜真跌{d['down']:.0f} 洗盘{d['wash']:.0f}",f"❌ 强支撑{s2:.2f}失守｜MA20/30结构转弱" if s2 else "❌ 关键趋势结构转弱",f"👉 操作：{'减仓/退出，不补仓' if tracked else '不买入、不抄底'}","📌 更像真实下跌，不按洗盘处理。"]
    return []


def v63_behavior_watch_if_due():
    """只在状态实质变化时提醒；对持仓+最近候选做行为诊断。"""
    global _v63_last_behavior
    now=time.time()
    if now-_v63_last_behavior < V63_BEHAVIOR_SECONDS: return
    _v63_last_behavior=now
    history=_load_json_file(V6_HISTORY_FILE,[])
    state=_load_json_file(V63_BEHAVIOR_FILE,{})
    watch={}
    for code,pos in POSITIONS.items(): watch[code]=pos.get("name",code)
    for x in history[-60:]:
        code=str(x.get("code",""));
        if code and not x.get("closed"): watch.setdefault(code,x.get("name",code))
    changed=False
    for code,name in list(watch.items())[:25]:
        try:
            q=get_tencent_quote(code)
            if not q or q.get("price",0)<=0: continue
            price=float(q["price"]); pct=float(q.get("pct",0)); key=code
            st=state.get(key,{}) if isinstance(state.get(key,{}),dict) else {}
            tech=get_stock_tech_info(code); levels=v61_key_levels(price,tech,{})
            st["high"]=max(float(st.get("high",price)),price); st["low"]=min(float(st.get("low",price)),price)
            intra={"high":st["high"],"low":st["low"],"prev":float(st.get("prev",price)),"r1":levels.get("resist1"),"s1":levels.get("support1"),"s2":levels.get("support2")}
            d=v63_behavior_diagnosis(code,price,pct,intra)
            old=st.get("behavior"); new=d["state"]
            # 只推有意义状态，且状态变化后推；普通洗盘/待确认只对持仓推，减少噪音
            important=new in ("STRONG_WASH","TRAP","DISTRIBUTION","DOWN") or code in POSITIONS
            if new!=old and important:
                tracking=_v63_tracking_context(code, history, POSITIONS.get(code))
                msg=_v63_behavior_message(name,code,price,pct,d,levels,tracking)
                if msg:
                    push_message("\n".join([x for x in msg if x]))
                    changed=True
                    statmap={"STRONG_WASH":"strong_wash","WASH":"wash","UNCERTAIN":"uncertain","DOWN_RISK":"down_risk","TRAP":"trap","DISTRIBUTION":"distribution","DOWN":"down"}
                    _v63_stats_add(statmap.get(new,"behavior"),1)
            st.update({"behavior":new,"prev":price,"wash":d["wash"],"down":d["down"],"trap":d["trap"],"distribution":d["distribution"],"updated":datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            state[key]=st
        except Exception:
            continue
    if changed or state: safe_write_json(V63_BEHAVIOR_FILE,state)


last_daily_review_push = ""
DAILY_REVIEW_STATE_FILE = "v63_daily_review_push_state.json"
_daily_review_lock = threading.Lock()


def try_daily_review_push():
    """收盘后每日只推送一次复盘。"""
    global last_daily_review_push
    now = datetime.now()
    key = now.strftime("%Y-%m-%d")
    if not (now.hour == 15 and now.minute >= 5) or last_daily_review_push == key:
        return
    with _daily_review_lock:
        state = _load_json_file(DAILY_REVIEW_STATE_FILE, {})
        if last_daily_review_push == key or state.get(key):
            last_daily_review_push = key
            return
        if push_message(daily_review_message()):
            last_daily_review_push = key
            state[key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            safe_write_json(DAILY_REVIEW_STATE_FILE, state)


def v66_initialize_closed_loop_runtime():
    """初始化独立闭环库；只建表，不写候选、行情或胜率样本。"""
    global _v66_runtime_info
    if not V66_CLOSED_LOOP_AVAILABLE:
        return False
    try:
        _v66_runtime_info = v66_closed_loop_initialize(Path(__file__).resolve().parent)
        status = _v66_runtime_info.get("database_status", {})
        counts = status.get("row_counts", {})
        print(
            "V6.6次日闭环：数据库完整性"
            f" {status.get('integrity_check', '未知')}｜候选{counts.get('candidate_pool', 0)}条"
            f"｜真实次日行情{counts.get('next_day_tracking', 0)}条。"
        )
        return True
    except Exception as exc:
        _v66_runtime_info = {}
        print("[V6.6闭环] 初始化失败，V6.5盘中功能继续运行：", repr(exc))
        return False


def _v66_stage_retry_due(stage, business_date, interval_seconds):
    key = f"{business_date}|{stage}"
    now_ts = time.time()
    if now_ts - float(_v66_last_stage_attempt.get(key, 0)) < interval_seconds:
        return False
    _v66_last_stage_attempt[key] = now_ts
    return True


def _v66_push_stage_once(stage, business_date, message):
    """数据库提交与微信推送分别记账；失败后允许下轮重试。"""
    state = v66_get_job_state(stage, business_date)
    if state.get("push_sent"):
        return True
    ok = bool(push_message(message))
    v66_mark_stage_push(
        stage,
        business_date,
        ok,
        {"channel": "企业微信3群", "message_type": "V6.6次日闭环"},
    )
    return ok


def _v66_push_degraded_once(stage, business_date, stage_label):
    """数据不完整时群3只提醒一次；不占用正常结果的push_sent状态。"""
    path = str(Path(__file__).resolve().parent / V66_DEGRADED_NOTICE_FILE)
    state = _load_json_file(path, {})
    key = f"{business_date}|{stage}"
    if state.get(key):
        return True
    message = (
        f"⚠️ A股机会雷达 V6.6 Pro｜{stage_label}数据暂不完整\n"
        "本次不调整评分和排序，继续沿用上一份有效名单。\n"
        "程序会在后台自动重试；不会把接口缺失当成没有利空。\n"
        "推送通道：企业微信3群。"
    )
    if push_message(message):
        state[key] = datetime.now().isoformat(timespec="seconds")
        safe_write_json(path, state)
        return True
    return False


def _v66_raw_daily_callback(code, candidate_date=None, as_of_date=None):
    """只把腾讯接口实际返回的不复权日线交给跟踪层。"""
    return {
        "rows": get_daily_kline_raw(code, 160),
        "data_source": "腾讯证券不复权日线实际行情",
        "quote_timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def _v66_evidence_with_source(payload, candidate_rows, stage):
    """把采集结果适配为可审计逐股证据；缺一项就保持 incomplete。"""
    result = dict(payload or {})
    evidence = {
        str(code).zfill(6): dict(item)
        for code, item in (result.get("evidence_by_code") or {}).items()
        if isinstance(item, dict)
    }
    source_value = result.get("data_source") or []
    if isinstance(source_value, (list, tuple)):
        source_text = "、".join(str(x) for x in source_value if str(x).strip())
    else:
        source_text = str(source_value or "").strip()
    source_text = source_text or "主程序实际数据接口"

    by_code = {
        str(row.get("stock_code") or row.get("code") or "").zfill(6): row
        for row in (candidate_rows or [])
    }
    sector_data_complete = True
    sector_map = {}
    if stage == "09:25" and evidence:
        try:
            frame = ak.stock_board_industry_summary_ths()
            if frame is None or frame.empty or "板块" not in frame.columns or "涨跌幅" not in frame.columns:
                raise ValueError("板块竞价快照字段不完整")
            for _, row in frame.iterrows():
                name = str(row.get("板块", "")).strip()
                value = row.get("涨跌幅")
                if name and value is not None and str(value).strip() not in ("", "-", "--", "nan", "None"):
                    sector_map[name] = to_float(value)
        except Exception as exc:
            sector_data_complete = False
            result.setdefault("source_status", {})["sector_auction_snapshot"] = {
                "complete": False,
                "error": repr(exc),
            }

    for code, item in evidence.items():
        item["data_source"] = source_text
        if item.get("external_market_snapshot") is not None:
            item["overseas_market"] = item.get("external_market_snapshot")
        if stage == "09:25":
            quote = item.get("auction_or_quote_snapshot") or {}
            if isinstance(quote, dict):
                item["auction_price"] = quote.get("auction_or_snapshot_price")
                item["auction_change_pct"] = quote.get("auction_or_snapshot_change_pct")
            sector = str(
                (by_code.get(code) or {}).get("sector_name")
                or (by_code.get(code) or {}).get("sector")
                or item.get("sector")
                or ""
            ).strip()
            if sector not in sector_map:
                sector_data_complete = False
                item["data_complete"] = False
                item.setdefault("missing_sources", []).append("sector_auction_snapshot")
            else:
                sector_pct = float(sector_map[sector])
                item["sector_auction_change_pct"] = round(sector_pct, 3)
                item["sector_auction_score"] = round(max(0.0, min(100.0, 50.0 + sector_pct * 10.0)), 1)
                item["sector_auction_basis"] = "同花顺行业板块09:25实际快照；50+涨跌幅×10"
                item["data_source"] += "、同花顺行业板块09:25实际快照"

    result["evidence_by_code"] = evidence
    if stage == "09:25" and not sector_data_complete:
        result["data_complete"] = False
    return result


def _v66_true_winrate_line():
    if not _v66_runtime_info:
        return "📈 真实次日胜率：数据库未就绪"
    reports = v66_closed_db.get_winrate_reports(
        db_path=_v66_runtime_info.get("database"), dimension="OVERALL"
    )
    if not reports:
        return "📈 真实次日胜率：暂无已完成跟踪样本"
    row = reports[0]
    win_rate = row.get("win_rate_pct")
    win_text = f"{float(win_rate):.1f}%" if win_rate is not None else "明确胜负样本不足"
    return (
        f"📈 真实次日胜率：{win_text}｜实际样本{row.get('sample_count', 0)}"
        f"｜明确胜负{row.get('decisive_count', 0)}｜顺序不明{row.get('ambiguous_count', 0)}"
    )


def _v66_run_tracking(now):
    try:
        result = v66_track_untracked_candidates(
            _v66_raw_daily_callback,
            as_of=now.isoformat(timespec="seconds"),
        )
        if result.get("tracked_count"):
            print(f"[V6.6闭环] 已写入真实次日OHLC {result['tracked_count']} 条。")
        for item in (result.get("skipped") or [])[:5]:
            print(f"[V6.6闭环] 次日跟踪暂缓 {item.get('code')}：{item.get('reason')}")
        return result
    except Exception as exc:
        print("[V6.6闭环] 次日真实行情跟踪失败，将稍后重试：", repr(exc))
        return {"ok": False, "error": repr(exc)}


def _v66_refresh_close_snapshot(now):
    """15:05用最终板块数据和实际收盘价复核全天累计候选。"""
    snapshot_path = str(_v66_runtime_info.get("snapshot_file") or "")
    snapshot = _load_json_file(snapshot_path, {}) if snapshot_path else {}
    if str(snapshot.get("capture_date", "")) != now.strftime("%Y-%m-%d"):
        raise ValueError("没有当日盘中真实候选截面")

    sector_df = ak.stock_board_industry_summary_ths()
    if sector_df is None or sector_df.empty:
        raise ValueError("15:05最终板块数据为空")
    sector_rows = _sector_rank_rows(sector_df)
    if not sector_rows:
        raise ValueError("15:05最终板块字段无法解析")

    entries = []
    for item in snapshot.get("radar", []) or []:
        if not isinstance(item, dict):
            continue
        for candidate in item.get("_v66_raw_candidates", []) or []:
            if not isinstance(candidate, dict):
                continue
            deep = candidate.get("_v64") or {}
            priority = (
                bool(deep.get("confirmed")),
                float(deep.get("individual_score", 0) or 0),
                float(candidate.get("score", 0) or 0),
                str(candidate.get("_v66_last_seen_at", "")),
            )
            entries.append((priority, item, dict(candidate)))
    entries.sort(key=lambda row: row[0], reverse=True)
    entries = entries[:V66_CLOSE_CANDIDATE_LIMIT]

    regime_cn = str(snapshot.get("market_regime") or "中性")
    regime_code = "WEAK" if regime_cn == "偏弱" else ("STRONG" if regime_cn == "偏强" else "NORMAL")
    grouped = {}
    refresh_failed = 0
    for _, old_item, candidate in entries:
        sector = str(old_item.get("sector", "")).strip()
        target_item = grouped.setdefault(sector, {**old_item, "_v66_raw_candidates": [], "candidates": []})
        code = str(candidate.get("code", "")).zfill(6)
        try:
            quote = get_tencent_quote(code)
            if not quote or float(quote.get("price", 0) or 0) <= 0:
                raise ValueError("收盘报价不可用")
            candidate["price"] = float(quote["price"])
            candidate["pct"] = float(quote.get("pct", 0) or 0)
            deep = v64_deep_confirm(
                candidate,
                item=target_item,
                regime=regime_code,
                source="板块共振",
            )
            candidate["_v64"] = deep
            candidate["_v66_risk"] = v66_close_risk_context(deep)
            candidate["_v66_close_refreshed_at"] = now.isoformat(timespec="seconds")
        except Exception as exc:
            refresh_failed += 1
            candidate["_v64"] = {
                "confirmed": False,
                "reasons": [f"15:05收盘复核失败：{exc}"],
            }
            candidate["_v66_risk"] = {
                "cannot_buy": True,
                "hard_risk_reasons": ["15:05收盘价格或深度数据无法核验"],
                "data_source": "15:05实际接口失败保护",
            }
        target_item["_v66_raw_candidates"].append(candidate)

    market_sentiment = snapshot.get("market_sentiment") or {}
    v66_capture_market_snapshot(
        sector_rows,
        list(grouped.values()),
        market_sentiment,
        regime_cn,
        captured_at=now.isoformat(timespec="seconds"),
    )
    return {
        "accumulated_count": sum(
            len(item.get("_v66_raw_candidates", [])) for item in snapshot.get("radar", []) if isinstance(item, dict)
        ),
        "refreshed_count": len(entries) - refresh_failed,
        "failed_count": refresh_failed,
    }


def _v66_run_close_job(now):
    day = now.strftime("%Y-%m-%d")
    if not _v66_stage_retry_due(V66_STAGE_CLOSE, day, V66_CLOSED_LOOP_RETRY_SECONDS):
        return
    tracking_state = v66_get_job_state(V66_STAGE_TRACKING, day)
    if not tracking_state.get("db_committed"):
        _v66_run_tracking(now)
    close_state = v66_get_job_state(V66_STAGE_CLOSE, day)
    if close_state.get("db_committed") and close_state.get("push_sent"):
        return
    try:
        if close_state.get("db_committed"):
            print("[V6.6闭环] 15:05结果已入库，跳过行情刷新并直接补推微信3群。")
        else:
            refresh = _v66_refresh_close_snapshot(now)
            print(
                f"[V6.6闭环] 收盘复核：全天累计{refresh['accumulated_count']}只，"
                f"刷新{refresh['refreshed_count']}只，失败{refresh['failed_count']}只。"
            )
        result = v66_run_close_selection(now=now.isoformat(timespec="seconds"))
        message = v66_build_close_message(result) + "\n" + _v66_true_winrate_line()
        _v66_push_stage_once(V66_STAGE_CLOSE, day, message)
        print(
            f"[V6.6闭环] 15:05收盘初版完成：板块{len(result.get('sector_forecasts') or [])}个，"
            f"候选{result.get('candidate_count', len(result.get('candidates') or []))}只。"
        )
    except Exception as exc:
        print("[V6.6闭环] 15:05任务未完成，将稍后重试：", repr(exc))


def _v66_run_evening_job(now):
    day = now.strftime("%Y-%m-%d")
    if not _v66_stage_retry_due(V66_STAGE_EVENING, day, V66_CLOSED_LOOP_RETRY_SECONDS):
        return
    state = v66_get_job_state(V66_STAGE_EVENING, day)
    if state.get("db_committed") and state.get("push_sent"):
        return
    candidates = v66_closed_db.get_candidates(day, _v66_runtime_info.get("database"))
    if not candidates:
        return
    try:
        payload = fetch_evening_evidence(candidates, analysis_date=day, ak_module=ak)
        payload = _v66_evidence_with_source(payload, candidates, "19:30")
        result = v66_apply_evening_revision(
            day,
            payload.get("evidence_by_code", {}),
            bool(payload.get("data_complete")),
            revised_at=now.isoformat(timespec="seconds"),
        )
        if result.get("data_complete"):
            _v66_push_stage_once(V66_STAGE_EVENING, day, v66_build_evening_message(result))
            print("[V6.6闭环] 19:30公告/行业/政策/外围修正已完成。")
        else:
            print("[V6.6闭环] 19:30数据不完整，原评分保持不变，稍后重试。")
            _v66_push_degraded_once(V66_STAGE_EVENING, day, "19:30晚间复核")
    except Exception as exc:
        print("[V6.6闭环] 19:30修正失败，将稍后重试：", repr(exc))


def _v66_preopen_candidates(strategy_day):
    return v66_closed_db.get_all_untracked_candidates(
        strategy_day,
        _v66_runtime_info.get("database"),
        include_cancelled=True,
    )


def _v66_run_preopen_job(now, stage):
    strategy_day = now.strftime("%Y-%m-%d")
    state_stage = V66_STAGE_PREOPEN_0900 if stage == "09:00" else V66_STAGE_PREOPEN_0925
    if not _v66_stage_retry_due(state_stage, strategy_day, 50):
        return
    state = v66_get_job_state(state_stage, strategy_day)
    if state.get("db_committed") and state.get("push_sent"):
        return
    candidates = _v66_preopen_candidates(strategy_day)
    if not candidates:
        return
    try:
        payload = fetch_preopen_evidence(
            candidates,
            strategy_date=strategy_day,
            stage=stage,
            quote_fetcher=get_tencent_quote if stage == "09:25" else None,
            ak_module=ak,
        )
        selected_day = str((payload.get("candidate_scope") or {}).get("selected_candidate_date") or "")
        if not selected_day:
            raise ValueError("没有可核验的上一交易日候选日期")
        payload = _v66_evidence_with_source(payload, candidates, stage)
        result = v66_apply_preopen_revision(
            selected_day,
            strategy_day,
            stage,
            payload.get("evidence_by_code", {}),
            bool(payload.get("data_complete")),
            revised_at=now.isoformat(timespec="seconds"),
        )
        if result.get("data_complete"):
            _v66_push_stage_once(state_stage, strategy_day, v66_build_preopen_message(result))
            print(f"[V6.6闭环] {stage}盘前修正已完成。")
        else:
            print(f"[V6.6闭环] {stage}数据不完整，沿用上次有效排序并稍后重试。")
            _v66_push_degraded_once(state_stage, strategy_day, f"{stage}盘前复核")
    except Exception as exc:
        print(f"[V6.6闭环] {stage}盘前任务失败，将稍后重试：", repr(exc))


def v66_run_scheduled_jobs():
    """固定时点闭环：09:00、09:25、15:05、19:30。"""
    if not V66_CLOSED_LOOP_AVAILABLE or not _v66_runtime_info:
        return
    now = datetime.now()
    if now.weekday() >= 5 or not v66_confirmed_trade_day(now.date()):
        return
    hm = now.hour * 60 + now.minute
    if 9 * 60 <= hm < 9 * 60 + 30:
        _v66_run_preopen_job(now, "09:00")
    if 9 * 60 + 25 <= hm < 9 * 60 + 30:
        _v66_run_preopen_job(now, "09:25")
    if hm >= 15 * 60 + 5:
        _v66_run_close_job(now)
    if hm >= 19 * 60 + 30:
        _v66_run_evening_job(now)


def _daily_review_worker():
    """独立运行固定时点闭环和原收盘复盘，避免阻塞盘中主扫描。"""
    while True:
        try:
            v66_run_scheduled_jobs()
            try_daily_review_push()
            if V7_SHADOW_AVAILABLE and v7_run_after_close_if_due is not None:
                v7_result = v7_run_after_close_if_due()
                if v7_result.get("ran"):
                    print(f"[V7统计] 收盘结算完成：{v7_result}")
        except Exception as exc:
            _wework_log("复盘线程异常", repr(exc))
        time.sleep(20)


def main():
    print("=" * 92)
    print("A股机会雷达 V6.6 Pro 次日选股闭环测试版｜微信3群专用")
    print("核心：完整保留 V6.5 盘中扫描；V6.6只新增独立的次日候选闭环。")
    print("新增：15:05初选、19:30消息修正、09:00盘前、09:25竞价、次日真实胜率。")
    print("A/B 级信号会蜂鸣；C 级只显示。")
    print("程序不会自动买股票，所有信号都需要在同花顺里复核。")
    print(f"交易时段每 {SCAN_SECONDS} 秒扫描；非交易时段自动低频待机，按 Ctrl+C 停止。")
    print("推送：仅企业微信；Telegram 已关闭。")
    print("=" * 92)
    load_calibration_state()
    if STATS_V2_AVAILABLE:
        stats_migrate_legacy()
        stats_generate_reports()
        print("统计V2：已启用独立信号、候选漏斗、每日分周期胜率和升级隔离存储。")
    if V64_STATS_AVAILABLE:
        v64_generate_reports()
        print("V6.6统计：沿用发现组/深度通过组对照、60分钟与到收盘胜率。")
    print(f"V6.6行情辅助：{'已加载' if V66_SHADOW_AVAILABLE else '未加载，将自动降级'}。")
    print(f"V6校准器：待跟踪信号 {len(pending_signal_evals)} 条；将自动记录5/15/30/60分钟表现。")
    print("事件雷达 Phase1.1：标题影响 + 实时价格反应确认；新闻源异常不影响行情雷达。")
    v66_initialize_closed_loop_runtime()

    webhook = resolve_wework_webhook()
    if webhook:
        startup_ok = push_message(
            "✅ A股机会雷达 V6.6 Pro 次日选股闭环测试版 已启动\n"
            f"扫描频率：{SCAN_SECONDS}秒\n"
            "盘中：完整保留V6.5。\n"
            "闭环：15:05候选池→19:30修正→09:00/09:25策略→次日真实跟踪。\n"
            "推送通道：仅企业微信3群。"
        )
        if startup_ok:
            print("企业微信通道自检：成功，中文启动消息已送达。")
            retry_pending_wework_messages(force=True)
        else:
            print(f"企业微信通道自检：失败；详细原因已写入 {WEWORK_PUSH_LOG}，程序将自动重试。")
    else:
        print(f"提示：没有找到有效企业微信配置，请检查程序目录里的 {WEWORK_WEBHOOK_FILE}。")

    threading.Thread(target=_daily_review_worker, name="daily-review-worker", daemon=True).start()
    print(f"V6.6 Pro 将在 {V64_START_STAGGER_SECONDS} 秒后开始扫描。")
    time.sleep(V64_START_STAGGER_SECONDS)

    while True:
        try:
            retry_pending_wework_messages()
            try_daily_review_push()
            if V64_STATS_AVAILABLE:
                v64_update_outcomes(get_tencent_quote)
            session = trading_session()
            if session == "OPEN":
                evaluate_pending_signals()
                # 实时市场扫描优先，避免历史候选深度接口拖慢当前行情消息。
                scan_once()
                v6_t1_diagnosis_if_due()
                v61_candidate_watch_if_due()
                v63_prelaunch_scan_if_due()
                v63_behavior_watch_if_due()
                v63_filter_stats_if_due()
                position_monitor()
                event_monitor()
                sleep_seconds = SCAN_SECONDS
            elif session == "PREOPEN":
                print("\n盘前集合竞价阶段，V6竞价雷达待命...")
                scan_auction_if_due()
                sleep_seconds = 20
            else:
                print("\n当前非A股连续交易时间，雷达低频待机。")
                sleep_seconds = 300
        except KeyboardInterrupt:
            print("\n机会雷达已停止。")
            break
        except Exception as e:
            print("本轮扫描失败：", repr(e))
            print("60 秒后自动重试。")
            sleep_seconds = 60

        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("\n机会雷达已停止。")
            break



# ===== V5.6 Unique Pick：唯一优选过滤 =====
def v56_unique_pick(candidates, min_score=85):
    """
    V5.6升级：
    从多个候选中选择最高质量标的。
    不满足门槛时返回空，提示保持观察。
    """
    if not candidates:
        return None

    best = sorted(
        candidates,
        key=lambda x: float(x.get("score", x.get("buy_score", 0))),
        reverse=True
    )[0]

    score = float(best.get("score", best.get("buy_score", 0)))

    if score < min_score:
        return None

    return best


# ===== V5.6 Final Unique Pick =====
def v56_final_unique_pick(candidates, min_score=85):
    """
    最终唯一优选：
    1. 候选按质量分排序
    2. 只保留最高质量标的
    3. 不满足门槛则保持现金
    """
    if not candidates:
        return {
            "status": "NO_BUY",
            "reason": "暂无高质量候选"
        }

    best = sorted(
        candidates,
        key=lambda x: float(x.get("score", x.get("buy_score", 0))),
        reverse=True
    )[0]

    score = float(best.get("score", best.get("buy_score", 0)))

    if score < min_score:
        return {
            "status": "NO_BUY",
            "reason": "最高评分未达到85分"
        }

    return {
        "status": "UNIQUE_PICK",
        "stock": best,
        "score": score
    }


# ===== V5.7 主力资金确认模块 =====
V57_CAPITAL_CONFIRM = True

def capital_signal_label(net_inflow):
    try:
        n = float(net_inflow)
    except Exception:
        n = 0

    if n >= 50000000:
        return "🟢 主力强流入"
    if n >= 10000000:
        return "🟡 主力流入"
    if n < 0:
        return "🔴 主力流出"
    return "⚪ 资金待确认"


# ===== V5.7 Plus 主力资金展示 =====
V57_CAPITAL_DISPLAY = True

def format_capital_flow(net_inflow, days=1):
    try:
        n = float(net_inflow)
    except Exception:
        n = 0

    if abs(n) >= 100000000:
        value = f"{n/100000000:+.2f}亿"
    else:
        value = f"{n/10000:+.0f}万"

    if n > 50000000:
        tag = "🟢 主力强流入"
    elif n > 0:
        tag = "🟡 主力流入"
    elif n < 0:
        tag = "🔴 主力流出"
    else:
        tag = "⚪ 资金待确认"

    return f"{tag}\\n💰 主力资金：{value}\\n📊 周期：{days}日确认"


# ===== V5.7 Plus 信号胜率统计 =====
V57_WINRATE_STATS = True
CALIBRATION_REPORT_FILE = "signal_winrate_report.json"

def generate_winrate_report():
    """
    根据 signal_calibration.csv 自动统计：
    - 信号数量
    - 胜率
    - 平均收益
    - 最大回撤
    """
    import csv, json, os

    if STATS_V2_AVAILABLE:
        d = stats_generate_reports()
        return {
            "signals": d.get("60分钟样本", 0),
            "win_rate": d.get("60分钟胜率%", 0),
            "avg_return": d.get("60分钟平均收益%", 0),
            "max_drawdown": 0,
            "scope": "当天独立信号-60分钟口径",
        }

    result = {
        "signals": 0,
        "win_rate": 0,
        "avg_return": 0,
        "max_drawdown": 0
    }

    file = "signal_calibration.csv"
    if not os.path.exists(file):
        return result

    returns = []

    try:
        with open(file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("收益率%"):
                    returns.append(float(row["收益率%"]))

        if returns:
            result["signals"] = len(returns)
            result["win_rate"] = round(
                sum(1 for x in returns if x > 0) / len(returns) * 100, 1
            )
            result["avg_return"] = round(sum(returns) / len(returns), 2)
            result["max_drawdown"] = round(min(returns), 2)

        with open(CALIBRATION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    except Exception:
        pass

    return result


# ===== V5.7 PLUS 每日收盘复盘模块 =====
V57_DAILY_REVIEW = True

DAILY_REVIEW_FILE = "daily_review.json"

def generate_daily_review():
    """
    每日15:05复盘：
    统计当天信号质量、胜率、平均收益、风险情况。
    """

    import json, os, csv

    # V2严格按当天、按独立信号统计；统一采用60分钟结果展示当日胜负。
    if STATS_V2_AVAILABLE:
        d = stats_generate_reports()
        return {
            "date": d.get("日期", datetime.now().strftime("%Y-%m-%d")),
            "signals": d.get("60分钟样本", 0),
            "wins": d.get("60分钟盈利数", 0),
            "losses": d.get("60分钟亏损数", 0),
            "win_rate": d.get("60分钟胜率%", 0),
            "avg_return": d.get("60分钟平均收益%", 0),
            "best_signal": "", "risk_notes": [],
        }

    result = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "signals": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0,
        "avg_return": 0,
        "best_signal": "",
        "risk_notes": []
    }

    file = "signal_calibration.csv"

    if os.path.exists(file):
        returns = []

        try:
            with open(file, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)

                for row in reader:
                    try:
                        ret = float(row.get("收益率%", 0))
                        returns.append(ret)

                        if ret > 0:
                            result["wins"] += 1
                        else:
                            result["losses"] += 1

                    except Exception:
                        continue

            result["signals"] = len(returns)

            if returns:
                result["win_rate"] = round(
                    result["wins"] / len(returns) * 100, 1
                )
                result["avg_return"] = round(
                    sum(returns) / len(returns), 2
                )

        except Exception:
            pass

    with open(DAILY_REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def daily_review_message():
    r = generate_daily_review()
    today = datetime.now().strftime("%Y-%m-%d")
    s = _load_json_file(V63_STATS_FILE, {}).get(today, {})
    if int(r.get("signals", 0) or 0) > 0:
        performance_line = f"📊 60分钟胜率{r['win_rate']}%｜平均收益{r['avg_return']}%"
    else:
        performance_line = "📊 60分钟胜率：暂无已完成样本｜平均收益：—"
    base = (
        "📋 A股机会雷达 V6.6 Pro｜盘中信号每日复盘\n\n"
        f"📅 {r['date']}｜信号{r['signals']}｜成功{r['wins']}｜失败{r['losses']}\n"
        f"{performance_line}\n\n"
        f"🔎 潜伏{s.get('prelaunch',0)}｜🟢强洗{s.get('strong_wash',0)}｜⚠️诱多{s.get('trap',0)}\n"
        f"🔴派发{s.get('distribution',0)}｜趋势破坏{s.get('down',0)}\n\n"
        "📌 新行为模块先低权重观察，后续按实盘结果校准误判/漏判。"
    )
    report = _load_json_file(SECTOR_REPORT_FILE, {})
    day = report.get(today, {}) if isinstance(report.get(today, {}), dict) else {}
    latest = day.get("latest") or report.get("latest") or {}
    rows = latest.get("rows", []) if isinstance(latest, dict) else []
    if not rows:
        return base
    morning_rows = (day.get("morning") or {}).get("rows", [])
    morning_rank = {x.get("sector"): i + 1 for i, x in enumerate(sorted(morning_rows, key=lambda z: z.get("net", 0), reverse=True))}
    inflow = sorted(rows, key=lambda x: x.get("net", 0), reverse=True)[:3]
    outflow = sorted(rows, key=lambda x: x.get("net", 0))[:3]
    strong = sorted(rows, key=lambda x: (x.get("pct", 0), x.get("net", 0), x.get("breadth", 0)), reverse=True)[:3]
    lines = [base, "", "📊 收盘板块与资金复盘", "", "🔥 最强板块"]
    for i, x in enumerate(strong, 1):
        old = morning_rank.get(x.get("sector"))
        change = f"｜早盘资金第{old}" if old else ""
        lines.append(f"{i}. {x['sector']} {x['pct']:+.2f}%｜资金{x['net']:+.2f}亿{change}")
    lines.extend(["", "💰 流入前三｜" + "｜".join(f"{x['sector']} {x['net']:+.1f}亿" for x in inflow)])
    lines.append("💸 流出前三｜" + "｜".join(f"{x['sector']} {x['net']:+.1f}亿" for x in outflow))
    candidates = [x for x in inflow if 0.5 <= x.get("pct", 0) <= 4.0 and x.get("breadth", 0) >= 1.5]
    overheated = [x for x in strong if x.get("pct", 0) > 4.0]
    if candidates:
        lines.extend(["", "🟢 明日延续观察｜" + "、".join(x["sector"] for x in candidates)])
        lines.append("👉 仅进入观察池，次日09:45等待回踩与资金确认。")
    if overheated:
        lines.append("🟡 过热不追｜" + "、".join(x["sector"] for x in overheated))
    lines.append("📌 资金为行情源估算；本报告不改变原选股和买卖点。")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n雷达已由用户停止。")
    except Exception as exc:
        # 防止双击启动时窗口一闪而过，并把完整错误留在同目录便于排查。
        import traceback

        error_text = traceback.format_exc()
        print("\n雷达启动或运行时发生错误：")
        print(error_text)
        try:
            with open("market_radar_error.log", "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
                f.write(error_text)
        except Exception:
            pass
        try:
            input("按回车键关闭窗口……")
        except Exception:
            pass
