#!/usr/bin/env python3
"""Rotate Hermes to the next healthy credential."""

import argparse
import json
import os
import subprocess
import sys
import uuid

import yaml

from sync_credential_pool import (
    AUTH_JSON,
    S_I,
    S_R,
    get_agent_name,
    get_runtime_config_path,
    gr,
    gt,
    locked_path,
    model_limits,
    status_add,
    status_remove,
    tk,
    us,
)


def identity(record):
    return (
        record.get("api_key", ""),
        str(record.get("base_url", "")).strip().lower().rstrip("/"),
        record.get("model", ""),
    )


def get_current_model_config():
    path = get_runtime_config_path()
    with locked_path(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    model = config.get("model") or {}
    return model if isinstance(model, dict) else {}


def normalise_records(records):
    result = []
    for record in records:
        if not isinstance(record, dict):
            continue
        item = dict(record)
        item["api_key"] = item.get("api_key") or item.get("access_token") or ""
        item["base_url"] = str(item.get("base_url", "")).strip().lower().rstrip("/")
        try:
            item["priority"] = int(item.get("priority", 99))
        except (TypeError, ValueError):
            item["priority"] = 99
        if all(item.get(field) for field in ("provider", "model", "base_url", "api_key")):
            result.append(item)
    return sorted(result, key=lambda item: item["priority"])


def read_auth_records():
    with locked_path(AUTH_JSON):
        data = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    records = []
    for provider_key, entries in (data.get("credential_pool") or {}).items():
        if not isinstance(entries, list):
            continue
        provider = str(provider_key).removeprefix("custom:")
        for entry in entries:
            if isinstance(entry, dict):
                item = dict(entry)
                item.setdefault("provider", provider)
                records.append(item)
    return normalise_records(records)


def run_sync(full=False):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_credential_pool.py")
    result = subprocess.run(
        [sys.executable, script] + ([] if full else ["--skip-health-rotate"]),
        capture_output=True,
        text=True,
        timeout=600 if full else 60,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    lines = [line for line in result.stdout.splitlines() if line.startswith("__RECORDS__")]
    if not lines:
        raise ValueError("同步输出缺少 __RECORDS__")
    return normalise_records(json.loads(lines[-1][len("__RECORDS__"):]))


def ordered_candidates(records, current):
    current_identity = identity(current)
    index = next((i for i, record in enumerate(records) if identity(record) == current_identity), None)
    ordered = records if index is None else records[index + 1:] + records[:index + 1]
    return [record for record in ordered if identity(record) != current_identity]


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
            if status == S_R or (error and "HTTP 429" in error):
                us(token, record_id, "⛔ 限流")
            elif status == S_I or (error and ("HTTP 401" in error or "HTTP 403" in error)):
                us(token, record_id, "❌ 无效")
    return None


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

    # 回写飞书状态和备注。
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
                new_status = status_remove(old_status, agent_name)
                us(token, target["old_record_id"], new_status, note="额度已用完")

            new_r = next(
                r for r in gr(token)
                if r["record_id"] == target["record_id"]
            )
            new_status = status_add(
                new_r.get("fields", {}).get("状态", ""),
                agent_name,
            )
            us(token, target["record_id"], new_status, note="验证通过")
        except Exception as exc:
            print(f"WARNING: 回写飞书状态失败: {exc}", file=sys.stderr)

    try:
        run_sync(full=True)
    except Exception as exc:
        print(f"WARNING: 切换后的完整同步失败: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
