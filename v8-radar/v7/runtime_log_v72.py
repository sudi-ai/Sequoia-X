from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "v72_runtime.jsonl"
_WEBHOOK_RE = re.compile(r"https?://[^\s\"']*(?:weixin|wecom|qyapi)[^\s\"']*", re.I)
_TOKEN_RE = re.compile(r"(?i)(token|api[_-]?key|webhook)\s*[=:]\s*[^\s,;]+")


def _safe_text(value: Any) -> str:
    text = str(value or "")
    for secret_name in ("TUSHARE_TOKEN", "WEWORK_WEBHOOK_URL_V66_GROUP3", "V71_WEWORK_WEBHOOK", "WEWORK_WEBHOOK_URL"):
        secret = os.getenv(secret_name, "").strip()
        if secret:
            text = text.replace(secret, "***REDACTED***")
    text = _WEBHOOK_RE.sub("***WEBHOOK_REDACTED***", text)
    text = _TOKEN_RE.sub(lambda m: m.group(1) + "=***REDACTED***", text)
    return text[:1200]


def log_event(module: str, message: str, *, code: str | None = None, exc: BaseException | None = None,
              degraded: bool = False, level: str = "ERROR", event: str | None = None) -> None:
    """Best-effort UTF-8 structured logging. Logging failure never interrupts runtime."""
    try:
        row = {
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": level,
            "module": str(module),
            "event": str(event or "runtime"),
            "code": str(code or ""),
            "exception_type": type(exc).__name__ if exc is not None else "",
            "message": _safe_text(message if exc is None else f"{message}: {exc}"),
            "degraded": bool(degraded),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return


def daily_error_summary(day: str | None = None) -> dict[str, Any]:
    day = day or datetime.now().astimezone().date().isoformat()
    modules: Counter[str] = Counter()
    events: Counter[str] = Counter()
    total = 0
    try:
        if not LOG_PATH.exists():
            return {"trade_date": day, "total": 0, "by_module": {}, "by_event": {}}
        for line in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("time", ""))[:10] != day or str(row.get("level", "")).upper() not in {"ERROR", "WARNING"}:
                continue
            total += 1
            modules[str(row.get("module") or "unknown")] += 1
            events[str(row.get("event") or "runtime")] += 1
    except Exception:
        return {"trade_date": day, "total": 0, "by_module": {}, "by_event": {}, "summary_error": True}
    return {"trade_date": day, "total": total, "by_module": dict(modules), "by_event": dict(events)}
