#!/usr/bin/env python3
"""Select the first healthy credential and atomically update Hermes config. v7.13.2"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

_hermes_site_packages = os.path.join(
    os.environ.get("APPDATA", ""), "uv", "tools", "hermes-agent", "Lib", "site-packages"
)
if os.path.isdir(_hermes_site_packages) and _hermes_site_packages not in sys.path:
    sys.path.insert(0, _hermes_site_packages)

import yaml

from sync_credential_pool import (
    detect_provider,
    get_agent_name,
    get_runtime_config_path,
    gr,
    gt,
    health_status,
    locked_path,
    model_limits,
    S_I,
    S_R,
    S_U,
    status_add,
    status_remove,
    tk,
    us,
    priority,
    group_by_priority,
    collect_active_tier,
    identity as pool_identity,
)


def run_sync(full=False):
    """Run sync_credential_pool.py. Default fast skip-mode fill; full=True runs a
    real health-checked sync (no --skip-health-rotate) to advance to the next tier."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_credential_pool.py")
    cmd = [sys.executable, script]
    if not full:
        cmd.append("--skip-health-rotate")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=(240 if full else 60),
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"credential sync failed ({result.returncode}): {detail}")
    lines = [line for line in result.stdout.splitlines() if line.startswith("__RECORDS__")]
    if not lines:
        raise ValueError("sync output is missing __RECORDS__")
    records = json.loads(lines[-1][len("__RECORDS__"):])
    if not isinstance(records, list):
        raise ValueError("__RECORDS__ must contain a JSON list")
    return sorted(records, key=lambda record: priority(record.get("priority")))


def write_runtime_config(record):
    runtime_config = get_runtime_config_path()
    with locked_path(runtime_config):
        with open(runtime_config, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            raise ValueError("config.yaml root must be a mapping")

        existing_model = config.get("model") or {}
        if not isinstance(existing_model, dict):
            existing_model = {}

        # `record.provider` may be an inferred Feishu display label (e.g. ARK).
        # Hermes still treats URL-backed records as custom providers.
        record_provider = "custom"
        provider = record_provider

        model = {
            "default": record["model"],
            "provider": provider,
            "base_url": str(record["base_url"]).strip().lower().rstrip("/"),
            "api_key": record["api_key"],
        }
        limits = model_limits(record["model"])
        if limits:
            model["model_config"] = limits
        config["model"] = model
        tmp = runtime_config.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False, default_flow_style=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, runtime_config)
        finally:
            if tmp.exists():
                tmp.unlink()
    return runtime_config


