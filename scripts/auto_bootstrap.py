#!/usr/bin/env python3
"""Select the first healthy credential and atomically update Hermes config."""

import json
import os
import subprocess
import sys
import uuid

import yaml

from sync_credential_pool import get_runtime_config_path, locked_path, model_limits, tk


def priority(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 99


def run_sync():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_credential_pool.py")
    result = subprocess.run(
        [sys.executable, script, "--skip-health-rotate"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"credential sync failed ({result.returncode}): {detail}")

    lines = [
        line for line in result.stdout.splitlines() if line.startswith("__RECORDS__")
    ]
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
        model = {
            "default": record["model"],
            "provider": "custom",
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
                yaml.safe_dump(
                    config,
                    handle,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, runtime_config)
        finally:
            if tmp.exists():
                tmp.unlink()
    return runtime_config


def main():
    try:
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

        for attempt in range(2):
            records = run_sync()
            for record in records:
                record["base_url"] = str(record.get("base_url", "")).strip().lower().rstrip("/")
                required = ("model", "base_url", "api_key")
                if any(not record.get(field) for field in required):
                    continue
                # 过滤视觉模型（max_tokens 限制 1024，不适合文本对话）
                model_name = str(record.get("model", "")).lower()
                if any(v in model_name for v in ("glm-4v", "glm-4.6v", "vision", "-v-flash", "vl-")):
                    print(f"Skipping vision model: {record.get('model')}")
                    continue
                record_identity = (record["api_key"], record["base_url"], record["model"])
                if record_identity == current_identity:
                    continue
                ok, _status, error, _used_url = tk(
                    record.get("provider", ""),
                    record["api_key"],
                    record["base_url"],
                    record["model"],
                )
                if not ok:
                    label = record.get("label") or record["model"]
                    print(f"Credential unavailable [{label}]: {error or 'health check failed'}")
                    continue
                path = write_runtime_config(record)
                label = record.get("label") or record["model"]
                print(f"Switched to [{label}]; updated {path}")
                return 0
            if attempt == 0:
                print("No available credential; syncing and retrying once")
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        RuntimeError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("No available credentials", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
