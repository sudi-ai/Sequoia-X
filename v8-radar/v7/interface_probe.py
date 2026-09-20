from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .tushare_bridge import BRIDGE

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT_DIR / "reports_v7"


def _default_probes():
    now = datetime.now()
    day = now.strftime("%Y%m%d")
    start = (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    end = now.strftime("%Y-%m-%d %H:%M:%S")
    return [
        ("index_basic", {"limit": 5}, "基础接口"),
        ("daily", {"trade_date": day}, "A股日线/收盘结算"),
        ("rt_k", {"ts_code": "000001.SZ"}, "A股实时日线/最新行情"),
        ("rt_min", {"ts_code": "000001.SZ", "freq": "1MIN"}, "A股实时1分钟"),
        ("rt_min_daily", {"ts_code": "000001.SZ", "freq": "1MIN"}, "A股当日实时分钟"),
        ("stk_auction_o", {"trade_date": day}, "开盘集合竞价盘后汇总（不是09:15-09:25过程）"),
        ("stk_auction_tick", {"ts_code": "000001.SZ"}, "09:15-09:25实时竞价过程能力（需验证字段和时间）"),
        ("anns_d", {"ann_date": day}, "上市公司公告"),
        ("news", {"src": "sina", "start_date": start, "end_date": end}, "新闻快讯"),
        ("major_news", {"src": "sina", "start_date": start, "end_date": end}, "重大新闻"),
        ("report_rc", {"report_date": day}, "券商盈利预测/研报"),
        ("us_tbr", {"start_date": (now-timedelta(days=10)).strftime("%Y%m%d"), "end_date": day}, "美债短期利率/宏观标签"),
        ("cyq_perf", {"ts_code": "000001.SZ", "start_date": (now-timedelta(days=30)).strftime("%Y%m%d"), "end_date": day}, "筹码胜率/平均成本"),
        ("cyq_chips", {"ts_code": "000001.SZ", "trade_date": day}, "筹码分布"),
        ("daily_basic", {"ts_code": "000001.SZ", "start_date": (now-timedelta(days=10)).strftime("%Y%m%d"), "end_date": day}, "每日基本指标"),
        ("moneyflow", {"ts_code": "000001.SZ", "start_date": (now-timedelta(days=10)).strftime("%Y%m%d"), "end_date": day}, "个股资金流"),
        ("forecast", {"ts_code": "000001.SZ", "start_date": (now-timedelta(days=365)).strftime("%Y%m%d"), "end_date": day}, "业绩预告"),
    ]


def _df_summary(value: Any) -> dict[str, Any]:
    try:
        columns = [str(c) for c in value.columns]
        rows = int(len(value))
        sample = value.head(2).to_dict(orient="records")
        return {"rows": rows, "columns": columns, "sample": sample}
    except Exception:
        return {"type": type(value).__name__, "repr": repr(value)[:500]}


def run_probe(extra: list[tuple[str, dict[str, Any], str]] | None = None) -> dict[str, Any]:
    result = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "bridge": BRIDGE.health(),
        "notes": [
            "stk_auction_o 只按开盘竞价汇总使用，绝不作为09:15-09:25实时竞价过程。",
            "若 stk_auction_tick 成功，仍必须检查股票代码、Tick时间、当日日期、行数和字段完整性后才允许启用严格竞价确认。",
            "历史分钟通过 pro_bar(api=pro, freq='1min') 验证；实时分钟通过 rt_min/rt_min_daily 验证。",
            "hk_daily/us_daily 不在V7.2正式接口范围。",
        ],
        "tests": [],
    }
    probes = _default_probes() + list(extra or [])
    for api, params, label in probes:
        row = {"api": api, "label": label, "params": params, "ok": False}
        try:
            value = BRIDGE.call(api, **params)
            row.update({"ok": True, "result": _df_summary(value)})
        except Exception as exc:
            row["error"] = repr(exc)
        result["tests"].append(row)

    now = datetime.now()
    p = {
        "api": "pro_bar", "label": "历史1分钟特殊调用",
        "params": {"ts_code": "000001.SZ", "freq": "1min", "start_date": (now-timedelta(days=7)).strftime("%Y-%m-%d 09:00:00"), "end_date": now.strftime("%Y-%m-%d 17:00:00")},
        "ok": False,
    }
    try:
        value = BRIDGE.pro_bar(**p["params"])
        p.update({"ok": True, "result": _df_summary(value)})
    except Exception as exc:
        p["error"] = repr(exc)
    result["tests"].append(p)
    result["bridge_after"] = BRIDGE.health()
    return result


def main():
    parser = argparse.ArgumentParser(description="V7 Tushare relay capability probe")
    parser.add_argument("--api", action="append", default=[], help="Extra API name to probe")
    parser.add_argument("--params", default="{}", help="JSON params applied to each extra API")
    args = parser.parse_args()
    try:
        params = json.loads(args.params)
    except Exception:
        params = {}
    extra = [(name, params, "用户指定接口") for name in args.api]
    result = run_probe(extra)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"Tushare接口探测_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    txt_path = path.with_suffix(".txt")
    lines = ["A股机会雷达 V7｜购买数据接口测试摘要", f"测试时间：{result['time']}", ""]
    for item in result["tests"]:
        status = "成功" if item.get("ok") else "失败"
        rows = item.get("result", {}).get("rows", "-") if item.get("ok") else "-"
        lines.append(f"[{status}] {item.get('label')} | API={item.get('api')} | rows={rows}")
        if not item.get("ok"):
            lines.append(f"  错误：{item.get('error','')[:300]}")
    lines += ["", "说明：stk_auction_o 若成功，只代表开盘竞价汇总能力；09:15-09:25实时竞价过程仍需单独确认。"]
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"\nJSON报告已保存：{path}")
    print(f"摘要已保存：{txt_path}")


if __name__ == "__main__":
    main()
