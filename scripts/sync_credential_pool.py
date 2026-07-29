#!/usr/bin/env python3
"""凭证池同步脚本 v7.2.0 — 含连通性验证 + 状态回写 + 429自动切换 + 飞书状态管理优化"""
import argparse, json, os, sys, urllib.request, urllib.error, time, subprocess, re, msvcrt
import random
import uuid
import yaml
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

BASE_TOKEN = "YedtbFYKZatu2QsGti9ch7xbnGc"
TABLE_ID = "tblOSK9HexYVOHBW"
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

S_U = "⚠️ 不可用"
S_A = "✅ 正常"
S_I = "❌ 无效"
S_R = "⛔ 限流"
S_C = "🔄 检查中"

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
    if status == S_R or "429" in detail or "限流" in detail or "额度" in detail:
        return S_R
    if status == S_I or "401" in detail or "403" in detail or "无效" in detail:
        return S_I
    return S_U

def _usage_names(note):
    match = re.search(r"🔄\s+(.+?)使用中", str(note or ""))
    return match.group(1).split("+") if match else []

def usage_remove(note, name):
    names = [agent for agent in _usage_names(note) if agent != name]
    # 移除现有的使用信息标记
    detail = re.sub(r"\s*\|\s*🔄\s+.+?使用中", "", str(note or "")).strip(" |")
    # 重新构建备注，移除空字符串
    parts = [p.strip() for p in detail.split("|") if p.strip()]
    if names:
        return f"{' | '.join(parts)} | 🔄 {'+'.join(names)}使用中"
    else:
        return " | ".join(parts) if parts else ""

def usage_add(note, name):
    names = [agent for agent in _usage_names(note) if agent != name] + [name]
    # 移除现有的使用信息标记
    detail = re.sub(r"\s*\|\s*🔄\s+.+?使用中", "", str(note or "")).strip(" |")
    # 重新构建备注，移除空字符串
    parts = [p.strip() for p in detail.split("|") if p.strip()]
    marker = f"🔄 {'+'.join(dict.fromkeys(names))}使用中"
    if parts:
        return f"{' | '.join(parts)} | {marker}"
    else:
        return marker

def gt():
    app_id, app_secret = load_feishu_credentials()
    d = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    r = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", data=d, headers={"Content-Type": "application/json"})
    result = request_with_retry(r, timeout=10)
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {result}")
    return result["tenant_access_token"]

def gr(t):
    h = {"Authorization": f"Bearer {t}"}; a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
        r = request_with_retry(urllib.request.Request(u, headers=h), timeout=15)
        a.extend(r["data"]["items"]); pt = r["data"].get("page_token", "")
        if not r["data"].get("has_more"): break
    return a

_UNSET = object()

def us(t, rid, s=None, note=None):
    h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
    f = {}
    if s is not None:
        f["状态"] = s
    if note is not None:
        f["备注"] = note

    if not f:
        return
    request_with_retry(urllib.request.Request(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records/{rid}", data=json.dumps({"fields": f}).encode(), headers=h, method="PUT"), timeout=10)

def clear_current(token, keep_id=None):
    """从凭证状态中清除本机 Agent，保留其他 Agent。"""
    agent_name = get_agent_name()
    for r in gr(token):
        if r.get("record_id") != keep_id:
            status = r.get("fields", {}).get("状态", "")
            new_status = status_remove(status, agent_name)
            if new_status != status:
                us(token, r["record_id"], new_status)

def endpoint_candidates(base_url):
    base = str(base_url or "").strip().rstrip("/")
    suffixes = ("/v1/chat/completions", "/chat/completions", "/v1/messages", "/messages")
    for suffix in suffixes:
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)].rstrip("/")
            break
    return [f"{base}{suffix}" for suffix in suffixes]


def model_limits(model_name):
    """Return model_config for models with known context length issues."""
    name = str(model_name or "").strip().lower()
    if name in ("glm-4.6v", "glm-4.5-air"):
        return {"max_tokens": 4096, "context_length": 32768}
    return {}


