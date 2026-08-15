#!/usr/bin/env python3
"""Claude Code credential-pool proxy.

The proxy keeps one stable local endpoint for Claude Code.  It reads its own
Feishu Bitable (never Hermes' table) and retries a request with the next
DeepSeek credential when the upstream rejects a key for quota/billing/rate
limit reasons.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FEISHU_API = "https://open.feishu.cn/open-apis"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 21435
RETRYABLE_STATUS = {402, 403, 429}
QUOTA_WORDS = ("quota", "balance", "billing", "insufficient", "credit", "额度", "余额", "欠费", "限流", "rate limit")
FIELD_ALIASES = {
    "api_key": ("API Key", "APIKEY", "Api Key"),
    "base_url": ("Base URL", "BaseURL", "Endpoint"),
    "model": ("模型", "Model"),
    "priority": ("优先级", "Priority"),
    "status": ("状态", "Status"),
    "note": ("备注", "Notes", "Note"),
    "label": ("Label", "标签"),
}
CONFIG_PATH = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ClaudeCodeCredentialPool" / "config.json"


def local_config() -> dict[str, str]:
    """Read the wizard-created local configuration without ever logging it."""
    try:
        content = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return content if isinstance(content, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def hermes_config() -> dict[str, str]:
    """Reuse the locally configured Hermes Feishu bot; never ask for it again."""
    path = Path(os.getenv("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "hermes" / "config.yaml"
    try:
        import yaml
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        feishu = ((config.get("platforms") or {}).get("feishu") or {}).get("extra") or {}
        if not feishu:
            feishu = (config.get("secrets") or {}).get("feishu") or config.get("feishu") or {}
        return {
            "CLAUDE_POOL_FEISHU_APP_ID": str(feishu.get("app_id") or feishu.get("appId") or ""),
            "CLAUDE_POOL_FEISHU_APP_SECRET": str(feishu.get("app_secret") or feishu.get("appSecret") or ""),
            "CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN": str((config.get("credential_pool_sync") or {}).get("bitable_app_token") or ""),
        }
    except Exception:
        return {}


def env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip() or str(local_config().get(name, "")).strip() or str(hermes_config().get(name, "")).strip()
    if required and not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


def field(fields: dict[str, Any], name: str, default: str = "") -> str:
    for alias in FIELD_ALIASES[name]:
        value = fields.get(alias)
        if value is not None:
            return str(value).strip()
    return default


def feishu_token() -> str:
    payload = json.dumps({"app_id": env("CLAUDE_POOL_FEISHU_APP_ID"), "app_secret": env("CLAUDE_POOL_FEISHU_APP_SECRET")}).encode()
    request = Request(f"{FEISHU_API}/auth/v3/app_access_token/internal", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read())
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu authentication failed: {data.get('msg', 'unknown error')}")
    return data["app_access_token"]


@dataclass(frozen=True)
class Credential:
    record_id: str
    api_key: str
    base_url: str
    model: str
    priority: int
    label: str


def credential_identity(credential: Credential) -> tuple[str, str, str]:
    """Match Hermes' credential identity: model + API key + normalized URL."""
    return (
        credential.model.strip().lower(),
        credential.api_key.strip(),
        credential.base_url.rstrip("/").strip().lower(),
    )


