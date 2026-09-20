# -*- coding: utf-8 -*-
"""Read-only V8.6.6 -> legacy V7 WeChat group bridge.

Only newly inserted, real, PIT-safe A signals are eligible. Discovery rows,
DEMO data, legacy imports, A+ activation and trading actions are excluded.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import DATA_DIR
from pit_store import DEFAULT_DB
from wecom_push import build_a_signal_message, send_text_result, webhook_status
from runtime_freshness import quote_guard, market_session

STATE_DB = DATA_DIR / "v866_push_bridge.db"
AUDIT_JSON = DATA_DIR / "PUSH_BRIDGE_AUDIT.json"
AUDIT_TXT = DATA_DIR / "PUSH_BRIDGE_AUDIT.txt"
SCHEMA = """
CREATE TABLE IF NOT EXISTS bridge_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sent_signal(
    snapshot_id TEXT PRIMARY KEY,
    signal_id INTEGER NOT NULL,
    decision_time TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def write_audit(payload: dict) -> None:
    AUDIT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "V8.6.6 企业微信推送桥审计",
        f"检查时间: {payload.get('checked_at', '')}",
        f"状态: {payload.get('status', '')}",
        f"机器人配置: {'已配置' if payload.get('webhook', {}).get('configured') else '未配置'}",
        f"配置来源: {payload.get('webhook', {}).get('configuration_source', '')}",
        f"本次发现: {payload.get('found', 0)}",
        f"本次发送: {payload.get('sent', 0)}",
        f"本次跳过: {payload.get('skipped', 0)}",
        f"最近错误: {payload.get('last_error', '') or '无'}",
        "安全规则: 仅真实、PIT安全、15分钟内新增的A级信号；不推DEMO/历史补录/发现样本；不自动交易。",
    ]
    AUDIT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PushBridge:
    def __init__(self, source_db: Path = DEFAULT_DB, state_db: Path = STATE_DB):
        self.source_db = Path(source_db)
        self.state_db = Path(state_db)
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.state_db)) as conn, conn:
            conn.executescript(SCHEMA)

    def _meta(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.state_db)) as conn:
            row = conn.execute("SELECT value FROM bridge_meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        with closing(sqlite3.connect(self.state_db)) as conn, conn:
            conn.execute(
                "INSERT INTO bridge_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def _max_signal_id(self) -> int:
        if not self.source_db.is_file():
            return 0
        with closing(sqlite3.connect(self.source_db)) as conn:
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM signal_snapshot").fetchone()
        return int(row[0] or 0)

    def initialize(self) -> dict:
        current = self._meta("last_signal_id")
        if current is not None:
            return {"initialized": True, "skipped_existing": 0, "last_signal_id": int(current)}
        maximum = self._max_signal_id()
        self._set_meta("last_signal_id", str(maximum))
        self._set_meta("initialized_at", now_iso())
        return {"initialized": True, "skipped_existing": maximum, "last_signal_id": maximum}

    def _new_rows(self, after_id: int) -> list[dict]:
        if not self.source_db.is_file():
            return []
        with closing(sqlite3.connect(self.source_db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signal_snapshot WHERE id>? ORDER BY id ASC LIMIT 200", (after_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item.update(json.loads(item.get("features_json") or "{}"))
            except (TypeError, ValueError):
                pass
            result.append(item)
        return result

    @staticmethod
    def eligible(row: dict) -> tuple[bool, str]:
        fresh, freshness_reason = quote_guard(row)
        if not market_session() or not fresh:
            return False, '报价或交易时段不满足实时推送：'+freshness_reason
        if str(row.get("pool", "")).upper() != "A":
            return False, "不是A级信号"
        source = str(row.get("source", "")).upper()
        if not source or any(word in source for word in ("DEMO", "MOCK", "SIM", "LEGACY")):
            return False, "非实时真实来源"
        if int(row.get("is_pit_safe", 0) or 0) != 1:
            return False, "PIT不安全"
        if float(row.get("data_quality", 0) or 0) < 0.75:
            return False, "数据质量不足"
        observed = parse_time(row.get("observed_at", ""))
        decision = parse_time(row.get("decision_time", ""))
        if not observed or not decision or observed > decision:
            return False, "时间戳不满足PIT"
        if decision-observed > timedelta(minutes=3):
            return False, '分析使用的行情超过三分钟，禁止新时间包装旧行情'
        now = datetime.now(timezone.utc)
        if decision > now + timedelta(minutes=1) or decision < now - timedelta(minutes=15):
            return False, "不是15分钟内新信号"
        return True, "通过"

    def run_once(self, dry_run: bool = False) -> dict:
        init = self.initialize()
        last_id = int(self._meta("last_signal_id") or 0)
        rows = self._new_rows(last_id)
        audit = {
            "checked_at": now_iso(),
            "status": "READY",
            "webhook": webhook_status(),
            "initialized": init,
            "found": len(rows),
            "sent": 0,
            "skipped": 0,
            "last_error": "",
            "dry_run": bool(dry_run),
        }
        for row in rows:
            ok, reason = self.eligible(row)
            if not ok:
                audit["skipped"] += 1
                if not dry_run:
                    self._set_meta("last_signal_id", str(row["id"]))
                continue
            if dry_run:
                audit["status"] = "DRY_RUN_ELIGIBLE"
                continue
            result = send_text_result(build_a_signal_message(row))
            if not result.get("ok"):
                audit["status"] = result.get("status", "ERROR")
                audit["last_error"] = result.get("error", "发送失败")
                break
            with closing(sqlite3.connect(self.state_db)) as conn, conn:
                conn.execute(
                    "INSERT OR IGNORE INTO sent_signal VALUES(?,?,?,?,?)",
                    (row["snapshot_id"], row["id"], row["decision_time"], now_iso(), "OK"),
                )
            audit["sent"] += 1
            self._set_meta("last_signal_id", str(row["id"]))
        write_audit(audit)
        return audit


def test_message() -> dict:
    message = (
        "A股机会雷达 V8.6.6｜新版推送通道测试\n"
        "新版工作台、真实数据接口与PIT采集器已就绪。\n"
        "本消息仅验证新版群通道，不构成交易建议，不自动交易。"
    )
    result = send_text_result(message)
    audit = {
        "checked_at": now_iso(), "status": result.get("status"), "webhook": webhook_status(),
        "found": 0, "sent": int(bool(result.get("ok"))), "skipped": 0,
        "last_error": result.get("error", ""), "test_message": True,
    }
    write_audit(audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-message", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=int(os.getenv("V866_PUSH_POLL_SECONDS", "30")))
    args = parser.parse_args()
    if args.test_message:
        result = test_message()
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("sent") == 1 else 1
    bridge = PushBridge()
    if args.once or args.dry_run:
        result = bridge.run_once(dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("status") not in ("ERROR", "TIMEOUT", "NOT_CONFIGURED", "WECHAT_REJECTED") else 1
    while True:
        bridge.run_once()
        time.sleep(max(10, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
