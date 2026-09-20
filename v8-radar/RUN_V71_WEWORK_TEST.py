from __future__ import annotations

import argparse
from v7.env_loader import load_project_env


def main() -> int:
    parser = argparse.ArgumentParser(description="V7.1新群推送通道测试")
    parser.add_argument("--send", action="store_true", help="显式发送一条到V7.1新群；默认仅Mock测试，不发送真实消息")
    args = parser.parse_args()
    load_project_env()
    from v7.wework_shadow_push import send_message

    text = "【V7.1 Shadow推送测试】\n仅测试V7.1新群通道，不调用V6.6原群。"
    if not args.send:
        def fake(url, payload, timeout):
            return True, "mock_ok"
        ok, detail = send_message(text, webhook="https://example.invalid/v71-shadow-redacted", post_func=fake)
        print("MOCK_OK" if ok else f"MOCK_FAILED: {detail}")
        return 0 if ok else 1

    ok, detail = send_message(text)
    print("SEND_OK" if ok else f"SEND_FAILED: {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
