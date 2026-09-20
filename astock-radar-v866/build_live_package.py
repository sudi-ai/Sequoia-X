# -*- coding: utf-8 -*-
"""构建不含本机 Token、缓存和数据库的 V8.6.6 LiveData 完整包。"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXCLUDED_PARTS = {".venv", "__pycache__", "data", ".git"}
EXCLUDED_NAMES = {"secrets.local.json", "test.zip"}


def build(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_NAMES or path.suffix.lower() in (".zip", ".pyc", ".db", ".log"):
                continue
            archive.write(path, Path("AStock_Radar_V8_6_6_LiveData") / relative)
    with zipfile.ZipFile(output) as archive:
        bad = archive.testzip()
        names = archive.namelist()
        assert bad is None, bad
        assert not any(name.endswith("secrets.local.json") for name in names)
        assert any(name.endswith("VERIFY_LIVE_DATA.bat") for name in names)
    return {"path": str(output), "entries": len(names), "size_bytes": output.stat().st_size, "integrity": "PASS", "secret_excluded": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT.parent / "AStock_Radar_V8_6_6_LiveData.zip"))
    args = parser.parse_args()
    print(build(args.output))


if __name__ == "__main__":
    main()
