#!/usr/bin/env python3
"""凭证池同步脚本 v2.0 — 含连通性验证 + 状态回写"""
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

S_U = "\u26a0\ufe0f \u4e0d\u53ef\u7528"
S_A = "\u2705 \u6b63\u5e38"
S_I = "\u274c \u65e0\u6548"
S_R = "\u26d4 \u9650\u6d41"

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

def us(t, rid, s=None, n=_UNSET, *, note=_UNSET):
    h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
    f = {}
    if s is not None:
        f["\u72b6\u6001"] = s
    if note is not _UNSET:
        f["\u5907\u6ce8"] = note
    elif n is not _UNSET:
        f["\u5907\u6ce8"] = n
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

def cleanup_fallback_chain(records):
    """Remove fallback providers whose base_url no longer exists in Feishu records."""
    print(f"\n{'='*50}\n\U0001f9f9 \u6e05\u7406 fallback chain\n{'='*50}")
    feishu_base_urls = set()
    for r in records:
        fields = r.get("fields") or {}
        bu = str(fields.get("Base URL", "") or "").strip().lower().rstrip("/")
        if bu:
            feishu_base_urls.add(bu)

    try:
        res = subprocess.run(["hermes", "fallback", "list"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print("  \u26a0\ufe0f hermes \u547d\u4ee4\u672a\u627e\u5230\uff0c\u8df3\u8fc7 fallback \u6e05\u7406")
        return
    except Exception as e:
        print(f"  \u26a0\ufe0f \u83b7\u53d6 fallback chain \u5931\u8d25: {e}")
        return

    if res.returncode != 0:
        print(f"  \u26a0\ufe0f \u65e0\u6cd5\u83b7\u53d6 fallback chain: {res.stderr.strip()}")
        return

    entries = _parse_fallback_list(res.stdout)
    if not entries:
        print("  \u65e0 fallback \u914d\u7f6e")
        return

    stale = [e for e in entries if e["base_url"] and e["base_url"] not in feishu_base_urls]
    if not stale:
        print("  fallback chain \u4e0e\u98de\u4e66\u8bb0\u5f55\u4e00\u81f4")
        return

    for e in sorted(stale, key=lambda x: x["index"], reverse=True):
        print(f"  \u79fb\u9664 stale fallback #{e['index']+1}: {e['model']} [{e['base_url']}]")
        try:
            rm = subprocess.run(
                ["hermes", "fallback", "remove"],
                input=f"{e['index']+1}\n",
                text=True,
                capture_output=True,
                timeout=30,
            )
        except Exception as e2:
            print(f"    \u79fb\u9664\u5931\u8d25: {e2}")
            continue
        if rm.returncode != 0:
            print(f"    \u79fb\u9664\u5931\u8d25: {rm.stderr.strip()}")

    print("  fallback chain \u6e05\u7406\u5b8c\u6210")


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
    model = str(fields.get("\u6a21\u578b", "") or "").strip()
    priority = _priority(fields.get("\u4f18\u5148\u7ea7", ""))
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
        "status": str(fields.get("\u72b6\u6001", "") or "").strip(),
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
    print("="*50); print("\u51ed\u8bc1\u6c60\u540c\u6b65 v2.0"); print("="*50)
    tok = gt()
    rs = gr(tok); print(f"\n\U0001f4cb \u98de\u4e66: {len(rs)} \u6761")
    fe, vc, ic, output_records, pending_updates = {}, 0, 0, [], []
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
            print(f"\n  \u23ed\ufe0f [{label}] \u8df3\u8fc7")
            if not skip_health_rotate and rid:
                pending_updates.append((rid, S_I, _UNSET))
            continue
        p = normalised["provider"]; l = normalised["label"]; ak = normalised["api_key"]
        bu = normalised["base_url"]; m = normalised["model"]; pr = normalised["priority"]
        if skip_health_rotate:
            iv, s, e, _used_url = True, S_A, None, ""
        else:
            print(f"\n  \U0001f504 [{l or p}] ...", end=" ")
            iv, s, e, _used_url = tk(p, ak, bu, m)
        if iv:
            if not skip_health_rotate:
                print(f"\U0001f504 {s}")
                record_identity = (ak, bu, m)
                if record_identity == current_identity:
                    new_status = status_add(f.get("状态", ""), agent_name)
                else:
                    new_status = status_remove(f.get("状态", ""), agent_name)
                normalised["status"] = new_status
                pending_updates.append((rid, new_status, "验证通过"))
            vc += 1
            output_records.append(normalised)
            eid = f"sync-{(m or l).lower().replace(' ', '-')}"
            pk = f"custom:{p.strip().lower().replace(' ', '-')}"
            fe.setdefault(pk, []).append({"id": eid, "label": l or m, "provider": p, "model": m, "auth_type": "api_key", "priority": pr, "source": f"manual:{ak[:12]}...", "access_token": ak, "api_key": ak, "last_status": "active", "base_url": bu, "request_count": 0, "secret_fingerprint": f"sha256:{eid}"})
        else:
            new_status = status_remove(f.get("状态", ""), agent_name)
            normalised["status"] = new_status
            note = e if e else "验证失败"
            print(f"\u274c {s}"); ic += 1
            pending_updates.append((rid, new_status, note))
        if not skip_health_rotate:
            time.sleep(0.3)
    print(f"\n{'='*50}\n\u2705 {vc} \u6709\u6548 | \u274c {ic} \u65e0\u6548")
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
    if not skip_health_rotate:
        clear_current(tok)
        for record_id, new_status, note in pending_updates:
            if note is _UNSET:
                us(tok, record_id, new_status)
            else:
                us(tok, record_id, new_status, note=note)
    if fe:
        print(f"\n\U0001f4dd auth.json: {list(ex['credential_pool'].keys())}, \u5171 {sum(len(v) for v in ex['credential_pool'].values())} \u4e2a")
    else:
        print("\n\u26a0\ufe0f \u65e0\u6709\u6548\u51ed\u8bc1\uff0c\u5df2\u6e05\u7a7a credential_pool")
    if not skip_health_rotate:
        cleanup_fallback_chain(rs)
    print(f"\n{'='*50}\n\u540c\u6b65\u5b8c\u6210 \u2705\n{'='*50}")
    output_records.sort(key=lambda item: item["priority"])
    print("__RECORDS__" + json.dumps(output_records, ensure_ascii=False, separators=(",", ":")))

def main():
    parser = argparse.ArgumentParser(description="Sync the Feishu credential pool to Hermes.")
    parser.add_argument(
        "--skip-health-rotate",
        action="store_true",
        help="skip health checks, Feishu status writes, and fallback cleanup",
    )
    args = parser.parse_args()
    try:
        sync(skip_health_rotate=args.skip_health_rotate)
    except urllib.error.HTTPError as exc:
        print(f"ERROR: 网络请求失败（HTTP {exc.code}），响应已过滤", file=sys.stderr)
        raise SystemExit(1)
    except urllib.error.URLError as exc:
        print(f"ERROR: 网络请求失败: {exc.reason}", file=sys.stderr)
        raise SystemExit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: JSON 解析失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except OSError as exc:
        print(f"ERROR: 文件操作失败: {exc}", file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
