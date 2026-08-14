#!/usr/bin/env python3
"""凭证池同步脚本 v7.17.0 — 含连通性验证、状态回写和安全配置加载。"""
import argparse, json, os, sys, urllib.request, urllib.error, time, subprocess, re, msvcrt
import random
import uuid

# The gateway injects this directory through PYTHONPATH, but scheduled and
# manual invocations do not. Keep the standalone credential-pool entry point
# runnable in both cases.
_hermes_site_packages = os.path.join(
    os.environ.get("APPDATA", ""), "uv", "tools", "hermes-agent", "Lib", "site-packages"
)
if os.path.isdir(_hermes_site_packages) and _hermes_site_packages not in sys.path:
    sys.path.insert(0, _hermes_site_packages)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

import yaml
from urllib.parse import urlparse
from contextlib import contextmanager
from pathlib import Path

def request_with_retry(req, timeout=8, max_retries=2):
    for i in range(max_retries):
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and i < max_retries - 1:
                time.sleep(2 ** i * (0.5 + random.random() * 0.5))
                continue
            raise
        except (urllib.error.URLError, OSError):
            if i == max_retries - 1:
                raise
            time.sleep(2 ** i * (0.5 + random.random() * 0.5))

def get_hermes_home():
    """Return the single Hermes data directory used by both scripts."""
    return Path.home() / "AppData" / "Local" / "hermes"

def get_runtime_config_path():
    """Return the Hermes runtime config path used by all helper scripts."""
    return get_hermes_home() / "config.yaml"

AUTH_JSON = get_hermes_home() / "auth.json"

