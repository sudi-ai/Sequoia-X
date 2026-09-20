# -*- coding: utf-8 -*-
"""Tushare 的唯一初始化入口。

项目内其他文件只能从本模块导入 ``get_pro`` 或 ``pro_bar``。真实 Token
优先从 secrets.local.json 读取，其次才读取 TUSHARE_TOKEN 环境变量。
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

HTTP_URL = "https://tt.dailyfetch.top/"
TOKEN_ENV = "TUSHARE_TOKEN"
SECRET_FILE = Path(__file__).with_name("secrets.local.json")


def load_token(secret_file=SECRET_FILE, environ=None, required=True):
    env = os.environ if environ is None else environ
    path = Path(secret_file)
    token = ""
    source = ""
    error = ""
    if path.exists():
        try:
            token = str(json.loads(path.read_text(encoding="utf-8")).get("tushare_token", "")).strip()
            if token:
                source = "secrets.local.json"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    if not token:
        token = str(env.get(TOKEN_ENV, "")).strip()
        if token:
            source = "environment"
    if required and not token:
        suffix = f"（密钥文件错误：{error}）" if error else ""
        raise RuntimeError("未配置 Tushare Token：请先运行 python setup_token.py" + suffix)
    return token, source, error


def token_status(secret_file=SECRET_FILE, environ=None):
    token, source, error = load_token(secret_file, environ, required=False)
    return {
        "configured": bool(token),
        "source": source or "none",
        "masked": (token[:4] + "***" + token[-4:]) if len(token) >= 10 else ("***" if token else ""),
        "error": error,
    }


@lru_cache(maxsize=1)
def get_pro():
    import tushare as ts

    token, _, _ = load_token(required=True)
    pro = ts.pro_api(token)
    pro._DataApi__http_url = HTTP_URL
    return pro


def pro_bar(**kwargs):
    import tushare as ts

    return ts.pro_bar(api=get_pro(), **kwargs)


def reset_client_cache():
    """仅供配置变更和离线测试使用。"""
    get_pro.cache_clear()


def smoke_test():
    pro = get_pro()
    print(pro.index_basic(limit=5))
    print(pro_bar(ts_code="000001.SZ", limit=3))


if __name__ == "__main__":
    smoke_test()
