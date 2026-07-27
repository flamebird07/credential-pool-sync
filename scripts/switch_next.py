#!/usr/bin/env python3
import subprocess, sys, os, json, yaml
from pathlib import Path


def get_current_model_config():
    runtime_config = Path.home() / 'AppData' / 'Local' / 'hermes' / 'config.yaml'
    with open(runtime_config, encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    model_config = config.get('model') or {}
    return model_config if isinstance(model_config, dict) else {}


def run_sync():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    r = subprocess.run([sys.executable, os.path.join(script_dir, "sync_credential_pool.py"), "--skip-health-rotate"], capture_output=True, text=True, timeout=120)
    output_lines = r.stdout.splitlines()
    print("\n".join(line for line in output_lines if not line.startswith("__RECORDS__")))
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        return []
    for line in output_lines:
        if line.startswith("__RECORDS__"):
            try:
                records = json.loads(line[len("__RECORDS__"):])
                return records if isinstance(records, list) else []
            except json.JSONDecodeError:
                return []
    return []


def update_runtime_main_model(model, base_url, api_key):
    runtime_config = Path.home() / 'AppData' / 'Local' / 'hermes' / 'config.yaml'
    with open(runtime_config, encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    config['model'] = {'default': model, 'provider': 'custom', 'base_url': base_url, 'api_key': api_key}
    temp_path = runtime_config.with_suffix('.yaml.tmp')
    with open(temp_path, 'w', encoding='utf-8', newline='\n') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    os.replace(temp_path, runtime_config)
    return str(runtime_config)


def main():
    print("=" * 50)
    print("切换凭证池下一个 API")
    print("=" * 50)

    try:
        current_model = get_current_model_config()
    except (OSError, yaml.YAMLError) as exc:
        print("ERROR: 无法读取当前模型配置: {}".format(exc), file=sys.stderr)
        sys.exit(1)

    print("同步飞书表格...")
    records = run_sync()
    if not records:
        print("ERROR: 同步失败或无有效凭证", file=sys.stderr)
        sys.exit(1)

    current_api_key = current_model.get("api_key")
    current_index = next(
        (
            index
            for index, record in enumerate(records)
            if record.get("api_key") == current_api_key
        ),
        None,
    )

    if current_index is None:
        target_record = records[0]
    elif len(records) == 1:
        print("无可用切换")
        return
    else:
        target_record = records[(current_index + 1) % len(records)]

    runtime_config = update_runtime_main_model(
        target_record["model"],
        target_record["base_url"],
        target_record["api_key"],
    )
    print(
        "切换成功！当前凭证: {} ({})".format(
            target_record.get("label") or target_record["model"],
            target_record.get("provider_name")
            or target_record.get("provider")
            or "unknown",
        )
    )
    print("主模型配置已更新: {}".format(runtime_config))
    print()
    print("完成")


if __name__ == "__main__":
    main()
