from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get(
    "V8_MIGRATION_SOURCE",
    r"E:\A股机会雷达_V7.2_Research_Shadow_融合运行版\.env.v7",
))
TARGET = ROOT / ".env.v8"
ALLOWED = {"TUSHARE_TOKEN", "TUSHARE_HTTP_URL"}


def parse(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def main() -> int:
    if not SOURCE.exists():
        raise FileNotFoundError("V7 paid configuration source does not exist")
    target_lines, current = parse(TARGET)
    _, source_values = parse(SOURCE)
    missing = [key for key in sorted(ALLOWED) if not source_values.get(key)]
    if missing:
        raise RuntimeError("Missing paid configuration keys: " + ",".join(missing))
    output = []
    replaced: set[str] = set()
    for raw in target_lines:
        stripped = raw.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key in ALLOWED:
            output.append(f"{key}={source_values[key]}")
            replaced.add(key)
        else:
            output.append(raw)
    for key in sorted(ALLOWED - replaced):
        output.append(f"{key}={source_values[key]}")
    temporary = TARGET.with_suffix(TARGET.suffix + ".tmp")
    temporary.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    os.replace(temporary, TARGET)
    # Never print values.
    print({"status": "OK", "target": TARGET.name,
           "migrated_keys": sorted(ALLOWED), "values_exposed": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
