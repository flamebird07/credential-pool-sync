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
from concurrent.futures import ThreadPoolExecutor
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
PROBE_MODEL = "__probe__"  # 兜底，credential.model 为空时使用
PROBE_TIMEOUT = 10
PROBE_INTERVAL_HEALTHY = 1500  # 健康凭证每 25 分钟重新探活一次（仅 UI 状态用，真实切换前会再实时检测）
PROBE_INTERVAL_DEGRADED = 120  # 探活失败凭证每 2 分钟重试一次（快速恢复，避免“不兼容”长时间残留）
PROBE_FAIL_TOLERANCE = 2  # 任意凭证连续失败达到该次数才降级写坏状态，避免瞬时抖动
PROBE_SUCCESS_TOLERANCE = 1  # 恢复对称容错：连续成功达到该次数才写回"✅ 正常"（1=立即恢复，因失败方向已有容错防抖动）
CLAUDE_MODEL_KEYWORDS = ("claude", "sonnet", "opus", "haiku", "fable", "mythos", "glm", "zai-org", "deepseek")
UPSTREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
        self._bad: set[tuple[str, str, str]] = set()
        self._index = 0
        self._active_tier: int = 0
        self._by_tier: dict[int, list[Credential]] = {}
        self._token_cache: str = ""
        self._token_ts: float = 0.0
        self._lock = threading.RLock()
        self._last_probe: dict[str, float] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._consecutive_successes: dict[str, int] = {}
        self._auth_override: dict[tuple[str, str, str], str] = {}

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
            # Only rows the user manually disabled stay out of the pool.  Bad
            # states the proxy wrote itself ("❌ 无效" / "⚠️ 额度耗尽") are probed
            # again every cycle so they can auto-recover instead of being locked.
            if any(word in status for word in ("停用", "disabled")):
                continue
            # 重启后恢复内存状态记忆：已健康凭证按 300s 间隔探活、避免 _sync_dashboard
            # 对全量待命凭证误写"✅ 正常"；坏状态凭证保持降级间隔（180s）以便快速恢复（P10-OS）
            record_id = str(record.get("record_id") or "")
            if record_id:
                status_text = field(fields, "status")
                if status_text:
                    self._known_status.setdefault(record_id, status_text)
            credentials.append(Credential(record_id, api_key, base_url.rstrip("/"), field(fields, "model"), priority, field(fields, "label")))
        credentials.sort(key=lambda item: item.priority)
        healthy = self._health_filter(credentials)
        if not healthy:
            raise RuntimeError("No healthy credentials in the Claude Code Feishu table")
        by_tier = self._group_by_tier(healthy)
        with self._lock:
            # 先按本轮探活结果清洗 _bad：恢复健康（进入 healthy 集合）的 identity 解除标记，
            # 仍探活失败（degraded、不在 healthy）的 identity 跨 refresh 保留。
            healthy_ids = {credential_identity(c) for c in healthy}
            self._bad = {identity for identity in self._bad if identity not in healthy_ids}
            active_tier = self._select_active_tier(by_tier, self._bad)
            if active_tier < 0:
                active_tier = min(by_tier)
            self._by_tier = by_tier
            self._active_tier = active_tier
            self._incomplete_records = incomplete_records
            current_identity = credential_identity(self._credentials[self._index]) if self._credentials else None
            self._credentials = list(by_tier[active_tier])
            self._index = next(
                (i for i, item in enumerate(self._credentials) if credential_identity(item) == current_identity and credential_identity(item) not in self._bad),
                0,
            )
            active = self._credentials[self._index]
        self._sync_dashboard(healthy, active)
        self._mark_incomplete_records(incomplete_records)
        return list(healthy)

    def _group_by_tier(self, credentials: list[Credential]) -> dict[int, list[Credential]]:
        """按 0-9 优先级分档（0 最高）。只含健康探活通过的凭证。"""
        by_tier: dict[int, list[Credential]] = {}
        for credential in credentials:
            by_tier.setdefault(credential.priority, []).append(credential)
        return by_tier

    def _select_active_tier(self, by_tier: dict[int, list[Credential]], bad: set[tuple[str, str, str]]) -> int:
        """返回最高优先（最低档号）且含 ≥1 健康未耗尽凭证的档；无则 -1。"""
        for tier in range(10):
            if any(credential_identity(credential) not in bad for credential in by_tier.get(tier, [])):
                return tier
        return -1

    def _health_filter(self, credentials: list[Credential]) -> list[Credential]:
        """Probe the API family this credential's model speaks; each probe uses
        its own short timeout so a hung gateway cannot stall the refresh loop.
        Healthy standby credentials are only re-probed every PROBE_INTERVAL_HEALTHY
        seconds so the pool does not burn quota on credentials that are not used."""
        healthy: list[Credential] = []
        now = time.time()
        to_probe: list[Credential] = []
        with self._lock:
            self._degraded = []
            for credential in credentials:
                last = self._last_probe.get(credential.record_id, 0)
                status_ok = self._known_status.get(credential.record_id) == "✅ 正常"
                interval = PROBE_INTERVAL_HEALTHY if status_ok else PROBE_INTERVAL_DEGRADED
                if now - last < interval:
                    if status_ok:
                        healthy.append(credential)
                    else:
                        self._degraded.append(credential)
                    continue
                to_probe.append(credential)
        def probe(credential: Credential) -> tuple[Credential, int, bytes]:
            model = credential.model or PROBE_MODEL
            if is_claude_model(model) or is_anthropic_endpoint(credential.base_url):
                path = "/v1/messages"
                headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
                body = json.dumps(
                    {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
                    separators=(",", ":"),
                ).encode("utf-8")
            else:
                path = "/v1/chat/completions"
                headers = {"Content-Type": "application/json"}
                body = json.dumps(
                    {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                    separators=(",", ":"),
                ).encode("utf-8")
            identity = credential_identity(credential)
            scheme = self._auth_override.get(identity) or _auth_scheme(model, credential.base_url)
            status_code, _headers, response = forward(credential, path, body, headers, timeout=PROBE_TIMEOUT, auth_scheme=scheme)
            if status_code in (401, 403):
                # 网关对非 Claude 模型的鉴权头可能与默认选择相反；401/403 时用对侧
                # 鉴权头重试一次，并对命中方做持久学习，覆盖间歇性 401/403 抖动
                alt = "bearer" if scheme == "x-api-key" else "x-api-key"
                alt_status, alt_headers, alt_body = forward(credential, path, body, headers, timeout=PROBE_TIMEOUT, auth_scheme=alt)
                if 200 <= alt_status < 300:
                    with self._lock:
                        self._auth_override[identity] = alt
                    status_code, _headers, response = alt_status, alt_headers, alt_body
                else:
                    with self._lock:
                        self._auth_override.pop(identity, None)
            return credential, status_code, response

        with ThreadPoolExecutor(max_workers=min(8, len(to_probe) or 1)) as pool:
            results = list(pool.map(probe, to_probe))
        with self._lock:
            for credential, status_code, response in results:
                self._last_probe[credential.record_id] = now
                if 200 <= status_code < 300:
                    self._consecutive_failures.pop(credential.record_id, None)
                    self._consecutive_successes[credential.record_id] = self._consecutive_successes.get(credential.record_id, 0) + 1
                    if self._consecutive_successes[credential.record_id] >= PROBE_SUCCESS_TOLERANCE:
                        try:
                            self._write_ui_state(credential, "✅ 正常", "Claude Code 凭证池待命（探活通过）")
                        except Exception as write_error:
                            print(f"WARNING: unable to write healthy result: {write_error}", file=sys.stderr)
                    healthy.append(credential)
                    continue
                # Keep the failed credential in memory (not the active pool) so a
                # manual rotate() can still select it again despite bad health.
                self._degraded.append(credential)
                self._consecutive_successes.pop(credential.record_id, None)
                detail = response.decode("utf-8", "replace")[:180]
                if status_code == 429:
                    display_status = "⛔ 限流"
                    note = f"Claude Code 上游检查失败：HTTP 429；{detail or '额度或限流'}"
                elif status_code in (401, 403):
                    display_status = "❌ 无效"
                    note = f"Claude Code 上游检查失败：HTTP {status_code}；API Key 无效或无权限"
                elif status_code == 404:
                    display_status = "❌ Claude 不兼容"
                    note = "该 Base URL 不支持所需的探活接口；请换用 Anthropic / OpenAI 兼容地址"
                else:
                    display_status = "⚠️ 不可用"
                    note = f"Claude Code 上游检查失败：HTTP {status_code}；{detail or '请检查上游服务连通性'}"
                # 容错：无论当前 _known_status 为何值，连续失败未达阈值不写坏状态（P3-OS）
                self._consecutive_failures[credential.record_id] = self._consecutive_failures.get(credential.record_id, 0) + 1
                if self._consecutive_failures[credential.record_id] < PROBE_FAIL_TOLERANCE:
                    continue
                try:
                    self._write_ui_state(credential, display_status, note)
                except Exception as write_error:
                    print(f"WARNING: unable to write health result: {write_error}", file=sys.stderr)
        return healthy

    def _write_ui_state(self, credential: Credential, status: str, note: str) -> None:
        """Keep the Claude-only Feishu table useful as a live status board."""
        if not credential.record_id:
            return
        with self._lock:
            token = self._token()
            fields = {
                FIELD_ALIASES["status"][0]: status,
                FIELD_ALIASES["note"][0]: note[:300],
            }
            payload = json.dumps({"fields": fields}, ensure_ascii=False).encode("utf-8")
            last_error: str = ""
            for attempt in range(3):
                request = Request(
                    f"{FEISHU_API}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{credential.record_id}",
                    data=payload,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                    method="PUT",
                )
                try:
                    with urlopen(request, timeout=15) as response:
                        result = json.loads(response.read())
                except HTTPError as http_err:
                    body = http_err.read().decode("utf-8", "replace") if http_err.fp else ""
                    # Feishu 429 限流时重试
                    if http_err.code == 429 and attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    raise RuntimeError(f"Feishu PUT HTTP {http_err.code}: {body[:120]}")
                except URLError as url_err:
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    raise
                if result.get("code") == 0:
                    break
                last_error = str(result.get("msg", "unknown"))
                # Feishu 429 限流重试
                if result.get("code") == 429 and attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RuntimeError(last_error or "Feishu status update failed")
            else:
                if last_error:
                    raise RuntimeError(last_error)
            # 飞书写成功后才更新内存状态，保证内存与飞书一致（P4-OS）；
            # 写入失败时内存保持旧值，_sync_dashboard 下轮会重试修复（P5-OS）
            self._known_status[credential.record_id] = status

    def _sync_dashboard(self, credentials: list[Credential], active: Credential) -> None:
        """Mark standby routes healthy. A passing probe also restores 限流/额度耗尽
        rows back to 正常 (recovery), so a transient failure never freezes a key.
        Already-correct rows are skipped to avoid redundant Feishu writes."""
        for credential in credentials:
            try:
                if credential.record_id == active.record_id:
                    continue
                cached = self._known_status.get(credential.record_id)
                if cached and cached.startswith("✅"):
                    continue
                # 成功恢复与失败降级对称：连续成功未达阈值不写回"✅ 正常"，
                # 防止 dashboard 同步绕过探活容错把状态提前翻绿（P13-OS）
                if self._consecutive_successes.get(credential.record_id, 0) < PROBE_SUCCESS_TOLERANCE:
                    continue
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

    def _probe_credential(self, credential: Credential) -> bool:
        """Real-time single-credential probe used before actually switching to it.
        The periodic probe only refreshes the UI status; a real switch must
        re-verify the key is currently usable so a stale (25-min-old) result
        never routes a real request to a now-dead credential."""
        model = credential.model or PROBE_MODEL
        if is_claude_model(model) or is_anthropic_endpoint(credential.base_url):
            path = "/v1/messages"
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            body = json.dumps(
                {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
                separators=(",", ":"),
            ).encode("utf-8")
        else:
            path = "/v1/chat/completions"
            headers = {"Content-Type": "application/json"}
            body = json.dumps(
                {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
                separators=(",", ":"),
            ).encode("utf-8")
        identity = credential_identity(credential)
        scheme = self._auth_override.get(identity) or _auth_scheme(model, credential.base_url)
        status_code, _headers, response = forward(credential, path, body, headers, timeout=PROBE_TIMEOUT, auth_scheme=scheme)
        if status_code in (401, 403):
            alt = "bearer" if scheme == "x-api-key" else "x-api-key"
            alt_status, alt_headers, alt_body = forward(credential, path, body, headers, timeout=PROBE_TIMEOUT, auth_scheme=alt)
            if 200 <= alt_status < 300:
                with self._lock:
                    self._auth_override[identity] = alt
                return True
            return False
        self._last_probe[credential.record_id] = time.time()
        return 200 <= status_code < 300

    def current(self, verify: bool = False) -> Credential:
        with self._lock:
            if not self._credentials:
                self.refresh()
            for offset in range(len(self._credentials)):
                candidate = self._credentials[(self._index + offset) % len(self._credentials)]
                if credential_identity(candidate) not in self._bad:
                    # 真实使用前实时探活确认（verify=True）：避免用 25 分钟前的探活
                    # 结果选到已失效凭证。仅 UI/显示用途（/health）不探活。
                    if not verify or self._probe_credential(candidate):
                        self._index = (self._index + offset) % len(self._credentials)
                        return candidate
            return self._credentials[self._index]

    def next_after(self, failed: Credential) -> Credential | None:
        with self._lock:
            if not self._credentials:
                return None
            start = next((i for i, item in enumerate(self._credentials) if credential_identity(item) == credential_identity(failed)), self._index)
            for offset in range(1, len(self._credentials)):
                candidate = self._credentials[(start + offset) % len(self._credentials)]
                if credential_identity(candidate) != credential_identity(failed) and credential_identity(candidate) not in self._bad:
                    # 切换前实时探活确认，避免切到已失效凭证
                    if self._probe_credential(candidate):
                        self._index = (start + offset) % len(self._credentials)
                        break
            else:
                if not self._advance_tier():
                    return None
            candidate = self._credentials[self._index]
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
        """Manually advance within the active tier; advance a tier only when it is exhausted."""
        with self._lock:
            if not self._credentials:
                return None
            start = self._index
            for offset in range(1, len(self._credentials)):
                candidate = self._credentials[(start + offset) % len(self._credentials)]
                if credential_identity(candidate) not in self._bad:
                    # 手动切换前同样实时探活确认
                    if self._probe_credential(candidate):
                        self._index = (start + offset) % len(self._credentials)
                        break
            else:
                if not self._advance_tier():
                    return None
            candidate = self._credentials[self._index]
        try:
            self._write_ui_state(candidate, "🔄 Claude Code 使用中", f"手动切换成功；当前模型：{candidate.model or '未填写'}")
        except Exception as error:
            print(f"WARNING: unable to mark active credential: {error}", file=sys.stderr)
        return candidate

    def _advance_tier(self) -> bool:
        """active 档耗尽后推进到下一个仍含健康可选用凭证的档。"""
        for tier in sorted(self._by_tier):
            if tier <= self._active_tier:
                continue
            available = [i for i, c in enumerate(self._by_tier[tier]) if credential_identity(c) not in self._bad]
            if available:
                self._active_tier = tier
                self._credentials = list(self._by_tier[tier])
                self._index = available[0]
                return True
        return False

    def _total_healthy(self) -> int:
        return sum(len(credentials) for credentials in self._by_tier.values())

    def mark_failure(self, credential: Credential, status: int, reason: str) -> None:
        if not credential.record_id:
            return
        with self._lock:
            self._bad.add(credential_identity(credential))
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


def is_claude_model(model: str) -> bool:
    """True when the model speaks Anthropic Messages; otherwise OpenAI-compatible."""
    name = model.strip().lower()
    if not name:
        return True
    return any(word in name for word in CLAUDE_MODEL_KEYWORDS)


def is_anthropic_endpoint(base_url: str) -> bool:
    """True when the base URL is an Anthropic-compatible endpoint (e.g. ends
    with /anthropic or contains /v1/messages), even if the pinned model name
    is not a Claude-family name (e.g. qwen / mimo / deepseek behind an
    Anthropic-compatible gateway)."""
    url = (base_url or "").lower()
    return "/anthropic" in url or "/v1/messages" in url


def _auth_scheme(model: str, base_url: str = "") -> str:
    """鉴权头方案由"模型名 + 端点"共同决定。Anthropic 兼容端点（base_url 含
    /v1/messages 或 /anthropic）用 x-api-key（Anthropic 惯例）；纯 OpenAI 兼容
    端点用 Bearer。opencode.ai / 小米 / DeepSeek 等 Anthropic 网关都期望 x-api-key，
    即使 pinned model 是非 Claude 名。"""
    if is_anthropic_endpoint(base_url):
        return "x-api-key"
    return "x-api-key" if is_claude_model(model) else "bearer"


def _apply_auth(outbound: dict[str, str], credential: Credential, scheme: str) -> None:
    if scheme == "x-api-key":
        outbound["x-api-key"] = credential.api_key
    else:
        outbound["Authorization"] = f"Bearer {credential.api_key}"


def join_target(base_url: str, path: str) -> str:
    """Join Base URL + request path without stacking duplicate version segments
    (https://host/v1 + /v1/messages -> https://host/v1/messages;
     https://host/v1/messages + /v1/messages -> https://host/v1/messages)."""
    base = base_url.rstrip("/")
    rel = path.lstrip("/")
    segment = rel.split("/", 1)[0]
    if segment[:1] == "v" and segment[1:].isdigit():
        if base.endswith("/" + segment):
            base = base[: -(len(segment) + 1)]
        elif ("/" + segment + "/") in base:
            base = base.split("/" + segment + "/", 1)[0]
    return base + "/" + rel


def forward(
    credential: Credential,
    path: str,
    body: bytes,
    headers: dict[str, str],
    on_headers: Any = None,
    on_chunk: Any = None,
    timeout: int = 600,
    auth_scheme: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    # Base URL may be https://host/api/anthropic or https://host/v1; preserve
    # the request path so every Anthropic / OpenAI compatible gateway works.
    target = join_target(credential.base_url, path)
    # A table row can pin the actual gateway model for this key.  Probes use
    # credential.model directly, but skip the rewrite when the body is our
    # short-lived ping payload (PROBE_MODEL) so the rewrite cannot feed bogus
    # model strings back into the upstream on the next refresh cycle.
    if credential.model:
        try:
            request_json = json.loads(body)
            if isinstance(request_json, dict) and request_json.get("model") != PROBE_MODEL:
                request_json["model"] = credential.model
                body = json.dumps(request_json, ensure_ascii=False, separators=(",", ":")).encode()
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    outbound = {key: value for key, value in headers.items() if key.lower() not in {"host", "content-length", "x-api-key", "authorization", "connection"}}
    if "user-agent" not in {key.lower() for key in outbound}:
        outbound["User-Agent"] = UPSTREAM_USER_AGENT
    # 鉴权方案默认按模型名决定（Claude→x-api-key，其余→Bearer）；显式 auth_scheme
    # 优先（探活学习到对侧方案或 401/403 重试时使用），见 _auth_override
    _apply_auth(outbound, credential, auth_scheme or _auth_scheme(credential.model, credential.base_url))
    outbound["Content-Length"] = str(len(body))
    request = Request(target, data=body, headers=outbound, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
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
    except (TimeoutError, OSError) as error:
        return 503, {"Content-Type": "application/json"}, json.dumps({"error": {"message": str(error), "type": "proxy_timeout"}}).encode()


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
            # 真实请求前实时探活确认，避免切到已失效凭证
            credential = self.pool.current(verify=True)
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
        # 档位跨档推进时循环上限取全部健康凭证总数，避免 active 档耗尽后提前退出
        total_healthy = self.pool._total_healthy()
        while credential and attempts < max(1, total_healthy):
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
                auth_scheme=self.pool._auth_override.get(credential_identity(credential)),
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
