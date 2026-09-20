"""V8唯一的Tushare初始化入口。

Token从私密环境文件读取，不写入源码。底层统一执行：
    pro = ts.pro_api(token)
    pro._DataApi__http_url = "https://tt.dailyfetch.top/"
所有pro_bar调用统一由bridge.pro_bar完成，内部固定传入api=pro。
"""
from __future__ import annotations

from typing import Any

from v7.tushare_bridge import BRIDGE


def get_pro():
    return BRIDGE.pro


def call(api_name:str,**kwargs)->Any:
    return BRIDGE.call(api_name,**kwargs)


def pro_bar(**kwargs)->Any:
    return BRIDGE.pro_bar(**kwargs)


def health()->dict[str,Any]:
    return BRIDGE.health()