def try_switch(records, attempt, deferred_records, rotation_lock):
    """Read, select, health-check, and write while holding the shared rotation lock."""
    current_identity = None
    with locked_path(rotation_lock):
        runtime_config = get_runtime_config_path()
        with locked_path(runtime_config):
            with open(runtime_config, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        current = config.get("model") or {}
        current_identity = (
            current.get("api_key", ""),
            str(current.get("base_url", "")).strip().lower().rstrip("/"),
            current.get("default", ""),
        )

        if attempt > 0 and deferred_records:
            records = records + deferred_records

        # 收窄到 active 档（优先级最高且存在有效凭证的档），只在该档内选候选
        by_tier = group_by_priority(records)
        _active_tier, active_valid, _health_results, _tier_pending, _healed, _url_updates = collect_active_tier(
            by_tier,
            current_identity=pool_identity(
                current.get("api_key", ""),
                current.get("base_url", ""),
                current.get("default", ""),
            ),
            agent_name=get_agent_name(),
        )
        records = active_valid

        for record in records:
            record["base_url"] = str(record.get("base_url", "")).strip().lower().rstrip("/")
            if any(not record.get(field) for field in ("model", "base_url", "api_key")):
                continue
            model_name = str(record.get("model", "")).lower()
            if any(v in model_name for v in ("glm-4v", "glm-4.6v", "vision", "-v-flash", "vl-")):
                print(f"Skipping vision model: {record.get('model')}")
                continue
            if "openai.com" in record.get("base_url", "") and attempt == 0:
                print(f"Deferring GPT credential (low priority): {record.get('model')}")
                deferred_records.append(record)
                continue
            if (record["api_key"], record["base_url"], record["model"]) == current_identity:
                continue
            ok, _status, error, _used_url = tk(
                record.get("provider", ""), record["api_key"], record["base_url"], record["model"]
            )
            if not ok:
                label = record.get("label") or record["model"]
                print(f"Credential unavailable [{label}]: {error or 'health check failed'}")
                try:
                    token = gt()
                    if _status == S_R:
                        note = error or '额度已用完'
                    elif _status == S_I:
                        note = error or 'Key 无效'
                    else:
                        note = error or '验证失败'
                    us(token, record["record_id"], health_status(False, _status, error), note=note)
                except Exception:
                    pass
                continue
            path = write_runtime_config(record)
            return record, path, current_identity
    return None, None, current_identity


def report_exhaustion(records, old_identity):
    """All candidates exhausted: mark the current credential failed in Feishu
    and clear fallback_providers in config.yaml. Failures here never change
    the exit code."""
    try:
        if old_identity:
            token = gt()
            for candidate in records:
                identity = (
                    candidate.get("api_key", ""),
                    str(candidate.get("base_url", "")).strip().lower().rstrip("/"),
                    candidate.get("model", ""),
                )
                if identity == old_identity and candidate.get("record_id"):
                    us(token, candidate["record_id"], health_status(False, S_U),
                       note="无可用候选，主凭证失效")
                    break
    except Exception as exc:
        print(f"WARNING: failed to report main credential failure: {exc}", file=sys.stderr)

    try:
        runtime_config = get_runtime_config_path()
        with locked_path(runtime_config):
            with open(runtime_config, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
            if isinstance(config, dict) and config.get("fallback_providers"):
                config["fallback_providers"] = []
                tmp = runtime_config.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
                try:
                    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                        yaml.safe_dump(config, handle, allow_unicode=True, sort_keys=False, default_flow_style=False)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, runtime_config)
                finally:
                    if tmp.exists():
                        tmp.unlink()
    except Exception as exc:
        print(f"WARNING: failed to clear fallback_providers: {exc}", file=sys.stderr)


def main():
    rotation_lock = Path(__file__).with_name(".rotation")
    deferred_records = []
    records = []
    try:
        # 首轮：skip 快速填充，只健康检查最低非空档
        records = run_sync()
        record, path, old_identity = try_switch(records, 0, deferred_records, rotation_lock)
        if record is None:
            # active 档全部健康检查失败：触发一次完整同步（不带 --skip-health-rotate）
            # 推进档位后重试
            print("No available credential in active tier; running full sync to advance tier...")
            records = run_sync(full=True)
            record, path, old_identity = try_switch(records, 1, deferred_records, rotation_lock)
        if record is not None:
            label = record.get("label") or record["model"]
            print(f"Switched to [{label}]; updated {path}")
            record_id = record.get("record_id")
            if record_id:
                try:
                    token = gt()
                    agent_name = get_agent_name()
                    # 从 records 中找到旧凭证的 record_id
                    old_record_id = None
                    if old_identity:
                        for candidate in records:
                            candidate_identity = (
                                candidate.get("api_key", ""),
                                str(candidate.get("base_url", "")).strip().lower().rstrip("/"),
                                candidate.get("model", ""),
                            )
                            if candidate_identity == old_identity:
                                old_record_id = candidate.get("record_id")
                                break

                    feishu_records = gr(token)

                    # 先清理旧凭证的 Agent 标记
                    if old_record_id and old_record_id != record_id:
                        for r in feishu_records:
                            if r["record_id"] == old_record_id:
                                old_status = r.get("fields", {}).get("状态", "")
                                cleaned_status = status_remove(old_status, agent_name)
                                us(token, old_record_id, cleaned_status)
                                break

                    # 再更新新凭证的 Agent 标记
                    for r in feishu_records:
                        if r["record_id"] == record_id:
                            new_status = status_add(r.get("fields", {}).get("状态", ""), agent_name)
                            us(token, record_id, new_status, note="验证通过")
                            break
                except Exception as exc:
                    print(f"WARNING: failed to update Feishu credential statuses: {exc}", file=sys.stderr)
            return 0
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("No available credentials", file=sys.stderr)
    report_exhaustion(records, old_identity)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
