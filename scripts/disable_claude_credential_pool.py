#!/usr/bin/env python3
"""One-command disablement: the symmetric reverse of enable_claude_credential_pool.py.

Restores Claude settings, removes the auto-start entry, stops the proxy, and
resets the Feishu table statuses that the proxy wrote, so the dashboard no
longer claims the proxy is connected after it has been disabled.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from claude_credential_proxy import CONFIG_PATH, FEISHU_API, feishu_token, hermes_config

DEFAULT_PORT = int(os.getenv("CLAUDE_POOL_PROXY_PORT", "21435"))
# Statuses written by the proxy; anything else is left untouched.
PROXY_STATUSES = {"🔄 Claude Code 使用中", "⚠️ 额度耗尽", "⛔ 限流", "❌ 无效", "❌ Claude 不兼容", "⚠️ 不可用", "✅ 正常"}


def bitable(token: str, path: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(f"{FEISHU_API}/bitable/v1{path}", data=data, headers=headers, method=method)
    with urlopen(request, timeout=20) as response:
        result = json.loads(response.read())
    if result.get("code") != 0:
        raise RuntimeError(result.get("msg") or "飞书接口请求失败")
    return result.get("data") or {}


def restore_claude_settings() -> None:
    target = Path.home() / ".claude" / "settings.json"
    if not target.exists():
        print("未发现 Claude 设置文件，跳过还原。")
        return
    backups = sorted(glob.glob(str(target.with_name("settings.json.before-claude-pool-*.bak"))))
    if backups:
        shutil.copy2(backups[-1], target)
        print(f"已从备份整文件还原 Claude 设置：{backups[-1]}")
        return
    settings = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise RuntimeError("Claude Code 设置文件格式不正确")
    env = settings.get("env")
    changed = False
    if isinstance(env, dict):
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            if key in env:
                del env[key]
                changed = True
    if not changed:
        print("Claude 设置中未发现代理环境变量，无需修改。")
        return
    shutil.copy2(target, target.with_name(f"settings.json.before-claude-pool-disable-{datetime.now():%Y%m%d%H%M%S}.bak"))
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, target)
    print("已从 Claude 设置移除代理环境变量。")


def remove_startup() -> None:
    startup = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs/Startup"
    launcher = startup / "ClaudeCodeCredentialPool.cmd"
    if launcher.exists():
        launcher.unlink()
        print("已移除自启入口。")
    else:
        print("未发现自启入口，跳过。")
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()
        print("已移除本机代理配置。")


def stop_proxy(port: int) -> None:
    url = f"http://127.0.0.1:{port}/shutdown"
    try:
        with urlopen(Request(url, method="GET"), timeout=5) as response:
            response.read()
        print("已请求代理停止。")
    except Exception:
        print("代理未运行或已停止，跳过。")


def reset_table_statuses(app_token: str, table_id: str) -> None:
    token = feishu_token()
    app = quote(app_token, safe="")
    table = quote(table_id, safe="")
    records: list[dict] = []
    page_token = ""
    while True:
        suffix = f"?page_size=500" + (f"&page_token={page_token}" if page_token else "")
        data = bitable(token, f"/apps/{app}/tables/{table}/records{suffix}")
        records.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    reset = 0
    for record in records:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            continue
        fields = record.get("fields") or {}
        status = str(fields.get("状态", "")).strip()
        if status not in PROXY_STATUSES:
            continue
        payload = {"fields": {"状态": "⚪ 空闲", "备注": "Claude Code 代理已停用"}}
        bitable(token, f"/apps/{app}/tables/{table}/records/{quote(record_id, safe='')}", "PUT", payload)
        reset += 1
    print(f"已将 {reset} 条凭证状态重置为「⚪ 空闲」。")


def main() -> int:
    config = {}
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    app_token = str(config.get("CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN") or "").strip() or hermes_config().get("CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN", "").strip()
    table_id = str(config.get("CLAUDE_POOL_FEISHU_BITABLE_TABLE_ID") or "").strip()
    try:
        restore_claude_settings()
        remove_startup()
        stop_proxy(DEFAULT_PORT)
        if app_token and table_id:
            reset_table_statuses(app_token, table_id)
        else:
            print("未找到已启用的 Claude Code 凭证池配置，跳过表格状态清理。", file=sys.stderr)
        print("已禁用 Claude Code 独立凭证池。")
        return 0
    except Exception as error:
        print(f"禁用失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())