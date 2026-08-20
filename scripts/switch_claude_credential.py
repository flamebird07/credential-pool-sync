#!/usr/bin/env python3
"""Manually switch the active Claude Code credential in the running proxy.

Send a POST to the proxy's /switch control endpoint.  This is deliberately a
separate control-plane path from the request-forwarding path, so a manual
switch is never confused with an upstream Claude Code request.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.getenv("CLAUDE_POOL_PROXY_PORT", "21435"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually switch the active Claude Code credential")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument(
        "--next",
        action="store_true",
        help="switch to the next credential even if the current one is healthy (default behavior when no flag is passed)",
    )
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}/switch"
    control_token = os.getenv("CLAUDE_POOL_CONTROL_TOKEN", "").strip()
    if not control_token:
        print("切换失败：未设置 CLAUDE_POOL_CONTROL_TOKEN", file=sys.stderr)
        return 2
    request = Request(
        url,
        data=b"",
        headers={"Authorization": f"Bearer {control_token}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except URLError as error:
        print(f"切换失败：无法连接代理 {url}（{error}）", file=sys.stderr)
        return 1
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"切换失败：代理返回了无效响应（{error}）", file=sys.stderr)
        return 1

    if result.get("ok"):
        print(f"已切换：{result.get('from') or '上一个'} -> {result.get('to')}；当前模型：{result.get('model') or '未填写'}")
        return 0
    print(f"切换失败：{result.get('error', '无可用下一档凭证')}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())