# -*- coding: utf-8 -*-
"""V8.6 工作台演示数据，不代表真实胜率。"""
def demo_summary():
    return {
      "meta":{"role":"Challenger","prob":"88%","calibrated":"86%","approved":False},
      "conformal":{"set":"{1}","alpha":"10%","status":"Singleton Positive"},
      "selective":{"decision":"REFERENCE","reason":"Meta尚未批准，不改变实盘"},
      "cpcv":{"precision":"74%","recall":"51%","brier":"0.168","note":"演示值"},
      "uniqueness":{"raw":126,"effective":"87"},
      "dsr":{"prob":"0.71","status":"PASS"},
      "sniper":{"grade":"A","note":"批准前不生成实盘A+"},
    }
