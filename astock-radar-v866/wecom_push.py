# -*- coding: utf-8 -*-
"""Enterprise WeChat transport shared by the V8.6.6 validation runtime.

The webhook is never stored in source. The new runtime first checks its own
environment variable, then reuses the legacy V8 webhook file path when the
server provides V8_WEBHOOK_FILE.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from config import WEWORK_WEBHOOK_ENV


def _webhook_from_file(path_value: str) -> str:
    path = Path(path_value).expanduser()
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            for key in ("webhook", "url", "WEWORK_WEBHOOK_URL"):
                value = str(payload.get(key, "")).strip()
                if value:
                    return value
        except (TypeError, ValueError):
            return ""
    return text.splitlines()[0].strip()


def resolve_webhook() -> tuple[str, str]:
    direct = os.getenv(WEWORK_WEBHOOK_ENV, "").strip()
    if direct:
        return direct, WEWORK_WEBHOOK_ENV
    legacy_file = os.getenv("V8_WEBHOOK_FILE", "").strip()
    if legacy_file:
        value = _webhook_from_file(legacy_file)
        if value:
            return value, "V8_WEBHOOK_FILE"
    return "", "UNCONFIGURED"


def webhook_status() -> dict:
    url, source = resolve_webhook()
    return {"configured": bool(url), "configuration_source": source}


def send_text_result(text: str, timeout: float = 10.0) -> dict:
    url, source = resolve_webhook()
    if not url:
        return {"ok": False, "status": "NOT_CONFIGURED", "error": "企业微信机器人地址未配置", "configuration_source": source}
    try:
        response = requests.post(url, json={"msgtype": "text", "text": {"content": str(text)}}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        ok = payload.get("errcode") == 0
        return {
            "ok": ok,
            "status": "OK" if ok else "WECHAT_REJECTED",
            "error": "" if ok else str(payload.get("errmsg", "企业微信拒绝消息")),
            "configuration_source": source,
        }
    except requests.Timeout:
        return {"ok": False, "status": "TIMEOUT", "error": "企业微信请求超时", "configuration_source": source}
    except Exception as exc:
        return {"ok": False, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}", "configuration_source": source}


def send_text(text: str) -> bool:
    return bool(send_text_result(text).get("ok"))


def build_a_signal_message(signal: dict) -> str:
    return (
        "A股机会雷达 V8.6.6｜真实A级验证信号\n"
        f"{signal.get('name', '')} {signal.get('ts_code', '')}\n"
        f"规则评分：{float(signal.get('score', 0) or 0):.1f}｜盈亏比：{float(signal.get('rr', 0) or 0):.2f}:1\n"
        f"板块：{signal.get('sector', '-') or '-'}｜市场：{signal.get('market_phase', '-') or '-'}\n"
        f"事件风险：{float(signal.get('event_risk', 0) or 0):.1f}｜派发风险：{float(signal.get('distribution_risk', 0) or 0):.1f}\n"
        f"来源：{signal.get('source', '-') or '-'}｜数据质量：{float(signal.get('data_quality', 0) or 0):.2f}\n"
        "真实胜率：样本不足，暂不生成\n"
        "状态：仅验证与展示，不自动交易"
    )


def push_a_signal(signal: dict) -> bool:
    if str(signal.get("pool", "")).upper() != "A":
        return False
    return send_text(build_a_signal_message(signal))
