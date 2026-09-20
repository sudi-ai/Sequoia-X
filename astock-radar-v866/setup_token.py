# -*- coding: utf-8 -*-
from pathlib import Path
import getpass, json
p=Path(__file__).with_name("secrets.local.json")
token=getpass.getpass("请输入付费接口 Token（输入过程不显示）：").strip()
if not token: raise SystemExit("Token为空，取消。")
p.write_text(json.dumps({"tushare_token":token},ensure_ascii=False,indent=2),encoding="utf-8")
print("Token 已仅保存到本机：", p)