def load_feishu_credentials():
    """Load Feishu credentials from the environment or Hermes config."""
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return app_id, app_secret
    try:
        import yaml
        config = yaml.safe_load(get_runtime_config_path().read_text(encoding="utf-8")) or {}
        candidates = [
            config.get("feishu"),
            (config.get("secrets") or {}).get("feishu") if isinstance(config.get("secrets"), dict) else None,
            (config.get("channels") or {}).get("feishu") if isinstance(config.get("channels"), dict) else None,
            ((config.get("platforms") or {}).get("feishu") or {}).get("extra") if isinstance(config.get("platforms"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, dict):
                config_id = str(candidate.get("app_id") or candidate.get("appId") or "").strip()
                config_secret = str(candidate.get("app_secret") or candidate.get("appSecret") or "").strip()
                if config_id and config_secret:
                    return config_id, config_secret
    except Exception:
        pass
    raise RuntimeError(
        "Feishu credentials are not configured. Set FEISHU_APP_ID and FEISHU_APP_SECRET, "
        "or configure secrets.feishu.app_id and secrets.feishu.app_secret in Hermes config.yaml."
    )


def load_bitable_ids():
    """Load the Feishu Bitable app token and table ID without embedding them in source.

    Environment variables take precedence.  Hermes config supports the same
    values under ``credential_pool_sync`` or any existing Feishu mapping so
    scheduled jobs work without inheriting an interactive shell environment.
    """
    app_token = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
    table_id = os.environ.get("FEISHU_BITABLE_TABLE_ID", "").strip()
    try:
        config = yaml.safe_load(get_runtime_config_path().read_text(encoding="utf-8")) or {}
        feishu_mappings = [
            config.get("credential_pool_sync"),
            config.get("feishu"),
            (config.get("secrets") or {}).get("feishu") if isinstance(config.get("secrets"), dict) else None,
            (config.get("channels") or {}).get("feishu") if isinstance(config.get("channels"), dict) else None,
            ((config.get("platforms") or {}).get("feishu") or {}).get("extra") if isinstance(config.get("platforms"), dict) else None,
        ]
        for mapping in feishu_mappings:
            if not isinstance(mapping, dict):
                continue
            app_token = app_token or str(
                mapping.get("bitable_app_token") or mapping.get("app_token") or mapping.get("base_token") or ""
            ).strip()
            table_id = table_id or str(
                mapping.get("bitable_table_id") or mapping.get("table_id") or ""
            ).strip()
    except (OSError, UnicodeError, yaml.YAMLError):
        pass
    if not app_token or not table_id:
        raise RuntimeError(
            "Feishu Bitable location is not configured. Set FEISHU_BITABLE_APP_TOKEN and "
            "FEISHU_BITABLE_TABLE_ID, or configure credential_pool_sync.bitable_app_token "
            "and credential_pool_sync.bitable_table_id in Hermes config.yaml."
        )
    return app_token, table_id

S_U = "⚠️ 不可用"
S_A = "✅ 正常"
S_I = "❌ 无效"
S_R = "⛔ 限流"
S_C = "🔄 检查中"

# A health-checked rebuild may replace the existing pool only when it retains
# at least this fraction, unless unavailable results are not the majority.
STANDARD_PROVIDERS = frozenset({'openai', 'anthropic', 'deepseek', 'moonshot', 'dashscope', 'z.ai'})
HERMES_CUSTOM_PROVIDER = "custom"

def _hermes_pool_key(provider):
    pk = str(provider or "").strip().lower().replace(" ", "-")
    if pk == HERMES_CUSTOM_PROVIDER:
        return HERMES_CUSTOM_PROVIDER
    if pk not in STANDARD_PROVIDERS:
        pk = f"custom:{pk}"
    return pk

def _strip_version_suffix(url):
    u = url.rstrip("/")
    if u.endswith("/v1") or u.endswith("/v3"):
        return u[:-3].rstrip("/")
    return u

MIN_POOL_RETENTION_RATIO = 0.5

def get_agent_name():
    """获取 Hermes Agent 名称，优先从 config.yaml 读取，其次从 hostname 映射。"""
    try:
        import yaml

        with open(get_runtime_config_path(), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        name = config.get("agent", {}).get("name", "")
        if name:
            return name
    except Exception:
        pass

    import socket

    hostname = socket.gethostname()
    mapping = {"上款电脑": "周公瑾", "87": "周公瑾", "200": "甘宁", "50": "郭奉孝"}
    return mapping.get(hostname, f"unknown-{hostname}")

def agents(s):
    """从状态字符串提取 agent 名单。"""
    match = re.fullmatch(r"🔄 (.+)使用中", s or "")
    return match.group(1).split("+") if match else []

def status_add(s, name):
    """添加 agent 到状态。"""
    names = "+".join(dict.fromkeys(agents(s) + [name]))
    return f"🔄 {names}使用中"

def status_remove(s, name):
    """从状态移除 agent。"""
    names = [agent for agent in agents(s) if agent != name]
    return f"🔄 {'+'.join(names)}使用中" if names else S_A

def health_status(is_valid, status, error=None):
    """Map a probe result to the health field; ownership never belongs here."""
    if is_valid:
        return S_A
    detail = f"{status or ''} {error or ''}"
    if status == S_R or "限流" in detail or "额度" in detail:
        return S_R
    if status == S_I or "401" in detail or "403" in detail or "无效" in detail:
        return S_I
    return S_U

def gt():
    app_id, app_secret = load_feishu_credentials()
    d = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    r = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", data=d, headers={"Content-Type": "application/json"})
    result = request_with_retry(r, timeout=10)
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {result}")
    return result["tenant_access_token"]

def gr(t):
    base_token, table_id = load_bitable_ids()
    h = {"Authorization": f"Bearer {t}"}; a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
        r = request_with_retry(urllib.request.Request(u, headers=h), timeout=15)
        a.extend(r["data"]["items"]); pt = r["data"].get("page_token", "")
        if not r["data"].get("has_more"): break
    return a

_UNSET = object()

def us(t, rid, s=None, note=None, provider=None, base_url=None):
    base_token, table_id = load_bitable_ids()
    h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
    f = {}
    if s is not None:
        f["状态"] = s
    if note is not None:
        f["备注"] = note

    if provider is not None:
        f["Provider"] = provider
    if base_url is not None:
        f["Base URL"] = base_url
    if not f:
        return
    request_with_retry(urllib.request.Request(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records/{rid}", data=json.dumps({"fields": f}).encode(), headers=h, method="PUT"), timeout=10)

def endpoint_candidates(base_url):
    """Return protocol paths for exactly one configured credential route.

    A credential's Plan route is part of its identity.  Do not strip a
    ``/v1`` or ``/v3`` suffix and then probe another Plan/version: doing so
    can use Coding-Plan's response to classify Agent-Plan (or vice versa).
    """
    base = _strip_endpoint_suffix(base_url)
    candidates = [f"{base}/chat/completions", f"{base}/messages"]
    # MiniMax and similar providers use /anthropic path which 404s on both
    # /chat/completions and /messages; add /v1/chat/completions as fallback
    base_l = base.lower()
    if base_l.endswith("/anthropic"):
        root = base[:-len("/anthropic")].rstrip("/")
        candidates.append(f"{root}/v1/chat/completions")
    elif not re.search(r"/v\d+$", base_l):
        candidates.append(f"{base}/v1/chat/completions")
    return candidates


def _strip_endpoint_suffix(value):
    value = str(value or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/messages"):
        if value.lower().endswith(suffix):
            return value[:-len(suffix)].rstrip("/")
    return value


def endpoint_base_url(endpoint):
    """Convert a probed endpoint back to the base URL stored by Hermes."""
    value = str(endpoint or "").rstrip("/")
    for suffix in ("/chat/completions", "/messages"):
        if value.lower().endswith(suffix):
            return value[:-len(suffix)].rstrip("/")
    return value


def detect_provider(base_url):
    """Infer the provider from a URL when the Feishu provider field is empty."""
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    mapping = (
        ("ark.cn-beijing.volces.com", "ARK"),
        ("open.bigmodel.cn", "Z.AI"),
        ("api.openai.com", "OPENAI"),
        ("api.anthropic.com", "ANTHROPIC"),
        ("api.deepseek.com", "DEEPSEEK"),
        ("api.moonshot.cn", "MOONSHOT"),
        ("dashscope.aliyuncs.com", "DASHSCOPE"),
        ("api.minimaxi.com", "MINIMAX"),
        ("api.minimax.chat", "MINIMAX"),
        ("xiaomimimo.com", "XIAOMI"),
    )
    for marker, provider in mapping:
        if marker in host:
            return provider
    return None


def try_url_variants(base_url):
    """Keep health checks on the exact route stored in Feishu.

    Endpoint healing is intentionally not performed during health checks:
    changing ``api/plan`` into ``api/coding`` makes status attribution
    unreliable.  Route corrections must be made explicitly in Feishu.

    When the stored base has no explicit version segment (``/v1``, ``/v3``,
    ...), also probe a ``/v1``-prefixed variant: many providers only serve
    their API under a versioned path.
    """
    base = _strip_endpoint_suffix(base_url)
    if not base:
        return []
    variants = [base]
    # 匹配任意版本段（/v1、/v3、/v2/...），不只限定 1 和 3
    if not re.search(r"/v\d+(?:/|$)", base.lower()):
        variants.append(f"{base}/v1")
    return variants


def model_limits(model_name):
    """Return model_config for models with known context length issues."""
    name = str(model_name or "").strip().lower()
    if name in ("glm-4.6v", "glm-4.5-air"):
        return {"max_tokens": 4096, "context_length": 32768}
    return {}


def tk(p, ak, bu, m):
    if not ak or not bu:
        return False, S_I, "缺少必填", ""
    candidates = [endpoint for variant in try_url_variants(bu) for endpoint in endpoint_candidates(variant)]
    last_error = None
    last_status = S_U
    for endpoint in candidates:
        anthropic = endpoint.endswith("/messages")
        model = m or ("claude-sonnet-4-20250514" if anthropic else "deepseek-v4-flash")
        if anthropic:
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            if "longcat" in endpoint.lower():
                headers["Authorization"] = f"Bearer {ak}"
            else:
                headers["x-api-key"] = ak
            payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]}
        else:
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {ak}"}
            payload = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}
        request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                code = response.getcode()
                body = response.read()
            if 200 <= code < 300:
                # 某些 Provider（如 ARK）会在 200 响应体里返回账号级错误码
                try:
                    data = json.loads(body)
                    err_code = None
                    err_msg = ""
                    if isinstance(data, dict):
                        err = data.get("error")
                        if isinstance(err, dict):
                            err_code = err.get("code")
                            err_msg = err.get("message", "")
                        err_code = err_code or data.get("code")
                        err_msg = err_msg or data.get("message", "")
                    if err_code in ("SetLimitExceeded", "RateLimitExceeded", "LimitExceeded", "RateLimit"):
                        return False, S_R, f"HTTP {code}: 账号级 {err_code}", endpoint.rstrip("/")
                except Exception:
                    pass
                return True, S_A, None, endpoint.rstrip("/")
            if code == 429:
                return False, S_R, "HTTP 429: rate limited", endpoint.rstrip("/")
            if code in (401, 403):
                return False, S_I, f"HTTP {code}: Key invalid", endpoint.rstrip("/")
            last_error = f"HTTP {code}"
            continue
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return False, S_R, "额度已用完", endpoint.rstrip("/")
            if exc.code in (401, 403):
                # 非 Anthropic/longcat 的 /messages 端点返回 401/403 属于端点不匹配，
                # 直接跳过并保留更早的错误信息（如 404 模型不存在）
                if endpoint.endswith("/messages") and "anthropic" not in endpoint.lower() and "longcat" not in endpoint.lower():
                    continue
                return False, S_I, "Key 无效", endpoint.rstrip("/")
            if exc.code == 404:
                # A 404 means the endpoint path does not exist. Try the next
                # candidate endpoint instead of immediately marking the
                # credential as S_U. If all candidates 404, the final return
                # at the end of the loop will report S_U with the last error.
                last_error = "HTTP 404"
                continue
            if exc.code == 405:
                last_error = "HTTP 405"
                continue
            if 400 <= exc.code < 500:
                # 这些 4xx 可能是端点/版本不匹配（如命中 /v3 而服务端只提供
                # /v1，或 plan 后缀错误），记录错误并继续尝试后续候选端点，
                # 而不是立即把凭据标记为 S_U。
                last_error = f"HTTP {exc.code}"
                continue
            return False, S_U, f"HTTP {exc.code}: 服务暂不可用", endpoint.rstrip("/")
        except (urllib.error.URLError, OSError) as exc:
            last_error = f"连接失败: {str(exc)[:80]}"
            continue
        except Exception as exc:
            return False, S_U, f"异常: {str(exc)[:80]}", endpoint.rstrip("/")
    return False, last_status, last_error or "无可用端点", candidates[-1].rstrip("/")


@contextmanager
def locked_path(target, timeout=10):
    """Lock target's sibling .lock file for a read-modify-replace operation."""
    target = Path(target)
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as lock_file:
        if lock_file.seek(0, os.SEEK_END) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
                time.sleep(0.1)
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(s):
    return _ANSI_RE.sub("", s)

def _parse_fallback_list(output):
    """Parse `hermes fallback list` output into a list of fallback entries."""
    entries = []
    pattern = re.compile(
        r"^\s*(\d+)\.\s+(?P<model>.+?)\s+\(via\s+(?P<provider>[^)]+)\)(?:\s+\[(?P<base_url>[^\]]+)\])?\s*$"
    )
    for raw in output.splitlines():
        line = _strip_ansi(raw)
        m = pattern.match(line)
        if not m:
            continue
        base = m.group("base_url")
        entries.append({
            "index": int(m.group(1)) - 1,
            "model": m.group("model").strip(),
            "provider": m.group("provider").strip(),
            "base_url": base.strip().lower().rstrip("/") if base else None,
        })
    return entries

def cleanup_fallback_chain(records, health_results=None):
    """Remove fallback providers missing from Feishu or failing health checks."""
    print(f"\n{'='*50}\n🧹 清理 fallback chain\n{'='*50}")
    feishu_base_urls = set()
    for r in records:
        fields = r.get("fields") or {}
        bu = str(fields.get("Base URL", "") or "").strip().lower().rstrip("/")
        if bu:
            feishu_base_urls.add(bu)

    try:
        res = subprocess.run(["hermes", "fallback", "list"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        print("  ⚠️  hermes 命令未找到，跳过 fallback 清理")
        return
    except Exception as e:
        print(f"  ⚠️  获取 fallback chain 失败: {e}")
        return

    if res.returncode != 0:
        print(f"  ⚠️  无法获取 fallback chain: {res.stderr.strip()}")
        return

    entries = _parse_fallback_list(res.stdout)
    if not entries:
        print("  无 fallback 配置")
        return

    failed_health = set()
    if health_results is not None:
        for identity, result in health_results.items():
            if not result or result[0]:
                continue
            if not isinstance(identity, tuple) or len(identity) < 3:
                continue
            _, base_url, model = identity[:3]
            failed_health.add(
                (
                    str(base_url or "").strip().lower().rstrip("/"),
                    str(model or "").strip(),
                )
            )

    stale = [
        e for e in entries
        if e["base_url"] and (
            e["base_url"] not in feishu_base_urls
            or (e["base_url"], e["model"]) in failed_health
        )
    ]
    if not stale:
        print("  fallback chain 与飞书记录一致")
        return

    for e in sorted(stale, key=lambda x: x["index"], reverse=True):
        print(f"  🗑️  移除 stale fallback #{e['index']+1}: {e['model']} [{e['base_url']}]")
        try:
            rm = subprocess.run(
                ["hermes", "fallback", "remove"],
                input=f"{e['index']+1}\n",
                text=True,
                capture_output=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as e2:
            print(f"    ❌ 移除失败: {e2}")
            continue
        if rm.returncode != 0:
            print(f"    ❌ 移除失败: {rm.stderr.strip()}")

    print("  fallback chain 清理完成")
    
    # 清理 429 配置


# 视觉模型关键词（max_tokens 限制 1024，不适合文本对话）
_VISION_KEYWORDS = ("glm-4v", "glm-4.6v", "vision", "-v-flash", "vl-")

# 已知无效模型 (ARK 上不存在或返回 404)
_INVALID_MODELS = frozenset({
    "deepseek-r1-distill-qwen-7b-250120",  # ARK 上不存在此模型
})


def sync_fallback_providers(raw_records, health_results=None, healed_urls=None):
    """同步 fallback_providers：将飞书表格中所有非视觉文本模型加入 fallback 链。

    核心原则：
    - ARK 只是来源，不是账号，每个模型的容量是独立的
    - 飞书里每条记录的容量都是独立的
    - 所有非视觉模型都应加入 fallback 链，不遗漏
    - GPT 放最后（quota 经常耗尽）
    """
    healed_urls = healed_urls or {}
    entries = []
    seen = set()
    gpt_entries = []
    
    # 读取当前主模型，避免重复添加
    current_main_model = None
    try:
        with open(get_runtime_config_path(), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        current_model = config.get("model") or {}
        if isinstance(current_model, dict):
            current_main_model = identity(
                current_model.get("api_key", ""),
                current_model.get("base_url", ""),
                current_model.get("default", ""),
            )
    except Exception:
        pass

    for r in raw_records:
        rec = _normalise_record(r)
        if rec is None:
            continue
        model = rec.get("model", "")
        base_url = rec.get("base_url", "")
        api_key = rec.get("api_key", "")
        original_identity = identity(api_key, base_url, model)
        if original_identity in healed_urls:
            base_url = healed_urls[original_identity]
        
        # 跳过视觉模型
        name_lower = model.lower()
        if any(v in name_lower for v in _VISION_KEYWORDS):
            continue
        
        # 跳过已知无效模型
        if name_lower in _INVALID_MODELS:
            continue
        
        # 跳过健康检查确认无效或限流的模型
        identity_key = identity(api_key, base_url, model)
        if health_results is not None:
            result = health_results.get(identity_key)
            if result and not result[0]:
                status = result[1]
                # Do not put a route with a confirmed model/access 404 into
                # fallback.  It cannot recover through a retry and must not
                # be mislabeled as an invalid key.
                if status == S_I or status == S_R or "HTTP 404" in str(result[2] or ""):
                    continue
            
        # 跳过当前主模型（避免重复）
        if current_main_model and identity_key == current_main_model:
            continue

        # 去重: (model, base_url, api_key)
        key = (model.lower(), base_url, api_key)
        if key in seen:
            continue
        seen.add(key)

        entry = {
            "provider": HERMES_CUSTOM_PROVIDER,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        }

        # GPT 放最后（quota 经常耗尽）
        if "openai.com" in base_url:
            gpt_entries.append(entry)
        else:
            entries.append(entry)

    entries.extend(gpt_entries)

    # 重新读取 config.yaml（避免覆盖其他进程的修改）并写入 fallback_providers
    runtime_config = get_runtime_config_path()
    with locked_path(runtime_config):
        with open(runtime_config, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        old_count = len(config.get("fallback_providers") or [])
        config["fallback_providers"] = entries

        tmp = runtime_config.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, runtime_config)
        finally:
            if tmp.exists():
                tmp.unlink()

    if len(entries) != old_count:
        print(f"  📈 fallback_providers: {old_count} → {len(entries)} (含 {len(gpt_entries)} GPT)")
    else:
        print(f"  ✅ fallback_providers: {len(entries)} 个模型 (含 {len(gpt_entries)} GPT)")


def cleanup_custom_providers():
    """Remove custom provider endpoints that are no longer used by the runtime model or fallback chain."""
    runtime_config = get_runtime_config_path()
    with locked_path(runtime_config):
        with open(runtime_config, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            return

        active_base_urls = set()

        current_model = config.get("model") or {}
        if isinstance(current_model, dict):
            base_url = str(current_model.get("base_url", "") or "").strip().lower().rstrip("/")
            if base_url:
                active_base_urls.add(base_url)

        fallback_providers = config.get("fallback_providers") or []
        if isinstance(fallback_providers, list):
            for entry in fallback_providers:
                if not isinstance(entry, dict):
                    continue
                base_url = str(entry.get("base_url", "") or "").strip().lower().rstrip("/")
                if base_url:
                    active_base_urls.add(base_url)

        active_stripped = {_strip_version_suffix(url) for url in active_base_urls}

        custom_providers = config.get("custom_providers") or []
        if not isinstance(custom_providers, list):
            return

        kept = []
        removed = 0
        for entry in custom_providers:
            if not isinstance(entry, dict):
                removed += 1
                continue
            base_url = str(entry.get("base_url", "") or "").strip().lower().rstrip("/")
            if base_url and base_url not in active_base_urls and _strip_version_suffix(base_url) not in active_stripped:
                removed += 1
                print(f"  🗑️  custom_provider: {entry.get('name', '?')} [{base_url}] — stale")
            else:
                kept.append(entry)

        if removed:
            config["custom_providers"] = kept
            tmp = runtime_config.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
            try:
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, runtime_config)
            finally:
                if tmp.exists():
                    tmp.unlink()
            print(f"  📦 custom_providers: {len(kept) + removed} → {len(kept)} (removed {removed} stale)")
        else:
            print(f"  ✅ custom_providers: {len(custom_providers)} 条，无过期条目")


def update_runtime_main_model(record):
    path = get_runtime_config_path()
    with locked_path(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            raise ValueError("config.yaml 顶层必须是映射")

        # The runtime model is always the Hermes custom provider.
        provider = HERMES_CUSTOM_PROVIDER

        model_name = str(record.get("model") or "").strip()
        base_url = normalise_base_url(record.get("base_url"))
        api_key = str(record.get("api_key") or "").strip()
        if not model_name:
            raise ValueError("record 缺少 model 字段")
        if not base_url:
            raise ValueError("record 缺少 base_url 字段")
        if not api_key:
            raise ValueError("record 缺少 api_key 字段")

        model = {
            "default": model_name,
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
        }

        limits = model_limits(model_name)
        if limits:
            existing_mc = (config.get("model") or {}).get("model_config") or {}
            existing_mc.update(limits)
            model["model_config"] = existing_mc
        existing = config.get("model") or {}
        if isinstance(existing, dict):
            existing.update(model)
            config["model"] = existing
        else:
            config["model"] = model
        temp = path.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
    return path


def priority(value):
    """Normalised 0-9. Out-of-range / non-integer -> 9 (lowest) + warning.
    0 is highest priority, 9 is lowest. Never drops a record; never promotes it."""
    try:
        p = int(value)
    except (TypeError, ValueError):
        print(f"WARNING: 非法优先级 {value!r}，按档 9 处理", file=sys.stderr)
        return 9
    if not (0 <= p <= 9):
        print(f"WARNING: 优先级 {p} 越界，按档 9 处理", file=sys.stderr)
        return 9
    return p


def normalise_base_url(value):
    """Return the canonical Hermes base URL for a credential record."""
    base_url = str(value or "").strip().lower().rstrip("/")
    if base_url == "https://ark.cn-beijing.volces.com/api/plan":
        return "https://ark.cn-beijing.volces.com/api/plan/v1"
    return base_url


def identity(api_key, base_url, model):
    """Unified credential identity: (api_key, normalised_base_url, lowered_model).

    All identity comparisons across sync, fallback, switch, and cleanup scripts
    MUST use this function to avoid format mismatches.
    """
    return (
        str(api_key or "").strip(),
        normalise_base_url(base_url),
        str(model or "").strip().lower(),
    )


def _normalise_record(record):
    fields = record.get("fields") or {}
    provider = str(fields.get("Provider", "") or "").strip()
    label = str(fields.get("Label", "") or "").strip()
    api_key = str(fields.get("API Key", "") or "").strip()
    base_url = normalise_base_url(fields.get("Base URL", ""))
    model = str(fields.get("模型", "") or "").strip()
    pr = priority(fields.get("优先级", ""))
    # glm-5-2 在 ARK 上必须使用 /api/v3 端点
    if model.lower().startswith("glm-5-2") and base_url == "https://ark.cn-beijing.volces.com/api":
        base_url = "https://ark.cn-beijing.volces.com/api/v3"
    # 反推 Provider：如果 Feishu 的 Provider 字段为空或为 "custom"，但从 URL 能推断出标准 Provider，则覆盖
    original_provider = provider
    inferred = detect_provider(base_url)
    if inferred and (not provider or provider.lower() == "custom"):
        provider = inferred
    if not api_key or api_key == "***":
        return None
    return {
        "record_id": record.get("record_id", ""),
        "label": label,
        "provider": provider,
        "original_provider": original_provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "priority": pr,
        "status": str(fields.get("状态", "") or "").strip(),
    }

def group_by_priority(records):
    """按 0-9 分档归一化后的凭证记录，返回 {tier: [rec,...]}。

    0 档优先级最高，9 档最低。空档位保留空列表。
    """
    groups = {tier: [] for tier in range(10)}
    for rec in records:
        groups[rec["priority"]].append(rec)
    return groups


def collect_active_tier(records_by_tier, skip_health_rotate=False, *, current_identity, agent_name):
    """从档 0 起逐档健康检查，返回第一个含 ≥1 有效凭证的档。

    - 只健康检查 0..active 档；active 之上的档完全不检查、不读取。
    - 0 档优先级最高；只要该档存在至少一个有效凭证就只加载该档。
    - skip_health_rotate 时所有记录视为有效，active_tier = 最低的非空档。

    返回 (active_tier_or_None, active_valid_records, health_results,
          pending_updates, healed_urls, url_updates)。
    """
    health_results = {} if not skip_health_rotate else None
    pending_updates = []
    healed_urls = {}
    url_updates = []
    active_tier = None
    active_valid_records = []

    for tier in range(10):
        tier_records = records_by_tier.get(tier, [])
        if not tier_records:
            continue
        valid_in_tier = []
        for normalised in tier_records:
            p = normalised["provider"]
            l = normalised["label"]
            ak = normalised["api_key"]
            bu = normalised["base_url"]
            m = normalised["model"]
            rid = normalised.get("record_id", "")
            original_provider = str(normalised.get("original_provider") or "").strip()
            detected_provider = detect_provider(bu)
            if skip_health_rotate:
                iv, s, e, _used_url = True, S_A, None, ""
            else:
                print(f"\n  🔍 [{l or p}] ...", end=" ")
                iv, s, e, _used_url = tk(p, ak, bu, m)
                if iv:
                    healed_url = endpoint_base_url(_used_url)
                    if healed_url and healed_url != bu:
                        healed_urls[identity(ak, bu, m)] = healed_url
                        url_updates.append((rid, detected_provider or p, healed_url))
                        normalised["base_url"] = healed_url
                        bu = healed_url
                    if detected_provider and (
                        not original_provider or original_provider.lower() == "custom"
                    ):
                        url_updates.append((rid, detected_provider, bu))
                        normalised["provider"] = detected_provider
                        p = detected_provider
            if health_results is not None:
                health_results[identity(ak, bu, m)] = (iv, s, e, _used_url)
            if s == S_R:
                if not skip_health_rotate:
                    print(f"⛔ {s}")
                    new_status = health_status(False, s, e)
                    h_note = e or "额度已用完"
                    pending_updates.append((rid, new_status, h_note))
                continue
            if iv:
                if not skip_health_rotate:
                    print(f"✅ {s}")
                    # 状态栏显示 Agent 使用信息，备注保留原始说明
                    if identity(ak, bu, m) == current_identity:
                        new_status = status_add(normalised.get("status") or "", agent_name)
                    else:
                        new_status = status_remove(normalised.get("status") or "", agent_name)
                    h_note = "验证通过"
                    pending_updates.append((rid, new_status, h_note))
                valid_in_tier.append(normalised)
            else:
                # 健康检查失败时，设置正确的健康状态，从状态栏移除当前 Agent
                new_status = health_status(False, s, e)
                h_note = e or s
                if "404" in str(e or ""):
                    h_note = "HTTP 404: model or account entitlement unavailable"
                print(f"❌ {s}")
                pending_updates.append((rid, new_status, h_note))
            if not skip_health_rotate:
                time.sleep(0.3)
        if valid_in_tier:
            active_tier = tier
            active_valid_records = valid_in_tier
            break

    return (active_tier, active_valid_records, health_results, pending_updates, healed_urls, url_updates)


def _read_existing_auth():
    """Read auth.json safely and retain unrelated top-level settings."""
    minimal = {"version": 1, "providers": {}, "credential_pool": {}}
    try:
        existing = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"WARNING: auth.json unreadable; rebuilding ({type(exc).__name__})", file=sys.stderr)
        return minimal
    if not isinstance(existing, dict):
        print("WARNING: auth.json root is not an object; rebuilding", file=sys.stderr)
        return minimal
    existing.setdefault("version", 1)
    existing.setdefault("providers", {})
    return existing


def _sync_unlocked(skip_health_rotate=False):
    print("="*50); print("凭证池同步 v7.17.0"); print("="*50)
    tok = gt()
    rs = gr(tok); print(f"\n📋 飞书: {len(rs)} 条")
    pending_updates = []
    url_updates = []
    healed_urls = {}
    agent_name = get_agent_name()
    with open(get_runtime_config_path(), encoding="utf-8") as handle:
        current_config = yaml.safe_load(handle) or {}
    current_model = current_config.get("model") or {}
    if not isinstance(current_model, dict):
        current_model = {}
    current_identity = identity(
        current_model.get("api_key", ""),
        current_model.get("base_url", ""),
        current_model.get("default", ""),
    )
    # Clean stale markers for this agent from all records before re-applying
    if not skip_health_rotate:
        for r in rs:
            f = r.get("fields") or {}
            rid = r.get("record_id", "")
            if rid and agent_name in agents(f.get("状态", "")):
                cleaned = status_remove(f.get("状态", ""), agent_name)
                if cleaned != f.get("状态", ""):
                    pending_updates.append((rid, cleaned, None))

    # 归一化所有记录并按优先级分档（0-9，0 最高）
    normalised_all = []
    for r in rs:
        f = r.get("fields") or {}
        rid = r.get("record_id", "")
        normalised = _normalise_record(r)
        if normalised is None:
            label = str(f.get("Label", "") or f.get("Provider", "") or "").strip()
            print(f"\n  ⏭️  [{label}] 跳过")
            if not skip_health_rotate and rid:
                pending_updates.append((rid, S_I, None))
            continue
        normalised_all.append(normalised)
    records_by_tier = group_by_priority(normalised_all)

    # 逐档健康检查：只加载优先级最高且存在有效凭证的档（0 档最高）
    active_tier, active_valid_records, health_results, tier_pending, healed_urls, url_updates = collect_active_tier(
        records_by_tier,
        skip_health_rotate=skip_health_rotate,
        current_identity=current_identity,
        agent_name=agent_name,
    )
    pending_updates.extend(tier_pending)

    # 只由 active 档有效凭证构建 credential_pool 与 output_records
    fe = {}
    output_records = list(active_valid_records)
    for normalised in active_valid_records:
        p = normalised["provider"]
        l = normalised["label"]
        m = normalised["model"]
        bu = normalised["base_url"]
        ak = normalised["api_key"]
        pr = normalised["priority"]
        rid_full = normalised.get("record_id", "")
        eid = f"sync-{rid_full}" if rid_full else f"sync-{uuid.uuid4().hex[:12]}"
        pk = _hermes_pool_key(p)
        fe.setdefault(pk, []).append({"id": eid, "label": l or m, "provider": HERMES_CUSTOM_PROVIDER, "model": m, "auth_type": "api_key", "priority": pr, "source": f"manual:{ak[:12]}...", "access_token": ak, "api_key": ak, "last_status": "active", "base_url": bu, "request_count": 0, "secret_fingerprint": f"sha256:{eid}"})

    vc = len(active_valid_records)
    r_limit = ic = 0
    if health_results is not None:
        for result in health_results.values():
            if not result[0] and result[1] == S_R:
                r_limit += 1
            elif not result[0]:
                ic += 1
    print(f"\n{'='*50}\n✅ {vc} 有效 | ⛔ {r_limit} 限流 | ❌ {ic} 无效")
    AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    with locked_path(AUTH_JSON):
        ex = _read_existing_auth()
        existing_pool = ex.get("credential_pool") or {}
        old_pool_count = (
            sum(len(entries) for entries in existing_pool.values() if isinstance(entries, list))
            if isinstance(existing_pool, dict)
            else 0
        )

        candidate_pool = {}
        for pv, es in fe.items():
            es.sort(key=lambda x: x.get("priority", 99))
            candidate_pool[pv] = es
        new_pool_count = sum(len(entries) for entries in candidate_pool.values())

        # preserve_existing_pool 判定沿用 active 档的 checked/unavailable 计数
        checked_count = len(health_results) if health_results is not None else 0
        unavailable_count = (
            sum(
                1
                for result in health_results.values()
                if not result[0] and result[1] == S_U
            )
            if health_results is not None
            else 0
        )
        preserve_existing_pool = (
            old_pool_count > 0
            and new_pool_count < old_pool_count * MIN_POOL_RETENTION_RATIO
            and unavailable_count > checked_count * 0.5
        )

        if preserve_existing_pool:
            print(
                f"WARNING: 本次健康检查有 {unavailable_count}/{checked_count} 条不可用，"
                f"候选凭证池将从 {old_pool_count} 缩减到 {new_pool_count}；"
                "疑似网络故障，保留现有 credential_pool",
                file=sys.stderr,
            )
        else:
            ex["credential_pool"] = candidate_pool
            ex["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        temp_auth = AUTH_JSON.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
        try:
            temp_auth.write_text(json.dumps(ex, ensure_ascii=False, indent=2), encoding="utf-8")
            with open(temp_auth, "r+b") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_auth, AUTH_JSON)
        finally:
            if temp_auth.exists():
                temp_auth.unlink()
    # 同步 fallback_providers：只加载 active 档的非视觉模型
    active_raw_records = []
    if active_tier is not None:
        for r in rs:
            fields = r.get("fields") or {}
            if priority(fields.get("优先级", "")) == active_tier:
                active_raw_records.append(r)
    sync_fallback_providers(active_raw_records, health_results, healed_urls)
    # 清理 custom_providers 中的过期端点（fallback 已收敛到 active 档，不会误删高档 provider）
    if not skip_health_rotate:
        cleanup_custom_providers()
    if not skip_health_rotate:
        # clear_current(tok) 已移除：状态栏由每个记录自己维护
        for record_id, new_status, note in pending_updates:
            if note is None or note == "":
                us(tok, record_id, new_status)
            else:
                us(tok, record_id, new_status, note=note)
        for record_id, provider, base_url in url_updates:
            us(tok, record_id, provider=provider, base_url=base_url)
    if fe:
        print(f"\n\n📝 auth.json: {list(ex['credential_pool'].keys())}, 共 {sum(len(v) for v in ex['credential_pool'].values())} 个")
    elif preserve_existing_pool:
        print(f"\n\n⚠️ 无有效凭证，保留现有 credential_pool（{old_pool_count} 个）")
    else:
        print("\n\n⚠️ 无有效凭证，已清空 credential_pool")
    if not skip_health_rotate:
        cleanup_fallback_chain(active_raw_records, health_results)
    print(f"\n{'='*50}\n✅ 同步完成\n{'='*50}")
    output_records.sort(key=lambda item: item["priority"])
    current_in_pool = any(
        identity(record.get("api_key", ""), record.get("base_url", ""), record.get("model", "")) == current_identity
        for record in output_records
    )
    if not current_in_pool:
        # 主模型切换候选只从 active 档选
        candidates = [
            record for record in output_records
            if identity(record.get("api_key", ""), record.get("base_url", ""), record.get("model", "")) != current_identity
        ]
        if candidates:
            # 取第一个候选（已按priority排序）自动切换
            target = candidates[0]
            try:
                update_runtime_main_model(target)
                print(f"🔄 主模型已自动切换: {target.get('label') or target['model']}")
            except Exception as exc:
                print(f"WARNING: 自动切换失败: {exc}", file=sys.stderr)
                print(
                    'WARNING: 当前主模型不在最终有效凭证池中，'
                    '建议运行 scripts/switch_next.py 切换到下一个可用凭证',
                    file=sys.stderr,
                )
            else:
                # 回写飞书状态：新目标。与主模型切换相互独立——状态回写失败
                # 不应被误报为切换失败。
                if not skip_health_rotate and target.get('record_id'):
                    try:
                        token = gt()
                        records_list = gr(token)
                        new_r = next(
                            (r for r in records_list if r["record_id"] == target["record_id"]),
                            None
                        )
                        if new_r:
                            current_status = new_r.get("fields", {}).get("状态", "")
                            new_status = status_add(current_status, agent_name)
                            existing_note = new_r.get("fields", {}).get("备注") or ""
                            note = None if existing_note.strip() else "验证通过"
                            us(token, target["record_id"], new_status, note=note)
                    except Exception as exc:
                        print(f"WARNING: 回写飞书状态失败: {exc}", file=sys.stderr)
        else:
            print(
                'WARNING: 当前主模型不在最终有效凭证池中，'
                '建议运行 scripts/switch_next.py 切换到下一个可用凭证',
                file=sys.stderr,
            )
    print("__RECORDS__" + json.dumps(output_records, ensure_ascii=False, separators=(",", ":")))


def sync(skip_health_rotate=False):
    """Run one synchronization workflow at a time."""
    workflow_lock = Path(__file__).with_suffix(".workflow.lock")
    with locked_path(workflow_lock, timeout=120):
        return _sync_unlocked(skip_health_rotate)


def mark_runtime_failure(failure_kind):
    """Write a Hermes runtime failure to the one matching Feishu record.

    Runtime identity is supplied by the gateway through environment variables
    so a fallback route is never confused with the globally configured primary
    route.  Model + exact Base URL must identify exactly one record; otherwise
    this function fails closed rather than writing a misleading status.
    """
    model = str(os.environ.get("HERMES_FAILURE_MODEL", "") or "").strip().lower()
    base_url = normalise_base_url(os.environ.get("HERMES_FAILURE_BASE_URL", ""))
    if not model or not base_url:
        config = yaml.safe_load(get_runtime_config_path().read_text(encoding="utf-8")) or {}
        current = config.get("model") or {}
        model = str(current.get("default", "") or "").strip().lower()
        base_url = normalise_base_url(current.get("base_url", ""))
    if not model or not base_url:
        raise ValueError("runtime failure identity is missing model or base_url")

    token = gt()
    matches = []
    for raw in gr(token):
        record = _normalise_record(raw)
        if not record:
            continue
        if record["model"].strip().lower() == model and normalise_base_url(record["base_url"]) == base_url:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(f"runtime failure identity matched {len(matches)} Feishu records")

    rate_limited = failure_kind in {"rate_limit", "billing", "upstream_rate_limit"}
    status = S_R if rate_limited else S_U
    note = f"Hermes runtime failure: {failure_kind}"
    us(token, matches[0]["record_id"], status, note=note)
    print(f"Marked runtime failure for {matches[0]['label'] or matches[0]['model']}: {failure_kind}")


def main():
    parser = argparse.ArgumentParser(description="同步飞书凭证池到 Hermes")
    parser.add_argument("--skip-health-rotate", action="store_true", help="跳过健康检查，直接同步")
    parser.add_argument("--mark-runtime-failure", metavar="KIND", help="mark the exact runtime route as failed")
    args = parser.parse_args()

    try:
        if args.mark_runtime_failure:
            mark_runtime_failure(args.mark_runtime_failure)
            return 0
        sync(args.skip_health_rotate)
    except KeyboardInterrupt:
        print("\n\n用户中断，同步已取消")
        return 1
    except OSError as exc:
        print(f"ERROR: 文件操作失败: {exc}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    raise SystemExit(main())
