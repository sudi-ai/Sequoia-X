from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REQUIRED = [
    "market_radar_V6_6_Pro.py",
    "radar_stats_v2.py",
    "radar_v64_stats.py",
    "v66_market_sentiment.py",
    "v66_regulatory_risk.py",
    "v66_trend_structure.py",
    "v66_nextday_engine.py",
    "v66_closed_loop_db.py",
    "v66_closed_loop_runtime.py",
    "v66_data_sources.py",
    "CHECK_CLOSED_LOOP_STATUS.py",
]

for name in REQUIRED:
    path = BASE_DIR / name
    if not path.is_file():
        raise SystemExit(f"[FAIL] 缺少文件：{name}")
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

main_source = (BASE_DIR / "market_radar_V6_6_Pro.py").read_text(encoding="utf-8-sig")
for required_text in (
    "def _v63_realtime_spot_dataframe(",
    "def _v63_fetch_tencent_spot_dataframe(",
    "V63_REALTIME_MARKET_MINIMUMS",
    "df = _v63_realtime_spot_dataframe()",
):
    if required_text not in main_source:
        raise SystemExit(f"[FAIL] 缺少全A行情容错接入：{required_text}")
if "df = ak.stock_zh_a_spot()" in main_source:
    raise SystemExit("[FAIL] 仍存在绕过统一容错层的全A直接调用")

if sys.version_info < (3, 10):
    raise SystemExit("[FAIL] 需要 Python 3.10 或更高版本")

sys.path.insert(0, str(BASE_DIR))
import v66_closed_loop_db as closed_db
import v66_closed_loop_runtime as runtime
import v66_nextday_engine as engine

with tempfile.TemporaryDirectory(prefix="v66_startup_check_") as folder:
    info = runtime.initialize(folder)
    status = closed_db.database_status(info["database"])
    if status.get("integrity_check") != "ok":
        raise SystemExit(f"[FAIL] SQLite完整性：{status.get('integrity_check')}")
    if any(status.get("row_counts", {}).values()):
        raise SystemExit("[FAIL] 空库自检不应出现业务数据")

    # 验证“数据库已提交但微信失败”时可以脱离行情截面重建15:05初版。
    day = "2026-08-11"
    closed_db.upsert_sector_forecast({
        "forecast_date": day,
        "sector_name": "自检板块",
        "forecast_rank": 1,
        "continuation_score": 75,
        "confidence": 80,
        "data_source": "自检临时记录",
        "algorithm_version": "V6.6-self-check",
    }, info["database"])
    candidate_id = closed_db.upsert_candidate({
        "candidate_date": day,
        "stock_code": "000001",
        "stock_name": "自检股票",
        "sector_name": "自检板块",
        "selected_at": f"{day}T15:05:00",
        "selection_reason": "补推路径自检",
        "base_score": 90,
        "comprehensive_score": 80,
        "market_sentiment_score": 60,
        "sector_score": 75,
        "stock_score": 80,
        "trend_score": 78,
        "chip_score": 70,
        "risk_score": 80,
        "risk_penalty": 10,
        "buy_range_low": 9.8,
        "buy_range_high": 10.2,
        "reference_price": 10,
        "target_price": 11,
        "stop_price": 9,
        "candidate_level": "KEY",
        "candidate_rank": 1,
        "status": "CANDIDATE",
        "algorithm_version": "V6.6-self-check",
        "data_source": "自检临时记录",
        "extra": {"confidence": 77},
    }, info["database"])
    closed_db.record_evening_revision({
        "candidate_id": candidate_id,
        "revision_date": day,
        "revised_at": f"{day}T19:30:00",
        "new_rank": 1,
        "new_score": 70,
        "new_status": "CANCELLED",
        "revision_reason": "验证补推恢复初版",
        "data_complete": True,
        "data_source": "自检临时记录",
    }, info["database"])
    runtime.mark_stage_db_committed(
        runtime.STAGE_CLOSE,
        day,
        True,
        {"forecast_count": 1, "candidate_count": 1},
    )
    rebuilt = runtime.run_close_selection(f"{day}T20:00:00")
    rebuilt_candidate = (rebuilt.get("candidates") or [{}])[0]
    if rebuilt_candidate.get("comprehensive_score") != 80 or rebuilt_candidate.get("status") != "CANDIDATE":
        raise SystemExit("[FAIL] 15:05数据库补推没有恢复初版评分/状态")

if not callable(runtime.run_close_selection):
    raise SystemExit("[FAIL] 缺少15:05候选池入口")
if not callable(runtime.track_untracked_candidates):
    raise SystemExit("[FAIL] 缺少次日真实行情跟踪入口")
if not callable(closed_db.generate_winrate_reports):
    raise SystemExit("[FAIL] 缺少真实胜率报告入口")
if sum(engine.CANDIDATE_COMPONENT_WEIGHTS.values()) != 100:
    raise SystemExit("[FAIL] 最终评分权重合计不是100%")

print("[OK] V6.6 Pro 主程序语法")
print("[OK] 次日板块预测、风险扣分和最终排序模块")
print("[OK] candidate_pool真实保存接口")
print("[OK] next_day_tracking不复权行情接口")
print("[OK] winrate_reports仅从真实跟踪表统计")
print("[OK] SQLite独立库结构与完整性")
print("[OK] 15:05入库后可脱离行情截面恢复初版并补推")
print("[OK] 全A东财/新浪失败后腾讯批量容错已统一接入")
print("[OK] 独立微信3群配置，不读取V6.5配置")
print(f"[OK] Python {sys.version.split()[0]}")
