from v8.env_loader import load_v8_env


load_v8_env()

from v8.push import send_once


MESSAGE = """🟢 A股机会雷达 V8｜中文编码修复成功

✅ V8 Research Shadow 已独立启动
✅ 新版微信群推送通道正常
✅ 持仓监测与机会扫描正在运行
🔒 V7.2 正在运行的版本未改动

当前为非交易时段，系统低频待机；交易时段自动扫描。
仅用于研究验证，不构成投资建议。"""


if __name__ == "__main__":
    print(send_once("V8_UTF8_ACCEPTANCE_20260817_001", MESSAGE))