class CredentialPool:
    def __init__(self) -> None:
        self.app_token = env("CLAUDE_POOL_FEISHU_BITABLE_APP_TOKEN")
        self.table_id = env("CLAUDE_POOL_FEISHU_BITABLE_TABLE_ID")
        self._credentials: list[Credential] = []
        self._incomplete_records: list[tuple[str, str]] = []
        self._degraded: list[Credential] = []
        self._known_status: dict[str, str] = {}
        self._bad: set[str] = set()
        self._index = 0
        self._token_cache: str = ""
        self._token_ts: float = 0.0
        self._lock = threading.RLock()

    def _token(self) -> str:
        """Reuse the Feishu app_access_token (valid ~2h) for ~110 minutes."""
        now = time.time()
        if not self._token_cache or now - self._token_ts > 6600:
            self._token_cache = feishu_token()
            self._token_ts = now
        return self._token_cache

    def refresh(self) -> list[Credential]:
        token = self._token()
        records: list[dict[str, Any]] = []
        page_token = ""
        while True:
            suffix = f"?page_size=500&page_token={page_token}" if page_token else "?page_size=500"
            request = Request(f"{FEISHU_API}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records{suffix}", headers={"Authorization": f"Bearer {token}"})
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read())
            if result.get("code") != 0:
                raise RuntimeError(f"Feishu read failed: {result.get('msg', 'unknown error')}")
            data = result.get("data") or {}
            records.extend(data.get("items") or [])
            if not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        credentials = []
        incomplete_records: list[tuple[str, str]] = []
        for record in records:
            fields = record.get("fields") or {}
            api_key, base_url = field(fields, "api_key"), field(fields, "base_url")
            if not api_key or api_key == "***" or not base_url:
                record_id = str(record.get("record_id") or "")
                if record_id:
                    incomplete_records.append((record_id, field(fields, "label") or field(fields, "model")))
                continue
            try:
                priority = int(field(fields, "priority", "9"))
            except ValueError:
                priority = 9
            status = field(fields, "status").lower()
            # 只剔除硬性失效；限流/额度耗尽是可恢复态，交给健康探活复核后恢复
            if any(word in status for word in ("无效", "invalid", "停用", "disabled")):
                continue
            credentials.append(Credential(record.get("record_id", ""), api_key, base_url.rstrip("/"), field(fields, "model"), priority, field(fields, "label")))
        credentials.sort(key=lambda item: item.priority)
        self._bad.clear()
        credentials = self._health_filter(credentials)
        if not credentials:
            raise RuntimeError("No healthy credentials in the Claude Code Feishu table")
        with self._lock:
            current_identity = credential_identity(self._credentials[self._index]) if self._credentials else None
            self._credentials = credentials
            self._incomplete_records = incomplete_records
            self._index = next((i for i, item in enumerate(credentials) if credential_identity(item) == current_identity), 0)
            active = self._credentials[self._index]
        self._sync_dashboard(credentials, active)
        self._mark_incomplete_records(incomplete_records)
        return list(credentials)

    def _health_filter(self, credentials: list[Credential]) -> list[Credential]:
        """Probe the Anthropic Messages API that Claude Code actually calls."""
        self._degraded = []
        healthy: list[Credential] = []
        body = json.dumps(
            {"model": "credential-pool-probe", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
            separators=(",", ":"),
        ).encode("utf-8")
        for credential in credentials:
            status_code, _headers, response = forward(
                credential,
                "/v1/messages",
                body,
                {"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
            )
            if 200 <= status_code < 300:
                healthy.append(credential)
                continue
            # Keep the failed credential in memory (not the active pool) so a
            # manual rotate() can still select it again despite bad health.
            self._degraded.append(credential)
            detail = response.decode("utf-8", "replace")[:180]
            if status_code == 429:
                display_status = "⛔ 限流"
                note = f"Claude Code 上游检查失败：HTTP 429；{detail or '额度或限流'}"
            elif status_code in (401, 403):
                display_status = "❌ 无效"
                note = f"Claude Code 上游检查失败：HTTP {status_code}；API Key 无效或无权限"
            elif status_code == 404:
                display_status = "❌ Claude 不兼容"
                note = "该 Base URL 不支持 Claude Code 所需的 /v1/messages 接口；请换用 Anthropic 兼容地址"
            else:
                display_status = "⚠️ 不可用"
                note = f"Claude Code 上游检查失败：HTTP {status_code}；{detail or '请检查上游服务连通性'}"
            try:
                self._write_ui_state(credential, display_status, note)
            except Exception as write_error:
                print(f"WARNING: unable to write health result: {write_error}", file=sys.stderr)
        return healthy

    def _write_ui_state(self, credential: Credential, status: str, note: str) -> None:
        """Keep the Claude-only Feishu table useful as a live status board."""
        if not credential.record_id:
            return
        self._known_status[credential.record_id] = status
        token = self._token()
        fields = {
            FIELD_ALIASES["status"][0]: status,
            FIELD_ALIASES["note"][0]: note[:300],
        }
        payload = json.dumps({"fields": fields}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{FEISHU_API}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{credential.record_id}",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
            method="PUT",
        )
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
        if result.get("code") != 0:
            raise RuntimeError(result.get("msg", "Feishu status update failed"))

    def _sync_dashboard(self, credentials: list[Credential], active: Credential) -> None:
        """Mark standby routes healthy. A passing probe also restores 限流/额度耗尽
        rows back to 正常 (recovery), so a transient failure never freezes a key."""
        for credential in credentials:
            try:
                if credential.record_id != active.record_id and self._known_status.get(credential.record_id) != "✅ 正常":
                    self._write_ui_state(credential, "✅ 正常", "Claude Code 凭证池待命（探活通过）")
            except Exception as error:
                print(f"WARNING: unable to update Claude credential dashboard: {error}", file=sys.stderr)

    def _mark_incomplete_records(self, records: list[tuple[str, str]]) -> None:
        for record_id, label in records:
            try:
                self._write_ui_state(
                    Credential(record_id, "", "", "", 9, label),
                    "⚪ 未配置",
                    "缺少 API Key 或 Base URL，未加入 Claude Code 凭证池",
                )
            except Exception as error:
                print(f"WARNING: unable to mark incomplete credential: {error}", file=sys.stderr)

    def current(self) -> Credential:
        with self._lock:
            if not self._credentials:
                self.refresh()
            return self._credentials[self._index]

    def next_after(self, failed: Credential) -> Credential | None:
        with self._lock:
            if not self._credentials:
                return None
            failed_identity = credential_identity(failed)
            start = next((i for i, item in enumerate(self._credentials) if credential_identity(item) == failed_identity), self._index)
            for offset in range(1, len(self._credentials)):
                candidate = self._credentials[(start + offset) % len(self._credentials)]
                if credential_identity(candidate) != failed_identity and candidate.api_key not in self._bad:
                    self._index = (start + offset) % len(self._credentials)
                    break
            else:
                return None
        try:
            self._write_ui_state(
                candidate,
                "🔄 Claude Code 使用中",
                f"自动切换成功；当前模型：{candidate.model or '未填写'}",
            )
        except Exception as error:
            print(f"WARNING: unable to mark active credential: {error}", file=sys.stderr)
        return candidate

    def rotate(self) -> Credential | None:
        """Manually advance to the next healthy (non-degraded) credential."""
        with self._lock:
            if not self._credentials:
                return None
            start = self._index
            for offset in range(1, len(self._credentials) + 1):
                candidate = self._credentials[(start + offset) % len(self._credentials)]
                if candidate.api_key not in self._bad:
                    self._index = (start + offset) % len(self._credentials)
                    break
            else:
                return None
        try:
            self._write_ui_state(candidate, "🔄 Claude Code 使用中", f"手动切换成功；当前模型：{candidate.model or '未填写'}")
        except Exception as error:
            print(f"WARNING: unable to mark active credential: {error}", file=sys.stderr)
        return candidate

    def mark_failure(self, credential: Credential, status: int, reason: str) -> None:
        if not credential.record_id:
            return
        self._bad.add(credential.api_key)
        try:
            if status == 429:
                self._write_ui_state(credential, "⛔ 限流", f"HTTP 429；{reason[:160]}")
            else:
                self._write_ui_state(credential, "⚠️ 额度耗尽", reason[:160])
        except Exception as error:
            print(f"WARNING: unable to mark failed credential: {error}", file=sys.stderr)


def is_quota_failure(status: int, body: bytes) -> bool:
    text = body.decode("utf-8", "replace").lower()
    return status in RETRYABLE_STATUS and (status == 429 or any(word in text for word in QUOTA_WORDS))


def forward(
    credential: Credential,
    path: str,
    body: bytes,
    headers: dict[str, str],
    on_headers: Any = None,
    on_chunk: Any = None,
) -> tuple[int, dict[str, str], bytes]:
    # Base URL may be https://host/api/anthropic or https://host/v1; preserve
    # the Claude request path so every Anthropic-compatible DeepSeek gateway works.
    target = credential.base_url.rstrip("/") + "/" + path.lstrip("/")
    # A table row can pin the actual gateway model for this key.  This lets a
    # Claude Code session keep its familiar model setting while the proxy uses
    # the DeepSeek model accepted by the configured Anthropic-compatible API.
    if credential.model:
        try:
            request_json = json.loads(body)
            if isinstance(request_json, dict):
                request_json["model"] = credential.model
                body = json.dumps(request_json, ensure_ascii=False, separators=(",", ":")).encode()
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    outbound = {key: value for key, value in headers.items() if key.lower() not in {"host", "content-length", "x-api-key", "authorization", "connection"}}
    outbound["x-api-key"] = credential.api_key
    outbound["Authorization"] = f"Bearer {credential.api_key}"
    outbound["Content-Length"] = str(len(body))
    request = Request(target, data=body, headers=outbound, method="POST")
    try:
        with urlopen(request, timeout=600) as response:
            status = response.status
            response_headers = dict(response.headers.items())
            if on_headers is not None:
                on_headers(status, response_headers)
            if on_chunk is not None:
                chunks = bytearray()
                data = response.read(65536)
                while data:
                    chunks.extend(data)
                    on_chunk(data)
                    data = response.read(65536)
                return status, response_headers, bytes(chunks)
            return status, response_headers, response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()) if error.headers else {}, error.read()
    except URLError as error:
        return 503, {"Content-Type": "application/json"}, json.dumps({"error": {"message": str(error), "type": "proxy_error"}}).encode()


class Handler(BaseHTTPRequestHandler):
    pool: CredentialPool
    server_version = "ClaudeCredentialPool/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        if self.path == "/health":
            try:
                current = self.pool.current()
                self._send(200, {"Content-Type": "application/json"}, json.dumps({"ok": True, "active_label": current.label, "active_model": current.model}).encode())
            except Exception as error:
                self._send(503, {"Content-Type": "application/json"}, json.dumps({"ok": False, "error": str(error)}).encode())
            return
        if self.path == "/shutdown":
            self._send(200, {"Content-Type": "application/json"}, b'{"ok":true,"shutdown":true}')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._send(404, {"Content-Type": "application/json"}, b'{"error":"not found"}')

    def do_POST(self) -> None:
        if self.path in ("/switch", "/rotate"):
            try:
                previous = self.pool.current()
                switched = self.pool.rotate()
            except Exception as error:
                self._send(
                    503,
                    {"Content-Type": "application/json"},
                    json.dumps({"ok": False, "error": str(error)}).encode(),
                )
                return
            self._send(
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "ok": True,
                        "from": previous.label,
                        "to": switched.label if switched else None,
                        "model": switched.model if switched else None,
                    }
                ).encode(),
            )
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            credential = self.pool.current()
        except Exception as error:
            self._send(503, {"Content-Type": "application/json"}, json.dumps({"error": {"message": str(error), "type": "credential_pool_error"}}).encode())
            return
        try:
            self.pool._write_ui_state(credential, "🔄 Claude Code 使用中", f"本机代理已连接；当前模型：{credential.model or '未填写'}")
        except Exception as error:
            print(f"WARNING: unable to mark active credential: {error}", file=sys.stderr)
        # Retry every other key once.  Claude Code stays connected to this
        # process, so a quota event is invisible to its development session.
        attempts = 0
        all_failed = False
        while credential and attempts < max(1, len(self.pool._credentials)):
            # Stream the first upstream 2xx straight back to Claude Code
            # instead of buffering the whole body; on_headers/on_chunk only
            # forward a 2xx, so quota failures still buffer for the retry loop.
            self._streaming = False
            status, response_headers, response_body = forward(
                credential,
                self.path,
                body,
                dict(self.headers.items()),
                on_headers=self._on_upstream_headers,
                on_chunk=self._on_upstream_chunk,
            )
            if self._streaming:
                return
            if not is_quota_failure(status, response_body):
                self._send(status, response_headers, response_body)
                return
            all_failed = True
            self.pool.mark_failure(credential, status, response_body.decode("utf-8", "replace"))
            credential = self.pool.next_after(credential)
            attempts += 1
        if all_failed:
            body = json.dumps({"error": {"message": "All Claude Code credentials in the pool are exhausted or rate-limited", "type": "proxy_out_of_credentials"}}).encode()
            self._send(429, {"Content-Type": "application/json"}, body)
            return
        self._send(status, response_headers, response_body)

    def _on_upstream_headers(self, status: int, response_headers: dict[str, str]) -> None:
        """Stream only a 2xx upstream response; buffer anything else so the
        first attempt can still fall through to the quota retry loop."""
        self._streaming = 200 <= status < 300
        if not self._streaming:
            return
        self.send_response(status)
        for key, value in response_headers.items():
            if key.lower() not in {"transfer-encoding", "content-length", "connection", "content-encoding"}:
                self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()

    def _on_upstream_chunk(self, data: bytes) -> None:
        if self._streaming:
            self.wfile.write(data)
            self.wfile.flush()

    def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() not in {"transfer-encoding", "content-length", "connection", "content-encoding"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code independent Feishu credential-pool proxy")
    parser.add_argument("--host", default=os.getenv("CLAUDE_POOL_PROXY_HOST", DEFAULT_HOST))
    parser.add_argument("--port", default=int(os.getenv("CLAUDE_POOL_PROXY_PORT", str(DEFAULT_PORT))), type=int)
    parser.add_argument("--check", action="store_true", help="verify access to the Claude-only Feishu table and exit")
    parser.add_argument("--refresh-interval", default=int(os.getenv("CLAUDE_POOL_REFRESH_INTERVAL", "60")), type=int, help="seconds between dashboard refresh cycles")
    args = parser.parse_args()
    try:
        pool = CredentialPool()
        credentials = pool.refresh()
    except (RuntimeError, HTTPError, URLError, OSError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if args.check:
        print(f"OK: {len(credentials)} Claude Code credential(s) loaded from its independent Feishu table")
        return 0
    Handler.pool = pool
    server = ThreadingHTTPServer((args.host, args.port), Handler)

    def _refresh_loop(pool: CredentialPool, interval: int) -> None:
        while True:
            time.sleep(interval)
            try:
                pool.refresh()
            except Exception as error:
                print(f"WARNING: periodic refresh failed: {error}", file=sys.stderr)

    threading.Thread(target=_refresh_loop, args=(pool, args.refresh_interval), daemon=True).start()
    print(f"Claude Code credential proxy listening on http://{args.host}:{args.port} ({len(credentials)} credential(s))")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
