from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import v66_closed_loop_db as db


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data_v66" / "v66_closed_loop.sqlite3"
JOB_STATE_PATH = BASE_DIR / "data_v66" / "v66_closed_loop_job_state.json"


def scalar(connection: sqlite3.Connection, sql: str):
    row = connection.execute(sql).fetchone()
    return row[0] if row else None


db.initialize_database(DB_PATH)
status = db.database_status(DB_PATH)
print("=" * 72)
print("A股机会雷达 V6.6 Pro｜次日闭环数据状态")
print("数据库：", status["database"])
print("完整性：", status["integrity_check"], "｜Schema：", status["schema_version"])
print("-" * 72)
for table, count in status["row_counts"].items():
    print(f"{table:24s} {count:>8d}")

with sqlite3.connect(DB_PATH) as connection:
    latest_close = scalar(connection, "SELECT MAX(forecast_date) FROM sector_forecasts")
    latest_candidate = scalar(connection, "SELECT MAX(candidate_date) FROM candidate_pool")
    latest_tracking = scalar(connection, "SELECT MAX(trade_date) FROM next_day_tracking WHERE actual_data=1")
    latest_report = scalar(connection, "SELECT MAX(report_as_of) FROM winrate_reports")
    outcome_rows = connection.execute(
        """SELECT outcome, COUNT(*) FROM next_day_tracking
           WHERE actual_data=1 GROUP BY outcome ORDER BY outcome"""
    ).fetchall()
    source_rows = connection.execute(
        """SELECT market_data_source, COUNT(*) FROM next_day_tracking
           WHERE actual_data=1 GROUP BY market_data_source ORDER BY COUNT(*) DESC"""
    ).fetchall()

print("-" * 72)
print("最近15:05收盘闭环：", latest_close or "暂无（空库不代表任务已运行）")
print("最近非空候选日期：", latest_candidate or "暂无（可能尚未运行，也可能当日零合格候选）")
print("最近真实跟踪：", latest_tracking or "暂无")
print("最近胜率报告：", latest_report or "暂无")

if JOB_STATE_PATH.exists():
    try:
        state = json.loads(JOB_STATE_PATH.read_text(encoding="utf-8"))
        jobs = list((state.get("jobs") or {}).values())
        latest_by_stage = {}
        for job in jobs:
            stage = str(job.get("stage") or "-")
            key = (str(job.get("business_date") or ""), str(job.get("updated_at") or ""))
            if stage not in latest_by_stage or key > latest_by_stage[stage][0]:
                latest_by_stage[stage] = (key, job)
        print("-" * 72)
        print("最近阶段状态（数据库提交/微信发送）：")
        for stage in sorted(latest_by_stage):
            job = latest_by_stage[stage][1]
            details = job.get("db_details") or {}
            candidate_count = details.get("candidate_count")
            count_text = f"｜候选 {candidate_count}" if candidate_count is not None else ""
            print(
                f"{stage:20s} {job.get('business_date', '-')}｜DB {bool(job.get('db_committed'))}"
                f"｜微信 {bool(job.get('push_sent'))}{count_text}"
            )
    except (OSError, ValueError, TypeError) as exc:
        print("任务状态文件读取失败：", repr(exc))
else:
    print("任务状态：暂无（仅创建空数据库不代表闭环已执行）。")

print("-" * 72)
print("真实次日结果：", "｜".join(f"{name} {count}" for name, count in outcome_rows) or "暂无")
print("真实行情来源：", "｜".join(f"{name} {count}" for name, count in source_rows) or "暂无")

reports = db.get_winrate_reports(db_path=DB_PATH, dimension="OVERALL")
if reports:
    row = reports[0]
    rate = row.get("win_rate_pct")
    print(
        "总体真实胜率：",
        f"{float(rate):.2f}%" if rate is not None else "明确胜负样本不足",
        f"｜实际样本 {row.get('sample_count', 0)}",
        f"｜明确胜负 {row.get('decisive_count', 0)}",
    )
else:
    print("总体真实胜率：暂无真实次日跟踪记录，系统不会生成模拟胜率。")
print("=" * 72)
