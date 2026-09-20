from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOCK_FILE = ROOT / ".v71_sidecar.lock"
SCHEDULE_STATE = ROOT / ".v71_sidecar_schedule.json"
CN_TZ = ZoneInfo("Asia/Shanghai")
CHECKPOINTS = ("09:25", "09:35", "10:00", "10:10")


def _load_environment(test_mode: bool = False) -> None:
    from v7.env_loader import load_project_env
    load_project_env()
    os.environ.setdefault("V7_SHADOW_ENABLED", "true")
    os.environ.setdefault("V71_ENABLE", "true")
    if test_mode:
        os.environ["V71_PUSH_ENABLED"] = "false"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _acquire_singleton() -> bool:
    try:
        if LOCK_FILE.exists():
            try:
                old = int(LOCK_FILE.read_text(encoding="utf-8").strip() or "0")
            except Exception:
                old = 0
            if _pid_alive(old):
                return False
            try:
                LOCK_FILE.unlink()
            except Exception:
                return False
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def _release_singleton() -> None:
    try:
        if LOCK_FILE.exists() and LOCK_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            LOCK_FILE.unlink()
    except Exception:
        pass


def _read_schedule_state(day: str) -> dict:
    try:
        data = json.loads(SCHEDULE_STATE.read_text(encoding="utf-8"))
        if data.get("trade_date") == day:
            return data
    except Exception:
        pass
    return {"trade_date": day, "completed": []}


def _write_schedule_state(state: dict) -> None:
    try:
        tmp = SCHEDULE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(SCHEDULE_STATE)
    except Exception:
        pass


def _hhmm(value: str) -> dtime:
    h, m = (int(x) for x in value.split(":", 1))
    return dtime(h, m)


def due_checkpoints(now: datetime, completed: set[str] | None = None) -> list[str]:
    """Return due V7 state checks; used by the launcher and offline tests."""
    completed = set(completed or set())
    now = now.astimezone(CN_TZ)
    if now.weekday() >= 5 or now.time().replace(tzinfo=None) > dtime(10, 15):
        return []
    current = now.time().replace(tzinfo=None)
    return [cp for cp in CHECKPOINTS if cp not in completed and current >= _hhmm(cp)]


def _launch_main() -> subprocess.Popen:
    env = os.environ.copy()
    script = ROOT / "market_radar_V6_6_Pro.py"
    return subprocess.Popen([sys.executable, "-u", str(script)], cwd=str(ROOT), env=env)


def run_scheduler(*, main_process: subprocess.Popen | None = None, poll_seconds: int = 15) -> int:
    from v7.shadow_state_runtime import run_pending_cycle, trade_day_status
    while True:
        if main_process is not None and main_process.poll() is not None:
            return int(main_process.returncode or 0)
        now = datetime.now(CN_TZ)
        day = now.date().isoformat()
        state = _read_schedule_state(day)
        completed = set(state.get("completed") or [])
        due = due_checkpoints(now, completed)
        for cp in due:
            cal = trade_day_status(now)
            if cal.get("status") == "UNKNOWN":
                # Do not hit realtime APIs until the trading calendar is verified.
                print(f"[V7.1 Sidecar] {cp} 交易日状态未知，本轮跳过实时检查。")
                break
            if cal.get("status") == "CLOSED":
                print(f"[V7.1 Sidecar] {cp} 非交易日，不请求实时接口。")
                completed.add(cp)
                continue
            result = run_pending_cycle(now=now, trade_day_override="OPEN")
            print(f"[V7.1 Sidecar] {cp} 自动检查：{json.dumps(result, ensure_ascii=False, default=str)}")
            completed.add(cp)
        if due:
            state["completed"] = sorted(completed, key=lambda x: CHECKPOINTS.index(x) if x in CHECKPOINTS else 99)
            _write_schedule_state(state)
        time.sleep(max(5, poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(description="V7.1 Shadow Sidecar自动调度器")
    parser.add_argument("--launch", action="store_true", help="同时启动V6.6主雷达并自动运行Sidecar")
    parser.add_argument("--once", action="store_true", help="仅执行一次Sidecar检查，不启动主雷达")
    parser.add_argument("--test-mode", action="store_true", help="测试模式：强制关闭V7.1真实微信推送")
    args = parser.parse_args()

    _load_environment(test_mode=args.test_mode)
    if not _acquire_singleton():
        print("[V7.1 Sidecar] 已有实例运行，本次不重复启动。")
        return 0
    proc = None
    try:
        if args.once:
            from v7.shadow_state_runtime import run_pending_cycle
            print(json.dumps(run_pending_cycle(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.launch:
            proc = _launch_main()
            mode = "测试模式（V7推送关闭）" if args.test_mode else "Shadow模式（推送由V71_PUSH_ENABLED决定）"
            print(f"[V7.1 Sidecar] 主雷达已启动；{mode}。")
        return run_scheduler(main_process=proc)
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        _release_singleton()


if __name__ == "__main__":
    raise SystemExit(main())
