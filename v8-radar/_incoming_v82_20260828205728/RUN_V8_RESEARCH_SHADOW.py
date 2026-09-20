from __future__ import annotations

import argparse
import os
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK = ROOT / ".v8_research_shadow.lock"


def configure() -> None:
    from v8.env_loader import load_v8_env
    load_v8_env()
    os.environ.setdefault("V8_PUSH_ENABLED", "false")
    os.environ["V8_AUTO_ORDER_ENABLED"] = "false"
    # Hard boundary: V8 never starts or routes V6/V7 signal streams.
    os.environ["V66_ORIGINAL_PUSH_ENABLED"] = "false"
    os.environ["V7_SHADOW_ENABLED"] = "false"
    os.environ["V71_PUSH_ENABLED"] = "false"
    os.environ["V71_UNIFIED_MODE"] = "false"
    os.environ["V71_ROUTE_V66_MESSAGES"] = "false"
    os.environ["V72_PORTFOLIO_PUSH"] = "false"
    os.environ["V72_CATALYST_PUSH"] = "false"
    os.environ["V7_TUSHARE_ENABLED"] = "true"
    os.environ["V7_PAID_PROVIDER_ENABLED"] = "true"
    os.environ["V72_REALTIME_AUCTION_TICK_ENABLED"] = "true"


def acquire() -> bool:
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().strip())
            os.kill(pid, 0)
            return False
        except Exception:
            LOCK.unlink(missing_ok=True)
    fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launch-discovery", action="store_true")
    parser.add_argument("--portfolio", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    configure()
    from v8.config import CONFIG
    from v8.storage import initialize
    if args.self_check:
        print({"version": "V8.2 Entry Quality Research Shadow", "db": str(initialize()),
               "push": CONFIG.push_enabled, "auto_order": CONFIG.auto_order_enabled,
               "independent_discovery": True})
        return 0
    if not acquire():
        print("[V8] 已有独立实例运行")
        return 0
    discovery_runtime = None
    paid_archive_service = None
    last_settlement_day = None
    last_market_audit_day = None
    try:
        initialize()
        from v8.forward_runtime import backfill_decision_outcomes
        print("[V8] 历史确认信号结果任务补齐：",backfill_decision_outcomes())
        if args.portfolio:
            def portfolio_loop() -> None:
                from v8.portfolio_monitor import scan_all
                while True:
                    now = datetime.now().astimezone()
                    clock = now.hour * 60 + now.minute
                    if now.weekday() < 5 and (564 <= clock <= 690 or 780 <= clock <= 900):
                        try:
                            scan_all(now)
                        except Exception as exc:
                            from v8.runtime_log import log_exception
                            log_exception("portfolio_loop", exc)
                    time.sleep(CONFIG.holding_scan_seconds)
            threading.Thread(target=portfolio_loop, name="V8PortfolioMonitor", daemon=True).start()
        if args.launch_discovery:
            from v8.independent_discovery import RUNTIME
            discovery_runtime = RUNTIME
            threading.Thread(target=RUNTIME.run_forever, name="V8IndependentDiscovery", daemon=True).start()
        if CONFIG.paid_archive_enable:
            from v8.paid_month_archive import SERVICE
            paid_archive_service = SERVICE
            threading.Thread(target=SERVICE.run_forever, name="V8PaidMonthArchive", daemon=True).start()
        print(f"[V8] 独立版已启动；独立扫描={args.launch_discovery}；持仓监测={args.portfolio}；"
              f"推送={CONFIG.push_enabled}；自动下单永久关闭。")
        while True:
            now = datetime.now().astimezone()
            try:
                from v8.notifications import run_scheduled_notifications
                run_scheduled_notifications(now)
            except Exception as exc:
                from v8.runtime_log import log_exception
                log_exception("scheduled_notifications", exc)
            try:
                from v8.event_radar import proactive_issue_audit, run_event_schedule
                run_event_schedule(now)
                if now.weekday() < 5 and (now.hour, now.minute) >= (21, 40):
                    proactive_issue_audit(now)
            except Exception as exc:
                from v8.runtime_log import log_exception
                log_exception("event_radar_schedule", exc)
            if now.weekday() < 5 and (now.hour, now.minute) >= (15, 20) and last_settlement_day != now.date():
                try:
                    from v8.forward_runtime import (settle_event_forward,settle_event_intraday,
                                                     settle_pending,settle_decision_intraday)
                    cross_day=settle_event_forward(now)
                    intraday=settle_event_intraday(now)
                    print("[V8] Forward结算：", settle_pending(now), "独立信号盘中：",settle_decision_intraday(now),
                          "事件跨日Forward：", cross_day,"事件盘中Forward：", intraday)
                    last_settlement_day = now.date()
                except Exception as exc:
                    from v8.runtime_log import log_exception
                    log_exception("forward_settlement", exc)
            if now.weekday() < 5 and (now.hour, now.minute) >= (15, 20) and last_market_audit_day != now.date():
                try:
                    from v8.market_audit_runtime import settle_daily_market_audit
                    print("[V8] 漏选审计：", settle_daily_market_audit(trade_date=now.date().isoformat()))
                    last_market_audit_day = now.date()
                except Exception as exc:
                    from v8.runtime_log import log_exception
                    log_exception("market_miss_audit", exc)
            time.sleep(10)
    except KeyboardInterrupt:
        return 0
    finally:
        if discovery_runtime is not None:
            discovery_runtime.stop()
        if paid_archive_service is not None:
            paid_archive_service.stop()
        try:
            LOCK.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
