#!/usr/bin/env python3
"""One-command enablement shared by Hermes and OpenCode."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml

from claude_credential_proxy import CONFIG_PATH, FEISHU_API, CredentialPool, feishu_token, hermes_config

TABLE_NAME = "Claude Code 凭证池"
FIELDS = (("Provider", 1), ("Label", 1), ("API Key", 1), ("Base URL", 1), ("模型", 1), ("优先级", 2), ("状态", 1), ("备注", 1))


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


def provision_table(app_token: str) -> str:
    token = feishu_token()
    app = quote(app_token, safe="")
    tables = bitable(token, f"/apps/{app}/tables").get("items") or []
    table = next((item for item in tables if item.get("name") == TABLE_NAME), None)
    if table is None:
        table = bitable(token, f"/apps/{app}/tables", "POST", {"table": {"name": TABLE_NAME}}).get("table")
        if not table:
            raise RuntimeError("飞书未返回新建数据表")
    table_id = str(table.get("table_id") or "")
    existing = {item.get("field_name") for item in bitable(token, f"/apps/{app}/tables/{quote(table_id, safe='')}/fields").get("items", [])}
    for name, field_type in FIELDS:
        if name not in existing:
            bitable(token, f"/apps/{app}/tables/{quote(table_id, safe='')}/fields", "POST", {"field_name": name, "type": field_type})
    return table_id


def update_claude_settings() -> None:
    target = Path.home() / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    if not isinstance(settings, dict):
        raise RuntimeError("Claude Code 设置文件格式不正确")
    if target.exists():
        shutil.copy2(target, target.with_name(f"settings.json.before-claude-pool-{datetime.now():%Y%m%d%H%M%S}.bak"))
    settings["env"] = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    port = os.getenv("CLAUDE_POOL_PROXY_PORT", "21435")
    settings["env"].update({"ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "managed-by-claude-credential-proxy"})
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, target)


def register_startup() -> None:
    script = Path(__file__).with_name("claude_credential_proxy.py").resolve()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    executable = str(pythonw if pythonw.exists() else Path(sys.executable))
    startup = Path(os.getenv("APPDATA", str(Path.home() / "AppData/Roaming"))) / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup.mkdir(parents=True, exist_ok=True)
    launcher = startup / "ClaudeCodeCredentialPool.cmd"
    launcher.write_text(f'@echo off\r\nstart "" "{executable}" -B "{script}"\r\n', encoding="utf-8")
    subprocess.Popen([executable, "-B", str(script)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    config = hermes_config()
    app_token = config.get("CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN", "").strip()
    if not config.get("CLAUDE_POOL_FEISHU_APP_ID") or not config.get("CLAUDE_POOL_FEISHU_APP_SECRET") or not app_token:
        print("未在 Hermes 本机配置中找到飞书机器人或现有凭证池位置，无法自动启用。", file=sys.stderr)
        return 2
    os.environ.update(config)
    try:
        table_id = provision_table(app_token)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps({"CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN": app_token, "CLAUDE_POOL_FEISHU_BITABLE_TABLE_ID": table_id}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            credential_count = len(CredentialPool().refresh())
        except RuntimeError as error:
            if "No healthy credentials in the Claude Code Feishu table" in str(error):
                print("Claude credential table is ready. Add at least one credential, then enable Claude credential pool again.")
                return 0
            raise
        register_startup()
        update_claude_settings()
        print(f"已启用 Claude Code 独立凭证池。飞书已创建/复用“{TABLE_NAME}”，请在其中填入 DeepSeek 凭证后重启 Claude Code。")
        return 0
    except Exception as error:
        print(f"启用失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
