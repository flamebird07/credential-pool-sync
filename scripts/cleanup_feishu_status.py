#!/usr/bin/env python3
"""Reconcile Feishu credential status with the active Hermes model v7.2.0 — 飞书状态管理优化"""

import sys

import yaml
from sync_credential_pool import (
    S_A, S_I, S_R,
    get_agent_name,
    get_runtime_config_path,
    gr,
    gt,
    locked_path,
    usage_add,
    usage_remove,
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
        current_status = value(fields, "状态")
        current_note = value(fields, "备注")
        is_valid, health_status, error, _used_url = tk(
            value(fields, "Provider"), api_key, base_url, model
        )
        
        # 健康状态放状态字段，使用信息放备注字段
        if is_valid:
            new_status = S_A
            if identity(api_key, base_url, model) == current:
                # 当前凭证，添加使用信息到备注
                new_note = usage_add(current_note, agent_name)
            else:
                # 其他凭证，移除使用信息
                new_note = usage_remove(current_note, agent_name)
                if new_note == "":
                    new_note = "验证通过"
        else:
            # 健康检查失败，设置健康状态，移除使用信息
            new_status = health_status
            new_note = usage_remove(current_note, agent_name)
            if new_note == "":
                new_note = error or "验证失败"
        
        if new_status != current_status or new_note != current_note:
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