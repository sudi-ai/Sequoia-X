# -*- coding: utf-8 -*-
"""执行 V8.6.6 LiveData 全量验收并生成最终交付物。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from build_live_package import build

ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT.parents[2] / "outputs"
FINAL_JSON = ROOT / "LIVE_DATA_INTEGRATION_AUDIT.json"
FINAL_TXT = ROOT / "LIVE_DATA_INTEGRATION_AUDIT.txt"


def _run(name, args, timeout=300, extra_env=None):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if extra_env:
        env.update(extra_env)
    started = time.monotonic()
    try:
        process = subprocess.run(args, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        combined = (process.stdout + "\n" + process.stderr).strip()
        return {"name": name, "status": "PASS" if process.returncode == 0 else "FAIL", "return_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 2), "output_tail": "\n".join(combined.splitlines()[-12:])}
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "status": "FAIL", "return_code": None, "duration_seconds": round(time.monotonic() - started, 2), "output_tail": f"TIMEOUT: {exc}"}


def _write(report):
    text_lines = [
        "A股机会雷达 V8.6.6 LiveData 最终接入验收",
        f"生成时间: {report['generated_at']}",
        f"总体状态: {report['overall_status']}",
        "结论边界: 仅证明真实数据接入与降级机制；不声明胜率，不授权交易。",
        "",
        "测试:",
    ]
    for item in report["tests"]:
        text_lines.append(f"- {item['name']}: {item['status']} ({item['duration_seconds']}s)")
    provider = report.get("live_data", {})
    text_lines += [
        "",
        f"真实接口核心状态: {provider.get('overall_status', 'UNKNOWN')}",
        f"Token: {'已配置' if provider.get('token', {}).get('configured') else '未配置'}（密钥未写入审计或ZIP）",
        f"dailyfetch: {provider.get('mirror', {}).get('status', 'UNKNOWN')}",
        f"随机股票: {', '.join(provider.get('sampled_stocks', [])) or '无'}",
        "",
        "ZIP:",
        f"- 完整性: {report.get('zip', {}).get('integrity', 'PENDING')}",
        f"- 文件数: {report.get('zip', {}).get('entries', 0)}",
        "- secrets.local.json: 已排除",
        "",
        "统计安全门: Meta / CPCV / Bootstrap / Alpha Rank / Conformal / DSR / Monte Carlo 在真实配对样本不足时均显示样本不足。",
        "安全规则: 不自动买卖、不自动改参数、不自动晋级 Challenger、不自动开启 A+。",
    ]
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    text = "\n".join(text_lines)
    FINAL_JSON.write_text(payload, encoding="utf-8")
    FINAL_TXT.write_text(text, encoding="utf-8")
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    (OUTPUTS / FINAL_JSON.name).write_text(payload, encoding="utf-8")
    (OUTPUTS / FINAL_TXT.name).write_text(text, encoding="utf-8")


def main():
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    isolated_temp = Path(tempfile.mkdtemp(prefix="radar_v866_accept_"))
    env = {"TEMP": str(isolated_temp), "TMP": str(isolated_temp)}
    tests = [
        _run("Python 全文件编译", [sys.executable, "-m", "compileall", "-q", "-x", r"[\\/](?:\.venv|__pycache__)[\\/]", "."], extra_env=env),
        _run("全量 self_test", [sys.executable, "self_test.py"], extra_env=env),
        _run("Token/权限/超时/镜像/字段变化/模式离线测试", [sys.executable, "live_data_integration_test.py"], extra_env=env),
        _run("所有页面与股票详情 HTTP 200", [sys.executable, "http_route_test.py"], extra_env=env),
        _run("真实付费接口探测", [sys.executable, "live_data_verifier.py"], timeout=420, extra_env=env),
    ]
    try:
        shutil.rmtree(isolated_temp)
    except OSError:
        pass
    try:
        live = json.loads((ROOT / "LIVE_DATA_AUDIT.json").read_text(encoding="utf-8"))
    except Exception as exc:
        live = {"overall_status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
    core = live.get("interfaces", {})
    core_ready = live.get("token", {}).get("configured") and live.get("mirror", {}).get("status") == "OK" and core.get("trade_cal", {}).get("status") == "OK" and any(core.get(name, {}).get("status") == "OK" for name in ("daily", "pro_bar"))
    report = {
        "version": "V8.6.6 LiveData",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall_status": "PASS" if all(item["status"] == "PASS" for item in tests) and core_ready else "PARTIAL",
        "claim_scope": "真实数据接入、标准化、缓存、PIT、降级和页面路由；不包含胜率结论",
        "tests": tests,
        "live_data": live,
        "zip": {"integrity": "PENDING"},
    }
    _write(report)
    zip_path = OUTPUTS / "AStock_Radar_V8_6_6_LiveData.zip"
    package = build(zip_path)
    with tempfile.TemporaryDirectory(prefix="radar_zip_extract_") as folder:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(folder)
        extracted = Path(folder) / "AStock_Radar_V8_6_6_LiveData"
        assert (extracted / "browser_dashboard.py").is_file()
        assert (extracted / "VERIFY_LIVE_DATA.bat").is_file()
        assert not (extracted / "secrets.local.json").exists()
    package["extract_test"] = "PASS"
    report["zip"] = package
    _write(report)
    package = build(zip_path)
    report["zip"].update(package)
    _write(report)
    print(json.dumps({"overall_status": report["overall_status"], "tests": {item["name"]: item["status"] for item in tests}, "live_data": live.get("overall_status"), "zip": report["zip"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
