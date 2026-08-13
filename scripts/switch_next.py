#!/usr/bin/env python3
"""切换到下一个可用凭证 v7.16.0。"""
import argparse, json, os, sys, urllib.request, urllib.error, time, subprocess, re, msvcrt, random
import uuid

_hermes_site_packages = os.path.join(
    os.environ.get("APPDATA", ""), "uv", "tools", "hermes-agent", "Lib", "site-packages"
)
if os.path.isdir(_hermes_site_packages) and _hermes_site_packages not in sys.path:
    sys.path.insert(0, _hermes_site_packages)

import yaml
from pathlib import Path
from contextlib import contextmanager
from sync_credential_pool import (
    detect_provider,
    normalise_base_url,
    status_add,
    status_remove,
    tk,
    endpoint_candidates,
    update_runtime_main_model,
    priority,
    group_by_priority,
    collect_active_tier,
    load_bitable_ids,
    identity as pool_identity,
)

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
    h = {"Authorization": f"Bearer {t}"}
    a, pt = [], ""
    while True:
        u = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records?page_size=100" + (f"&page_token={pt}" if pt else "")
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
    base_token, table_id = load_bitable_ids()
    h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
    f = {}
    if s is not None:
        f["状态"] = s
    if note is not None:
        f["备注"] = note

    if not f:
        return
    request_with_retry(urllib.request.Request(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{base_token}/tables/{table_id}/records/{rid}", data=json.dumps({"fields": f}).encode(), headers=h, method="PUT"), timeout=10)

_UNSET = object()

def identity(record):
    model = str(record.get("model", "") or "").strip()
    api_key = str(record.get("api_key", "") or "").strip()
    base_url = normalise_base_url(record.get("base_url", ""))
    return (model.lower(), api_key.strip(), base_url)

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
    """Run a mandatory full sync before selecting the next credential.

    A rotation must fail closed when the local cache cannot be refreshed from
    Feishu; continuing would permit a stale credential to be selected.
    """
    if full:
        import subprocess
        try:
            sync_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_credential_pool.py")
            result = subprocess.run([sys.executable, sync_script], capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                raise RuntimeError(f"Full Feishu sync failed; switch cancelled: {detail[:500]}")
            else:
                print("Full Feishu sync completed; local credential cache is current")
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Full Feishu sync timed out after 240 seconds; switch cancelled "
                "to avoid using stale local credentials"
            ) from exc
    return read_auth_records()

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
    # 反推 Provider：如果 Provider 为空或为 custom，从 URL 推断
    if not provider or provider.lower() == "custom":
        provider = detect_provider(base_url) or provider
    if not provider or not api_key or api_key == "***":
        return None
    return {
        "record_id": record.get("record_id", ""),
        "label": label,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "priority": pr,
        "status": str(fields.get("状态", "") or "").strip(),
    }

def get_current_model_config():
    """获取当前主模型配置"""
    try:
        with open(get_runtime_config_path(), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        model_config = config.get("model") or {}
        if not isinstance(model_config, dict):
            return None
        current = model_config.copy()
        current["model"] = str(model_config.get("default", "") or "").strip()
        current['base_url'] = normalise_base_url(model_config.get('base_url', ''))
        return current
    except Exception:
        return None

def first_healthy(records, current, token=None):
    old_record = None
    current_identity = identity(current) if current else None
    if current_identity:
        for record in records:
            if identity(record) == current_identity:
                old_record = record
                break

    # 收窄到 active 档（优先级最高且存在有效凭证的档），只在此档内选候选
    by_tier = group_by_priority(records)
    _active_tier, active_valid, _health_results, tier_pending, _healed, _url_updates = collect_active_tier(
        by_tier,
        current_identity=pool_identity(
            (current or {}).get("api_key", ""),
            (current or {}).get("base_url", ""),
            (current or {}).get("default", ""),
        ),
        agent_name=get_agent_name(),
    )
    # 回写收窄过程产生的状态更新（低档位健康检查失败/限流等）
    if token:
        for record_id, new_status, note in tier_pending:
            if record_id:
                us(token, record_id, new_status, note=(note or None))

    candidates = ordered_candidates(active_valid, current)
    # 当候选列表为空但当前凭证仍有效时，说明当前已是最优，返回 noop 标记
    if not candidates and current_identity is not None and any(
        identity(r) == current_identity for r in active_valid
    ):
        target = dict(current or {})
        target["noop"] = True
        target["record_id"] = None
        return target

    for record in candidates:
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
                target["old_status"] = old_record.get("status", "")
            return target
        record_id = record.get("record_id")
        if token and record_id:
            # 健康状态放字段，使用信息放备注
            if status == S_R or (error and "HTTP 429" in error):
                us(token, record_id, S_R, note=error or '额度已用完')
            elif status == S_I or (error and ("HTTP 401" in error or "HTTP 403" in error)):
                us(token, record_id, S_I, note=error or 'Key 无效')
    return None

def ordered_candidates(records, current):
    """按优先级排序候选记录，当前记录排在最后"""
    current_identity = identity(current) if current else None
    index = next((i for i, record in enumerate(records) if current_identity is not None and identity(record) == current_identity), None)
    ordered = records if index is None else records[index + 1:] + records[:index + 1]
    return [record for record in ordered if current_identity is None or identity(record) != current_identity]


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

def rotate_once(records, rotation_lock):
    """Read, select, health-check, and write while holding the rotation lock."""
    with locked_path(rotation_lock):
        current = get_current_model_config()
        health_token = gt()
        target = first_healthy(records, current, health_token)
        path = update_runtime_main_model(target) if target is not None else None
    return target, path

def main():
    parser = argparse.ArgumentParser(description="切换到下一个可用凭证")
    parser.add_argument("--skip-sync", action="store_true", help="跳过 sync_credential_pool.py 子进程，直接从飞书读取凭证（不更新 auth.json）")
    args = parser.parse_args()

    try:
        rotation_lock = Path(__file__).with_name(".rotation")
        records = read_auth_records() if args.skip_sync else run_sync(full=True)
        target, path = rotate_once(records, rotation_lock)
        if target is not None and target.get("noop"):
            print("当前已是最优凭证，无需切换")
            return 0
        if target is None:
            print("没有可用候选，执行完整同步后再试一次...")
            records = run_sync(full=True)
            target, path = rotate_once(records, rotation_lock)
        if target is None:
            print("ERROR: 所有候选凭证均不可用", file=sys.stderr)
            return 1
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"默认配置已切换: {target.get('label') or target['model']}")
    print(f"主模型配置已更新: {path}")
    print(
        "生效范围: 下一条新消息会按此配置创建/重建 Agent。"
        "当前正在生成的回复仍使用切换前的运行时；若其中出现 fallback 通知，"
        "该通知描述的是旧运行时，不代表本次配置切换失败。"
    )

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
                # 从状态栏移除旧凭证的 Agent 标记
                new_status = status_remove(old_status, agent_name)
                old_status_val = target.get("old_status", "")
                if S_R in old_status_val or "限流" in old_status_val or "额度" in old_status_val:
                    old_note = "额度已用完"
                elif S_I in old_status_val or "无效" in old_status_val:
                    old_note = "Key 无效"
                else:
                    old_note = "验证失败"
                us(token, target["old_record_id"], new_status, note=old_note)

            new_r = next(
                r for r in gr(token)
                if r["record_id"] == target["record_id"]
            )
            # 在状态栏添加新凭证的 Agent 标记
            current_status = new_r.get("fields", {}).get("状态", "")
            new_status = status_add(current_status, agent_name)
            us(token, target["record_id"], new_status, note="验证通过")
        except Exception as exc:
            print(f"WARNING: 回写飞书状态失败: {exc}", file=sys.stderr)

    try:
        pass  # The switch is already complete; do not perform a second sync.
    except Exception as exc:
        print(f"WARNING: 切换后的完整同步失败: {exc}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