def tk(p, ak, bu, m):
    if not ak or not bu:
        return False, S_I, "缺少必填", ""
    candidates = endpoint_candidates(bu)
    last_error = None
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
            request_with_retry(request, timeout=8, max_retries=2)
            return True, S_A, None, endpoint.rstrip("/")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                return False, S_R, "额度已用完", endpoint.rstrip("/")
            if exc.code in (401, 403):
                return False, S_I, "Key 无效", endpoint.rstrip("/")
            if exc.code in (404, 405):
                last_error = f"HTTP {exc.code}"
                continue
            if 400 <= exc.code < 500:
                return True, S_A, None, endpoint.rstrip("/")
            return False, S_U, f"HTTP {exc.code}: 服务暂不可用", endpoint.rstrip("/")
        except (urllib.error.URLError, OSError) as exc:
            last_error = f"连接失败: {str(exc)[:80]}"
            continue
        except Exception as exc:
            return False, S_U, f"异常: {str(exc)[:80]}", endpoint.rstrip("/")
    return False, S_U, last_error or "无可用端点", candidates[-1].rstrip("/")


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

def _health_is_429(health_result):
    """Check if health result indicates 429 quota exhaustion."""
    if not health_result:
        return False
    is_valid, status, error, _ = health_result
    return not is_valid and (status == S_R or "429" in str(error) or "限流" in str(error) or "额度" in str(error))

