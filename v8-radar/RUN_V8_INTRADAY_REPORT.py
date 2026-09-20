from __future__ import annotations
import json
from v8.env_loader import load_v8_env
load_v8_env()
from v8.intraday_report import build_intraday_report
if __name__=='__main__':print(json.dumps(build_intraday_report(),ensure_ascii=False,indent=2))
