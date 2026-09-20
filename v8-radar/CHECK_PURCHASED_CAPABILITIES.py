from __future__ import annotations
import json
from v7.env_loader import load_env_file
load_env_file()
from v7.api_health import run_health_check

if __name__ == "__main__":
    result = run_health_check(force=True, save=True)
    safe = {"checked_at": result.get("checked_at"), "status": result.get("status"), "apis": result.get("apis", [])}
    print(json.dumps(safe, ensure_ascii=False, indent=2))