def _cleanup_429_fallback_config(health_results):
    """Remove 429 entries from config.yaml fallback_providers."""
    if not health_results:
        return
        
    print(f"\n{'='*50}\n🧹 清理 429 fallback 配置\n{'='*50}")
    
    runtime_config = get_runtime_config_path()
    with locked_path(runtime_config):
        with open(runtime_config, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        
        old_count = len(config.get("fallback_providers") or [])
        new_entries = []
        
        for entry in config.get("fallback_providers", []):
            identity = (entry.get("api_key", ""), entry.get("base_url", ""), entry.get("model", ""))
            if not _health_is_429(health_results.get(identity)):
                new_entries.append(entry)
        
        config["fallback_providers"] = new_entries
        
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
        
        if len(new_entries) != old_count:
            print(f"  📉 fallback_providers: {old_count} → {len(new_entries)} (移除 429 记录)")
        else:
            print(f"  📊 fallback_providers: {len(new_entries)} 个模型")

def cleanup_fallback_chain(records, health_results=None):
    """Remove fallback providers whose base_url no longer exists in Feishu records."""
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

    stale = [e for e in entries if e["base_url"] and e["base_url"] not in feishu_base_urls]
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
    _cleanup_429_fallback_config(health_results)


# 视觉模型关键词（max_tokens 限制 1024，不适合文本对话）
_VISION_KEYWORDS = ("glm-4v", "glm-4.6v", "vision", "-v-flash", "vl-")

# 已知无效模型 (ARK 上不存在或返回 404)
_INVALID_MODELS = frozenset({
    "deepseek-r1-distill-qwen-7b-250120",  # ARK 上不存在此模型
})


def sync_fallback_providers(raw_records, health_results=None):
    """同步 fallback_providers：将飞书表格中所有非视觉文本模型加入 fallback 链。

    核心原则：
    - ARK 只是来源，不是账号，每个模型的容量是独立的
    - 飞书里每条记录的容量都是独立的
    - 所有非视觉模型都应加入 fallback 链，不遗漏
    - GPT 放最后（quota 经常耗尽）
    """
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
            current_main_model = (
                str(current_model.get("api_key", "") or "").strip(),
                str(current_model.get("base_url", "") or "").strip().lower().rstrip("/"),
                str(current_model.get("default", "") or "").strip(),
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
        
        # 跳过视觉模型
        name_lower = model.lower()
        if any(v in name_lower for v in _VISION_KEYWORDS):
            continue
        
        # 跳过已知无效模型
        if name_lower in _INVALID_MODELS:
            continue
        
        # 跳过 429 的模型
        identity = (api_key, base_url, model)
        if health_results is not None and _health_is_429(health_results.get(identity)):
            continue
            
        # 跳过当前主模型（避免重复）
        if current_main_model and identity == current_main_model:
            continue

        # 去重: (model, base_url, api_key)
        key = (model.lower(), base_url, api_key)
        if key in seen:
            continue
        seen.add(key)

        entry = {
            "provider": "custom",
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


def _priority(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 99

def _normalise_record(record):
    fields = record.get("fields") or {}
    provider = str(fields.get("Provider", "") or "").strip()
    label = str(fields.get("Label", "") or "").strip()
    api_key = str(fields.get("API Key", "") or "").strip()
    base_url = str(fields.get("Base URL", "") or "").strip().lower().rstrip("/")
    # 修正: ARK api/plan 端点需要 /v1 后缀
    # OpenAI SDK 使用 base_url + /chat/completions, 但 api/plan/chat/completions 返回 404
    # api/plan/v1/chat/completions 返回 200
    if base_url == "https://ark.cn-beijing.volces.com/api/plan":
        base_url = "https://ark.cn-beijing.volces.com/api/plan/v1"
    model = str(fields.get("模型", "") or "").strip()
    priority = _priority(fields.get("优先级", ""))
    if not provider or not api_key or api_key == "***":
        return None
    return {
        "record_id": record.get("record_id", ""),
        "label": label,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "priority": priority,
        "status": str(fields.get("状态", "") or "").strip(),
    }

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


def sync(skip_health_rotate=False):
    print("="*50); print("凭证池同步 v7.2.0"); print("="*50)
    tok = gt()
    rs = gr(tok); print(f"\n📋 飞书: {len(rs)} 条")
    fe, vc, ic, output_records, pending_updates = {}, 0, 0, [], []
    health_results = {} if not skip_health_rotate else None
    agent_name = get_agent_name()
    with open(get_runtime_config_path(), encoding="utf-8") as handle:
        current_config = yaml.safe_load(handle) or {}
    current_model = current_config.get("model") or {}
    if not isinstance(current_model, dict):
        current_model = {}
    current_identity = (
        str(current_model.get("api_key", "") or "").strip(),
        str(current_model.get("base_url", "") or "").strip().lower().rstrip("/"),
        str(current_model.get("default", "") or "").strip(),
    )
    for r in rs:
        f = r.get("fields") or {}; rid = r.get("record_id", "")
        normalised = _normalise_record(r)
        if normalised is None:
            label = str(f.get("Label", "") or f.get("Provider", "") or "").strip()
            print(f"\n  ⏭️  [{label}] 跳过")
            if not skip_health_rotate and rid:
                pending_updates.append((rid, S_I, None))
            continue
        p = normalised["provider"]; l = normalised["label"]; ak = normalised["api_key"]
        bu = normalised["base_url"]; m = normalised["model"]; pr = normalised["priority"]
        # 注意: 不要修改 api/plan → api/plan/v3
        # 诊断证明 api/plan 配合 /v1/chat/completions 是正确的端点
        # api/plan/v3 反而导致 404
        if skip_health_rotate:
            iv, s, e, _used_url = True, S_A, None, ""
        else:
            print(f"\n  🔍 [{l or p}] ...", end=" ")
            iv, s, e, _used_url = tk(p, ak, bu, m)
        if health_results is not None:
            health_results[(ak, bu, m)] = (iv, s, e, _used_url)
        if iv:
            if not skip_health_rotate:
                print(f"✅ {s}")
                # 健康状态放字段，使用信息放备注
                new_status = health_status(True, s)
                h_note = usage_add(f.get("备注", ""), agent_name)
                pending_updates.append((rid, new_status, h_note))
            vc += 1
            output_records.append(normalised)
            rid_full = r.get("record_id", "")
            eid = f"sync-{rid_full}" if rid_full else f"sync-{uuid.uuid4().hex[:12]}"
            pk = f"custom:{p.strip().lower().replace(' ', '-')}"
            fe.setdefault(pk, []).append({"id": eid, "label": l or m, "provider": p, "model": m, "auth_type": "api_key", "priority": pr, "source": f"manual:{ak[:12]}...", "access_token": ak, "api_key": ak, "last_status": "active", "base_url": bu, "request_count": 0, "secret_fingerprint": f"sha256:{eid}"})
        else:
            # 健康检查失败时，设置正确的健康状态，移除使用信息
            new_status = health_status(False, s, e)
            h_note = usage_remove(f.get("备注", ""), agent_name)
            print(f"❌ {s}"); ic += 1
            pending_updates.append((rid, new_status, h_note))
        if not skip_health_rotate:
            time.sleep(0.3)
    print(f"\n{'='*50}\n✅ {vc} 有效 | ❌ {ic} 无效")
    AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    with locked_path(AUTH_JSON):
        ex = _read_existing_auth()
        ex["credential_pool"] = {}
        for pv, es in fe.items():
            es.sort(key=lambda x: x.get("priority", 99))
            ex["credential_pool"][pv] = es
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
    # 同步 fallback_providers：所有非视觉模型加入 fallback 链
    if health_results is not None and _health_is_429(health_results.get(current_identity)):
        # 主模型 429，自动切换到第一个健康的非视觉模型
        healthy_non_vision = sorted(
            (record for record in output_records
             if not any(v in record["model"].lower() for v in _VISION_KEYWORDS)),
            key=lambda item: item["priority"],
        )
        if healthy_non_vision:
            target = healthy_non_vision[0]
            old_model = current_identity[2]
            runtime_config = get_runtime_config_path()
            with locked_path(runtime_config):
                with open(runtime_config, encoding="utf-8") as handle:
                    config = yaml.safe_load(handle) or {}
                model_config = {
                    "default": target["model"],
                    "provider": "custom",
                    "base_url": target["base_url"].rstrip("/"),
                    "api_key": target["api_key"],
                }
                limits = model_limits(target["model"])
                if limits:
                    model_config["model_config"] = limits
                config["model"] = model_config
                tmp = runtime_config.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
                try:
                    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, runtime_config)
                finally:
                    if tmp.exists():
                        tmp.unlink()
            print(f"主模型 {old_model} -> {target['model']} (429自动切换)")
            
            # 更新新主模型的飞书状态为使用中
            target_record_id = None
            for record in output_records:
                if record["model"] == target["model"] and record["api_key"] == target["api_key"] and record["base_url"] == target["base_url"]:
                    target_record_id = record.get("record_id")
                    break
            
            if target_record_id:
                try:
                    # 获取当前状态并添加 agent 使用信息
                    current_status = None
                    for r in gr(tok):
                        if r["record_id"] == target_record_id:
                            current_status = r.get("fields", {}).get("状态", "")
                            break
                    if current_status:
                        new_status = usage_add(current_status, agent_name)
                        us(tok, target_record_id, new_status, note="429自动切换")
                except Exception as exc:
                    print(f"WARNING: 更新新主模型状态失败: {exc}", file=sys.stderr)

    sync_fallback_providers(rs, health_results)
    if not skip_health_rotate:
        clear_current(tok)
        for record_id, new_status, note in pending_updates:
            if note is None or note == "":
                us(tok, record_id, new_status)
            else:
                us(tok, record_id, new_status, note=note)
    if fe:
        print(f"\n\n📝 auth.json: {list(ex['credential_pool'].keys())}, 共 {sum(len(v) for v in ex['credential_pool'].values())} 个")
    else:
        print("\n\n⚠️ 无有效凭证，已清空 credential_pool")
    if not skip_health_rotate:
        cleanup_fallback_chain(rs, health_results)
    print(f"\n{'='*50}\n✅ 同步完成\n{'='*50}")
    output_records.sort(key=lambda item: item["priority"])
    print("__RECORDS__" + json.dumps(output_records, ensure_ascii=False, separators=(",", ":")))


def main():
    parser = argparse.ArgumentParser(description="同步飞书凭证池到 Hermes")
    parser.add_argument("--skip-health-rotate", action="store_true", help="跳过健康检查，直接同步")
    args = parser.parse_args()

    try:
        sync(args.skip_health_rotate)
    except KeyboardInterrupt:
        print("\n\n用户中断，同步已取消")
        return 1
    except OSError as exc:
        print(f"ERROR: 文件操作失败: {exc}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    raise SystemExit(main())