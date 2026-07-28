#!/usr/bin/env python3
"""Reconcile Feishu credential status with the active Hermes model."""

import sys

import yaml

from sync_credential_pool import (
    S_A,
    get_agent_name,
    get_runtime_config_path,
    gr,
    gt,
    locked_path,
    status_add,
    status_remove,
    tk,
    us,
)


def value(fields, name):
    raw = fields.get(name, "")
    if isinstance(raw, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in raw
        ).strip()
    return str(raw or "").strip()


def identity(api_key, base_url, model):
    return (api_key, str(base_url or "").strip().lower().rstrip("/"), model)


def active_identity():
    path = get_runtime_config_path()
    with locked_path(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    model = config.get("model") or {}
    if not isinstance(model, dict):
        return ("", "", "")
    return identity(model.get("api_key", ""), model.get("base_url", ""), model.get("default", ""))


def cleanup_feishu_status():
    token = gt()
    records = gr(token)
    agent_name = get_agent_name()
    current = active_identity()

    for record in records:
        record_id = record.get("record_id")
        fields = record.get("fields") or {}
        if not record_id:
            continue
        api_key = value(fields, "API Key")
        base_url = value(fields, "Base URL")
        model = value(fields, "模型")
        status = value(fields, "状态")
        note = value(fields, "备注")
        is_valid, health_status, error, _used_url = tk(
            value(fields, "Provider"), api_key, base_url, model
        )
        without_this_agent = status_remove(status, agent_name)
        if is_valid and identity(api_key, base_url, model) == current:
            new_status = status_add(without_this_agent, agent_name)
        elif is_valid:
            # Preserve another agent's in-use marker; otherwise this key is simply healthy.
            new_status = without_this_agent if without_this_agent != S_A else S_A
        else:
            new_status = without_this_agent if without_this_agent != S_A else health_status
        new_note = "验证通过" if is_valid else (error or "验证失败")
        if new_status != status or new_note != note:
            us(token, record_id, new_status, note=new_note)

    print(f"飞书状态清理完成，共检查 {len(records)} 条记录")


def main():
    cleanup_feishu_status()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
