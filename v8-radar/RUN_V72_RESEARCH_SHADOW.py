from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CN = ZoneInfo("Asia/Shanghai")
LOCK = ROOT / ".v72_research_shadow.lock"
STATE = ROOT / ".v72_scheduler_state.json"
CHECKPOINTS = ("09:25", "09:35", "10:00", "10:10")
CATALYST_CHECKPOINTS = ("08:50", "09:10", "10:00", "13:30", "14:30", "15:15", "19:30", "22:00")


def _load(test: bool = False) -> None:
    from v7.env_loader import load_project_env
    load_project_env()
    os.environ.setdefault("V7_SHADOW_ENABLED", "true")
    os.environ.setdefault("V71_ENABLE", "true")
    os.environ.setdefault("V72_RESEARCH_ENABLE", "true")
    os.environ.setdefault("V72_PORTFOLIO_ENABLE", "true")
    if test:
        os.environ["V71_PUSH_ENABLED"] = "false"
        os.environ["V72_PORTFOLIO_PUSH"] = "false"
        os.environ["V72_RESEARCH_PUSH"] = "false"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _lock() -> bool:
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip() or 0)
            if _alive(pid):
                return False
            LOCK.unlink(missing_ok=True)
        except Exception as exc:
            from v7.runtime_log_v72 import log_event
            log_event("scheduler", "读取/清理单实例锁失败", exc=exc, degraded=True, event="lock")
            return False
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except Exception as exc:
        from v7.runtime_log_v72 import log_event
        log_event("scheduler", "创建单实例锁失败", exc=exc, degraded=True, event="lock")
        return False


def _unlock() -> None:
    try:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()
    except Exception as exc:
        from v7.runtime_log_v72 import log_event
        log_event("scheduler", "释放单实例锁失败", exc=exc, degraded=True, event="lock")


def _launch():
    return subprocess.Popen([sys.executable, "-u", str(ROOT / "market_radar_V6_6_Pro.py")], cwd=str(ROOT), env=os.environ.copy())


def _blank_state(day: str) -> dict:
    return {
        "trade_date": day,
        "candidate_checkpoints": [],  # backward-compatible completed list
        "checkpoint_status": {},
        "catalyst_checkpoints": [],
        "after_close_done": False,
    }


def _state(day: str) -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        if d.get("trade_date") == day:
            d.setdefault("candidate_checkpoints", [])
            d.setdefault("checkpoint_status", {})
            d.setdefault("catalyst_checkpoints", [])
            d.setdefault("after_close_done", False)
            return d
    except FileNotFoundError:
        pass
    except Exception as exc:
        from v7.runtime_log_v72 import log_event
        log_event("scheduler", "读取调度状态失败，将使用当日新状态", exc=exc, degraded=True, event="scheduler_state")
    return _blank_state(day)


def _save(s: dict) -> None:
    try:
        STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        from v7.runtime_log_v72 import log_event
        log_event("scheduler", "保存调度状态失败", exc=exc, degraded=True, event="scheduler_state")


def _iso_plus(now: datetime, seconds: int) -> str:
    return (now + timedelta(seconds=max(1, seconds))).isoformat(timespec="seconds")


