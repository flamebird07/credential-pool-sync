#!/usr/bin/env python3
"""Reconcile Feishu credential status with the active Hermes model v7.16.0。"""

import os
import sys

_hermes_site_packages = os.path.join(
    os.environ.get("APPDATA", ""), "uv", "tools", "hermes-agent", "Lib", "site-packages"
)
if os.path.isdir(_hermes_site_packages) and _hermes_site_packages not in sys.path:
    sys.path.insert(0, _hermes_site_packages)

import yaml
from sync_credential_pool import (
    S_A, S_I, S_R, S_U,
    detect_provider,
    get_agent_name,
    get_runtime_config_path,
    gr,
    gt,
    health_status,
    locked_path,
    normalise_base_url,
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


def identity(model, api_key, base_url):
    return (str(model or '').strip().lower(), str(api_key or '').strip(), normalise_base_url(base_url))


def active_identity():
    path = get_runtime_config_path()
    with locked_path(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    model = config.get("model") or {}
    if not isinstance(model, dict):
        return ("", "", "")
    return identity(model.get('default',''), model.get('api_key',''), model.get('base_url',''))


def repair_feishu_providers():
    """修复飞书表格中的 Provider 字段：根据 Base URL 反推标准 Provider 名称。

    当飞书表格的 Provider 字段为空或为 "custom" 时，根据 Base URL 自动推断
    标准 Provider（如 ARK、OPENAI、ANTHROPIC、DEEPSEEK 等），并更新到飞书。
    """
    token = gt()
    records = gr(token)
    updated = 0

    for record in records:
        record_id = record.get("record_id")
        fields = record.get("fields") or {}
        if not record_id:
            continue

        base_url = value(fields, "Base URL")
        current = value(fields, "Provider")
        inferred = detect_provider(base_url)

        # 只有推断出标准 Provider，且当前字段为空或为 "custom" 时才更新
        if inferred and (not current or current.lower() == "custom"):
            us(token, record_id, provider=inferred)
            updated += 1
            print(f"  ✅ [{record_id[:8]}] Provider: {current or '(空)'} → {inferred}")

    print(f"\n飞书 Provider 修复完成，共更新 {updated} 条记录")
    return updated


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
        current_provider = value(fields, "Provider")
        current_status = value(fields, "状态")
        current_note = value(fields, "备注")

        inferred_provider = detect_provider(base_url)
        provider_update = None
        provider_for_check = current_provider

        if inferred_provider and (
            not current_provider or current_provider.lower() == "custom"
        ):
            provider_for_check = inferred_provider
            provider_update = inferred_provider

        is_valid, probe_status, error, _used_url = tk(
            provider_for_check, api_key, base_url, model
        )
        
        # 状态栏显示 Agent 使用信息，备注保留原始说明
        if is_valid:
            if identity(model, api_key, base_url) == current:
                new_status = status_add(current_status, agent_name)
            else:
                new_status = status_remove(current_status, agent_name)
            new_note = '验证通过'
        else:
            # 健康检查失败，设置健康状态
            new_status = health_status(False, probe_status, error)
            if probe_status == '⛔ 限流':
                new_note = error or '额度已用完'
            elif probe_status == '❌ 无效':
                new_note = error or 'Key 无效'
            else:
                new_note = error or '验证失败'
        
        if (
            new_status != current_status
            or new_note != current_note
            or provider_update is not None
        ):
            us(
                token,
                record_id,
                new_status,
                note=new_note,
                provider=provider_update,
            )

    print(f"飞书状态清理完成，共检查 {len(records)} 条记录")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞书凭证状态管理工具")
    parser.add_argument("--repair-provider", action="store_true",
                        help="根据 Base URL 反推标准 Provider，修复飞书表格中的 Provider 字段")
    args = parser.parse_args()

    if args.repair_provider:
        return repair_feishu_providers()
    else:
        cleanup_feishu_status()
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
