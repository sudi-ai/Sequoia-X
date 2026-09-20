from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .config import CONFIG
from .storage import connect, initialize


def _webhook() -> str:
    try:
        return CONFIG.webhook_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def send_once(dedupe_key: str, message: str, *, path: Path | None = None, max_attempts: int = 3) -> tuple[bool, str]:
    """V8-only WeCom sender. Disabled means no network call and no false success record."""
    if not CONFIG.push_enabled:
        return False, "push_disabled"
    hook = _webhook()
    if not hook.startswith("https://"):
        return False, "webhook_missing"
    initialize(path); c = connect(path)
    row = c.execute("select status,attempts from v8_push_state where dedupe_key=?", (dedupe_key,)).fetchone()
    if row and row["status"] == "SENT":
        c.close(); return False, "deduped"
    attempts = int(row["attempts"] if row else 0)
    if attempts >= max_attempts:
        c.close(); return False, "max_attempts"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    c.execute("""INSERT INTO v8_push_state(dedupe_key,status,attempts,last_attempt_at,message_hash)
      VALUES(?,?,?,?,?) ON CONFLICT(dedupe_key) DO UPDATE SET status='PENDING',attempts=v8_push_state.attempts+1,
      last_attempt_at=excluded.last_attempt_at,message_hash=excluded.message_hash""",
      (dedupe_key,"PENDING",1,now,digest)); c.commit(); c.close()
    try:
        body = json.dumps({"msgtype":"text","text":{"content":message}}, ensure_ascii=False).encode("utf-8")
        req = Request(hook, data=body, headers={"Content-Type":"application/json; charset=utf-8"}, method="POST")
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        ok = int(result.get("errcode", -1)) == 0
        detail = str(result.get("errmsg") or result)
    except Exception as exc:
        ok, detail = False, type(exc).__name__
    c = connect(path)
    c.execute("update v8_push_state set status=?,last_error=?,sent_at=? where dedupe_key=?",
              ("SENT" if ok else "FAILED", "" if ok else detail, now if ok else None, dedupe_key))
    c.commit(); c.close()
    return ok, detail
