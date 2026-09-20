# -*- coding: utf-8 -*-
"""Scheduled, read-only WeCom market brief for the V8.6.6 operator.

This is deliberately separate from the A-signal bridge: a market brief helps
the operator understand the session, while only a newly created, PIT-safe A
signal may create an immediate execution alert.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from p0_realtime import DB_PATH
from pit_store import DEFAULT_DB
from wecom_push import send_text_result, webhook_status

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "v866_market_digest_state.json"
AUDIT_PATH = DATA_DIR / "MARKET_DIGEST_AUDIT.json"
CHECKPOINTS = ("09:35", "10:00", "11:00", "13:30", "14:30")
CN_TZ = timezone(timedelta(hours=8))
MAX_SNAPSHOT_AGE = timedelta(minutes=5)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _latest_snapshot() -> dict | None:
    if not DB_PATH.is_file():
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM snapshot_manifest ORDER BY observed_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def _snapshot_freshness(snapshot: dict, current: datetime) -> tuple[bool, str, dict]:
    """Reject stale or cross-session data before any WeCom market brief.

    `force` may bypass the scheduler window, but it must never bypass this
    check: yesterday's close cannot be labelled as today's intraday report.
    """
    observed_raw = str(snapshot.get("observed_at") or "")
    trade_date = str(snapshot.get("trade_date") or "").replace("-", "")
    expected_date = current.strftime("%Y%m%d")
    detail = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_trade_date": trade_date,
        "expected_trade_date": expected_date,
        "observed_at": observed_raw,
    }
    if trade_date != expected_date:
        return False, "TRADE_DATE_MISMATCH", detail
    try:
        observed = datetime.fromisoformat(observed_raw.replace("Z", "+00:00"))
    except ValueError:
        return False, "INVALID_OBSERVED_AT", detail
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=CN_TZ)
    observed = observed.astimezone(CN_TZ)
    age = current - observed
    detail["age_seconds"] = round(age.total_seconds(), 1)
    if observed.strftime("%Y%m%d") != expected_date:
        return False, "SNAPSHOT_DATE_MISMATCH", detail
    if age < timedelta(minutes=-1):
        return False, "FUTURE_SNAPSHOT", detail
    if age > MAX_SNAPSHOT_AGE:
        return False, "STALE_SNAPSHOT", detail
    if int(snapshot.get("is_pit_safe", 0) or 0) != 1:
        return False, "PIT_UNSAFE", detail
    if float(snapshot.get("data_quality", 0) or 0) < 0.75:
        return False, "LOW_DATA_QUALITY", detail
    return True, "FRESH", detail


def _market_payload(snapshot_id: str, trade_date: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        coverage = conn.execute(
            "SELECT COUNT(*) AS stocks, "
            "SUM(CASE WHEN pct_chg>0 THEN 1 ELSE 0 END) AS up, "
            "SUM(CASE WHEN pct_chg<0 THEN 1 ELSE 0 END) AS down "
            "FROM intraday_quote WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        leaders = conn.execute(
            "SELECT name,ts_code,pct_chg,market_rank,industry FROM intraday_quote "
            "WHERE snapshot_id=? ORDER BY market_rank ASC LIMIT 3", (snapshot_id,)
        ).fetchall()
        sectors = conn.execute(
            "SELECT sector,median_pct,up_ratio FROM sector_realtime_snapshot "
            "WHERE snapshot_id=? ORDER BY diffusion_rank ASC LIMIT 3", (snapshot_id,)
        ).fetchall()
        discovery = conn.execute(
            "SELECT COUNT(DISTINCT ts_code) FROM discovery_observation WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()[0]
    a_count = 0
    if DEFAULT_DB.is_file():
        try:
            with sqlite3.connect(DEFAULT_DB) as conn:
                a_count = int(conn.execute(
                    "SELECT COUNT(*) FROM signal_snapshot WHERE trade_date=? AND UPPER(pool)='A' "
                    "AND is_pit_safe=1 AND UPPER(COALESCE(source,'')) NOT LIKE '%DEMO%'",
                    (trade_date,),
                ).fetchone()[0] or 0)
        except sqlite3.Error:
            a_count = 0
    return {
        "coverage": dict(coverage) if coverage else {}, "leaders": [dict(row) for row in leaders],
        "sectors": [dict(row) for row in sectors], "discovery_count": int(discovery or 0), "a_count": a_count,
    }


def _due_checkpoint(value: datetime) -> str | None:
    current = value.replace(second=0, microsecond=0)
    for label in CHECKPOINTS:
        hour, minute = (int(part) for part in label.split(":"))
        target = value.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= current < target + timedelta(minutes=3):
            return label
    return None


def _format_market_brief(snapshot: dict, payload: dict, label: str) -> str:
    coverage = payload["coverage"]
    market = "上涨 {up}｜下跌 {down}｜覆盖 {stocks}".format(
        up=int(coverage.get("up") or 0), down=int(coverage.get("down") or 0), stocks=int(coverage.get("stocks") or 0)
    )
    sector_text = "；".join(
        "%s %+.2f%%（上涨占比 %.0f%%）" % (row.get("sector") or "未分类", float(row.get("median_pct") or 0), float(row.get("up_ratio") or 0) * 100)
        for row in payload["sectors"]
    ) or "暂无有效板块快照"
    leader_text = "\n".join(
        "• %s %s｜%+.2f%%｜%s" % (row.get("name") or "未命名", row.get("ts_code") or "--", float(row.get("pct_chg") or 0), row.get("industry") or "未分类")
        for row in payload["leaders"]
    ) or "暂无强势股记录"
    execution = "当前无真实A级信号：不执行" if payload["a_count"] == 0 else "当前有 %s 条真实A级信号：请以单独A级消息逐项人工复核" % payload["a_count"]
    return (
        "🟡 A股机会雷达 V8.6.6｜%s盘中作战简报\n\n"
        "🕘 服务器接收时间 %s（行情源时间待核验）\n"
        "🌐 市场：%s\n"
        "🏭 主线：%s\n"
        "📈 强势观察\n%s\n"
        "⭐ 发现层：%s 只盘中强势记录（不等于买入）\n"
        "⚖ 执行结论：%s\n\n"
        "👉 操作\n"
        "先看市场和主线，再等独立A级复核消息；观察股、涨停股和未通过门槛的股票均不追。\n"
        "🔎 依据：%s｜数据质量 %.2f｜PIT安全：%s\n"
        "⚠ 数据限制：盘中强势记录不等于可成交信号；不构成投资建议。"
    ) % (
        label, snapshot.get("observed_at") or "--", market, sector_text, leader_text,
        payload["discovery_count"], execution, snapshot.get("source") or "--",
        float(snapshot.get("data_quality") or 0), "是" if snapshot.get("is_pit_safe") else "否",
    )


def run_once(force: bool = False) -> dict:
    snapshot = _latest_snapshot()
    current = now_cn()
    audit = {"checked_at": current.isoformat(timespec="seconds"), "status": "WAITING", "webhook": webhook_status(), "sent": 0, "reason": ""}
    if not snapshot:
        audit.update(status="NO_SNAPSHOT", reason="尚无盘中PIT快照")
        write_json(AUDIT_PATH, audit)
        return audit
    fresh, freshness_status, freshness_detail = _snapshot_freshness(snapshot, current)
    audit["freshness"] = freshness_detail
    if not fresh:
        audit.update(
            status=freshness_status,
            reason="简报已拦截：快照不是当前交易日的有效实时PIT数据，不发送企业微信消息。",
        )
        write_json(AUDIT_PATH, audit)
        return audit
    checkpoint = _due_checkpoint(current)
    label = checkpoint or "即时"
    if not force and not checkpoint:
        audit.update(status="WAITING_CHECKPOINT", reason="当前不在定时简报窗口")
        write_json(AUDIT_PATH, audit)
        return audit
    state = read_json(STATE_PATH)
    key = "%s|%s" % (snapshot.get("trade_date") or current.strftime("%Y%m%d"), label)
    if not force and state.get("sent", {}).get(key):
        audit.update(status="ALREADY_SENT", reason="该时点简报已经发送")
        write_json(AUDIT_PATH, audit)
        return audit
    payload = _market_payload(str(snapshot["snapshot_id"]), str(snapshot.get("trade_date") or ""))
    result = send_text_result(_format_market_brief(snapshot, payload, label))
    audit.update(status=result.get("status", "ERROR"), sent=int(bool(result.get("ok"))), reason=result.get("error", ""), snapshot_id=snapshot.get("snapshot_id"), checkpoint=label, a_count=payload["a_count"], discovery_count=payload["discovery_count"])
    if result.get("ok"):
        state.setdefault("sent", {})[key] = current.isoformat(timespec="seconds")
        write_json(STATE_PATH, state)
    write_json(AUDIT_PATH, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="立即发送一条当前真实盘中简报")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    if args.once:
        print(json.dumps(run_once(force=True), ensure_ascii=False))
        return 0
    while True:
        try:
            run_once()
        except Exception as exc:
            write_json(AUDIT_PATH, {"checked_at": now_cn().isoformat(timespec="seconds"), "status": "ERROR", "reason": "%s: %s" % (type(exc).__name__, exc), "sent": 0, "webhook": webhook_status()})
        time.sleep(max(15, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
