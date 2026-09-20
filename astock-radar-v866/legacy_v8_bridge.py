# -*- coding: utf-8 -*-
from __future__ import annotations
import importlib, os, sys
from pathlib import Path

def load_legacy():
    module=os.getenv('V8_LEGACY_MODULE','').strip()
    if not module:return None
    d=Path(__file__).with_name('legacy_v8')
    if d.exists():sys.path.insert(0,str(d))
    try:return importlib.import_module(module)
    except Exception:return None

def legacy_snapshot(mod):
    if mod is None:return None
    for name in ('snapshot','get_state'):
        fn=getattr(mod,name,None)
        if callable(fn):
            try:return fn()
            except Exception:return None
    return None
