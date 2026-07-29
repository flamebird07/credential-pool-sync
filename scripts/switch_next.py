#!/usr/bin/env python3
"""切换到下一个可用凭证 v7.2.0 — 含飞书状态管理优化"""
import argparse, json, os, sys, urllib.request, urllib.error, time, subprocess, re, msvcrt, random
import uuid
import yaml
from pathlib import Path
from contextlib import contextmanager

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

def _usage_names(note):
    match = re.search(r"🔄\s+(.+?)使用中", str(note or ""))
    return match.group(1).split("+") if match else []

def usage_remove(note, name):
    names = [agent for agent in _usage_names(note) if agent != name]
    detail = re.sub(r"\s*\|\s*🔄\s+.?使用中", "", str(note or "")).strip(" |")
    return f"{detail} | 🔄 {'+'.join(names)}使用中" if names else detail

def usage_add(note, name):
    names = [agent for agent in _usage_names(note) if agent != name] + [name]
    detail = re.sub(r"\s*\|\s*🔄\s+.?使用中", "", str(note or "")).strip(" |")
    marker = f"🔄 {'+'.join(dict.fromkeys(names))}使用中"
    return f"{detail} | {marker}" if detail else marker

def gt():
    app_id, app_secret = load_feishu_credentials()
    d = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    r = urllib.request.Request("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", data=d, headers={"Content-Type": "application/json"})
    result = request_with_retry(r, timeout=10)
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu auth failed: {result}")
    return result["tenant_access_token"]

def gr(t):
    h = {"Authorization": f"Bearer {t}"}
    a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_ID}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
        r = request_with_retry(urllib.request.Request(u, headers=h), timeout=15)
        a.extend(r["data"]["items"]); pt = r["data"].get("page_token", "")
        if not r["data"].get("has_more"): break
    return a

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

_UNSET = object()

def identity(record):
    return (
        str(record.get("api_key", "") or "").strip(),
        str(record.get("base_url", "") or "").strip().lower().rstrip("/"),
        str(record.get("model", "") or "").strip(),
    )

def model_limits(model_name):
    """Return model_config for models with known context length issues."""
    name = str(model_name or "").strip().lower()
    if name in ("glm-4.6v", "glm-4.5-air"):
        return {"max_tokens": 4096, "context_length": 32768}
    return {}

def read_auth_records():
    """从飞书读取凭证记录"""
    try:
        token = gt()
        records = gr(token)
        return [_normalise_record(r) for r in records if _normalise_record(r)]
    except Exception as e:
        print(f"ERROR: 读取飞书凭证失败: {e}")
        return []

def run_sync(full=False):
    """运行同步，返回记录列表"""
    if full:
        import subprocess
        try:
            result = subprocess.run([sys.executable, "sync_credential_pool.py"], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                print(f"ERROR: 同步失败: {result.stderr}")
            else:
                print("同步完成")
        except Exception as e:
            print(f"ERROR: 运行同步失败: {e}")
    return read_auth_records()

def _normalise_record(record):
    fields = record.get("fields") or {}
    provider = str(fields.get("Provider", "") or "").strip()
    label = str(fields.get("Label", "") or "").strip()
    api_key = str(fields.get("API Key", "") or "").strip()
    base_url = str(fields.get("Base URL", "") or "").strip().lower().rstrip("/")
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

def _priority(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 99

def get_current_model_config():
    """获取当前主模型配置"""
    try:
        with open(get_runtime_config_path(), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        model = config.get("model") or {}
        if not isinstance(model, dict):
            return None
        return model
    except Exception:
        return None

def first_healthy(records, current, token=None):
    old_record = None
    if current:
        old_identity = identity(current)
        for record in records:
            if identity(record) == old_identity:
                old_record = record
                break

    for record in ordered_candidates(records, current):
        if not record.get("api_key") or not record.get("base_url"):
            continue
        is_valid, status, error, _used_url = tk(
            record["provider"],
            record["api_key"],
            record["base_url"],
            record["model"],
        )
        if is_valid:
            target = record.copy()
            if old_record:
                target["old_record_id"] = old_record.get("record_id")
            return target
        record_id = record.get("record_id")
        if token and record_id:
            # 健康状态放字段，使用信息放备注
            if status == S_R or (error and "HTTP 429" in error):
                us(token, record_id, S_R, note=error)
            elif status == S_I or (error and ("HTTP 401" in error or "HTTP 403" in error)):
                us(token, record_id, S_I, note=error)
    return None

def ordered_candidates(records, current):
    """按优先级排序候选记录，当前记录排在最后"""
    current_identity = identity(current) if current else None
    index = next((i for i, record in enumerate(records) if identity(record) == current_identity), None)
    ordered = records if index is None else records[index + 1:] + records[:index + 1]
    return [record for record in ordered if identity(record) != current_identity]

def update_runtime_main_model(record):
    path = get_runtime_config_path()
    with locked_path(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            raise ValueError("config.yaml 顶层必须是映射")
        model = {
            "default": record["model"],
            "provider": "custom",
            "base_url": record["base_url"].rstrip("/"),
            "api_key": record["api_key"],
        }
        limits = model_limits(record["model"])
        if limits:
            model["model_config"] = limits
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

def endpoint_candidates(base_url):
    base = str(base_url or "").strip().rstrip("/")
    suffixes = ("/v1/chat/completions", "/chat/completions", "/v1/messages", "/messages")
    for suffix in suffixes:
        if base.lower().endswith(suffix):
            base = base[:-len(suffix)].rstrip("/")
            break
    return [f"{base}{suffix}" for suffix in suffixes]

def main():
    parser = argparse.ArgumentParser(description="切换到下一个可用凭证")
    parser.add_argument("--skip-sync", action="store_true", help="直接读取 auth.json，不同步飞书")
    args = parser.parse_args()

    try:
        current = get_current_model_config()
        records = read_auth_records() if args.skip_sync else run_sync(full=False)
        health_token = gt()
        target = first_healthy(records, current, health_token)
        if target is None:
            print("没有可用候选，执行完整同步后再试一次...")
            records = run_sync(full=True)
            target = first_healthy(records, current, health_token)
        if target is None:
            print("ERROR: 所有候选凭证均不可用", file=sys.stderr)
            return 1
        path = update_runtime_main_model(target)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"切换成功: {target.get('label') or target['model']}")
    print(f"主模型配置已更新: {path}")

    # 回写飞书状态和备注
    if target.get("record_id"):
        try:
            token = gt()
            agent_name = get_agent_name()
            if target.get("old_record_id"):
                old_r = next(
                    r for r in gr(token)
                    if r["record_id"] == target["old_record_id"]
                )
                old_status = old_r.get("fields", {}).get("状态", "")
                # 移除旧凭证的使用信息
                new_status = usage_remove(old_status, agent_name)
                us(token, target["old_record_id"], new_status, note="额度已用完")

            new_r = next(
                r for r in gr(token)
                if r["record_id"] == target["record_id"]
            )
            # 添加新凭证的使用信息，不覆盖健康状态
            current_status = new_r.get("fields", {}).get("状态", "")
            new_note = usage_add(new_r.get("fields", {}).get("备注", ""), agent_name)
            us(token, target["record_id"], current_status, note=new_note)
        except Exception as exc:
            print(f"WARNING: 回写飞书状态失败: {exc}", file=sys.stderr)

    try:
        run_sync(full=True)
    except Exception as exc:
        print(f"WARNING: 切换后的完整同步失败: {exc}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())