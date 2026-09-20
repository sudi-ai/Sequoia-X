# -*- coding: utf-8 -*-
"""V8.6.6 工作台全页面 HTTP 200 验收。"""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from browser_dashboard import H, PAGE_NAV


def run():
    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    checked = []
    try:
        for mode in ("DEMO", "LIVE", "PIT"):
            for page, _ in PAGE_NAV:
                with urlopen(f"{base}/?page={page}&mode={mode}", timeout=10) as response:
                    assert response.status == 200
                    body = response.read().decode("utf-8")
                    assert "A股机会雷达 V8.6.6" in body
                    if page == "sources":
                        assert "付费接口探测明细" in body
                checked.append(f"{mode}:{page}")
        routes = ("/api", "/api/health", "/api/data-sources", "/api/page?name=a_exec&mode=DEMO", "/api/stock?code=300308.SZ&mode=DEMO")
        for route in routes:
            with urlopen(base + route, timeout=10) as response:
                assert response.status == 200
                json.loads(response.read().decode("utf-8"))
            checked.append(route)
        with urlopen(base + "/?page=stock_detail&code=300308.SZ&mode=DEMO", timeout=10) as response:
            assert response.status == 200
        checked.append("stock_detail")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print(json.dumps({"http_200": len(checked), "routes": checked}, ensure_ascii=False, indent=2))
    print("ALL HTTP ROUTE TESTS PASSED")
    return checked


if __name__ == "__main__":
    run()
