from __future__ import annotations
import json
from v7.reporting_v72 import build_evening_review
if __name__=='__main__': print(json.dumps(build_evening_review(),ensure_ascii=False,indent=2,default=str))