def _in_checkpoint_window(now: datetime, checkpoint: str, window_minutes: int = 15) -> bool:
    """Run a checkpoint only near its scheduled time; never replay stale scans after startup."""
    hour, minute = (int(x) for x in checkpoint.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (now - target).total_seconds()
    return 0 <= delta <= window_minutes * 60


def _checkpoint_record(s: dict, cp: str) -> dict:
    rec = (s.setdefault("checkpoint_status", {})).setdefault(cp, {
        "status": "PENDING", "attempts": 0, "last_attempt_at": None,
        "next_retry_at": None, "last_error": "", "completed_at": None,
    })
    return rec


def _checkpoint_due(rec: dict, now: datetime) -> bool:
    if rec.get("status") in {"COMPLETED", "FAILED"}:
        return False
    nxt = rec.get("next_retry_at")
    if not nxt:
        return True
    try:
        return now >= datetime.fromisoformat(str(nxt)).astimezone(CN)
    except Exception:
        return True


def run_checkpoint_once(cp: str, *, now: datetime, state: dict, runner, max_retries: int, retry_seconds: int) -> dict:
    """Run one candidate checkpoint. Only explicit success marks it completed."""
    from v7.runtime_log_v72 import log_event
    rec = _checkpoint_record(state, cp)
    if not _checkpoint_due(rec, now):
        return {"ran": False, "status": rec.get("status"), "checkpoint": cp}
    rec["attempts"] = int(rec.get("attempts") or 0) + 1
    rec["last_attempt_at"] = now.isoformat(timespec="seconds")
    try:
        result = runner(now=now, trade_day_override="OPEN")
        success = bool(isinstance(result, dict) and result.get("success", int(result.get("errors") or 0) == 0) and int(result.get("errors") or 0) == 0)
        reason = str(result.get("reason") or "") if isinstance(result, dict) else "invalid_result"
        if success and reason not in {"trade_calendar_unknown"}:
            rec.update({"status": "COMPLETED", "next_retry_at": None, "last_error": "", "completed_at": now.isoformat(timespec="seconds")})
            completed = set(state.get("candidate_checkpoints") or [])
            completed.add(cp)
            state["candidate_checkpoints"] = sorted(completed)
            return {"ran": True, "status": "COMPLETED", "checkpoint": cp, "result": result}
        err = reason or f"errors={result.get('errors') if isinstance(result, dict) else 'unknown'}"
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        log_event("candidate_checkpoint", f"{cp} 检查点执行异常", exc=exc, degraded=True, event="checkpoint")
    rec["last_error"] = err[:500]
    if rec["attempts"] >= max_retries:
        rec["status"] = "FAILED"
        rec["next_retry_at"] = None
        log_event("candidate_checkpoint", f"{cp} 达到最大重试次数，标记FAILED：{err}", degraded=True, event="checkpoint")
    else:
        rec["status"] = "RETRY"
        rec["next_retry_at"] = _iso_plus(now, retry_seconds)
        log_event("candidate_checkpoint", f"{cp} 失败，等待重试：{err}", degraded=True, level="WARNING", event="checkpoint")
    return {"ran": True, "status": rec["status"], "checkpoint": cp, "error": err, "attempts": rec["attempts"]}


def _after_close(now: datetime, s: dict) -> None:
    from v7.runtime_log_v72 import log_event
    try:
        from v7.daily_maintenance import run_after_close_if_due
        from v7.forward_v72 import settle_all_from_daily_prices
        from v7.history_archive_v72 import archive_after_close_targets
        maintenance = run_after_close_if_due(now.replace(tzinfo=None))
        archive = archive_after_close_targets(now=now)
        forward = settle_all_from_daily_prices() if maintenance.get("ran") or maintenance.get("reason") == "already_done" else {"skipped": True, "reason": maintenance.get("reason")}
        print("[V7.2] 收盘维护：", json.dumps({"maintenance": maintenance, "minute_archive": archive, "forward": forward}, ensure_ascii=False, default=str))
        if maintenance.get("ran") or maintenance.get("reason") in {"already_done", "market_closed"}:
            try:
                from v7.reporting_v72 import build_evening_review
                from v7.wework_shadow_push import send_v72_evening_review
                send_v72_evening_review(build_evening_review())
            except Exception as exc:
                log_event("evening_review_push", "晚间复盘构建/推送失败", exc=exc, degraded=True, event="evening_review")
            s["after_close_done"] = True
    except Exception as exc:
        log_event("after_close_maintenance", "收盘维护失败", exc=exc, degraded=True, event="after_close")
        print("[V7.2] 收盘维护失败：", type(exc).__name__)


def scheduler(proc=None, poll: int = 10):
    from v7.config import V72_CONFIG
    from v7.shadow_state_runtime import run_pending_cycle, trade_day_status
    from v7.portfolio_runtime_v72 import scan_all_positions
    from v7.api_health import run_health_check
    from v7.catalyst_shadow_v72 import run_catalyst_scan
    from v7.runtime_log_v72 import log_event
    last_port = 0.0
    while True:
        if proc is not None and proc.poll() is not None:
            return int(proc.returncode or 0)
        now = datetime.now(CN)
        day = now.date().isoformat()
        s = _state(day)  # new natural day automatically discards prior-day checkpoint state
        cal = trade_day_status(now)
        try:
            run_health_check(force=False, now=now, save=True)
        except Exception as exc:
            log_event("api_health", "接口健康检查调度失败", exc=exc, degraded=True, event="health_check")
        if cal.get("status") == "OPEN":
            current = now.strftime("%H:%M")
            catalyst_done = set(s.get("catalyst_checkpoints") or [])
            for cp in CATALYST_CHECKPOINTS:
                if cp in catalyst_done or not _in_checkpoint_window(now, cp):
                    continue
                try:
                    result = run_catalyst_scan(now=now, push=True)
                    print("[V7.2] Catalyst Shadow:", json.dumps(result, ensure_ascii=False, default=str))
                except Exception as exc:
                    log_event("catalyst_scan", f"{cp} Catalyst扫描失败", exc=exc, degraded=True, event="catalyst")
                finally:
                    # One attempt per checkpoint prevents an interface outage from causing request storms.
                    catalyst_done.add(cp)
                    s["catalyst_checkpoints"] = sorted(catalyst_done)
            for cp in CHECKPOINTS:
                if current >= cp and current <= "10:15":
                    run_checkpoint_once(cp, now=now, state=s, runner=run_pending_cycle,
                                        max_retries=V72_CONFIG.checkpoint_max_retries,
                                        retry_seconds=V72_CONFIG.checkpoint_retry_seconds)
            t = now.time().replace(tzinfo=None)
            in_session = (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))
            if V72_CONFIG.portfolio_enable and in_session and time.monotonic() - last_port >= V72_CONFIG.holding_scan_seconds:
                try:
                    scan_all_positions(now=now, push=True)
                except Exception as exc:
                    log_event("portfolio_scan", "Portfolio扫描失败", exc=exc, degraded=True, event="portfolio_scan")
                last_port = time.monotonic()
            if current >= "15:10" and not s.get("after_close_done"):
                _after_close(now, s)
        _save(s)
        time.sleep(max(5, poll))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--test-mode", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    _load(a.test_mode)
    if not _lock():
        print("[V7.2] 已有Research Shadow实例运行，本次不重复启动。")
        return 0
    proc = None
    try:
        from v7.migration_v72 import migrate
        mig = migrate(make_backup=True)
        if not mig.get("ok"):
            print("[V7.2] 数据库迁移失败，已尝试回滚；Research Shadow不启动。")
            return 2
        if a.once:
            from v7.portfolio_runtime_v72 import scan_all_positions
            print(json.dumps(scan_all_positions(push=False), ensure_ascii=False, indent=2, default=str))
            return 0
        if a.launch:
            proc = _launch()
            print("[V7.2] V6.6正式雷达 + V7.2 Research Shadow 已启动。Portfolio/Research推送默认关闭。")
        return scheduler(proc)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                from v7.runtime_log_v72 import log_event
                log_event("scheduler", "终止子进程失败", exc=exc, degraded=True, event="shutdown")
        _unlock()


if __name__ == "__main__":
    raise SystemExit(main())
