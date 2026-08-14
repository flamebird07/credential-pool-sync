#!/usr/bin/env python3
"""
凭证池技能一键部署脚本 v7.17.0

在任何安装了本技能的 Hermes 机器上运行一次，即可完成全部配置：
  1. 检查飞书机器人凭证与目标多维表格的读写权限
  2. 为空白多维表格创建标准“凭证池”数据表，或补齐缺失字段
  3. 运行首次完整同步，并确认至少一条凭证通过健康检查
  4. 仅在通过准入检查后注册定时任务和 Gateway 启动钩子
  5. 输出部署报告或明确的人工补充指引

用法：
  python scripts/setup.py
  python scripts/setup.py --skip-cron       # 不注册 cron job
  python scripts/setup.py --skip-bootstrap  # 不配 gateway startup hook
  python scripts/setup.py --bitable-app-token app_xxx
  python scripts/setup.py --bitable-app-token app_xxx --bitable-table-id tbl_xxx
  python scripts/setup.py --sync-only       # 只做首次同步（等同 --skip-cron --skip-bootstrap）

幂等：重复运行安全。已存在的 cron job 不会重复注册，已配置的 startup hook 不会重复添加。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

_hermes_site_packages = os.path.join(
    os.environ.get("APPDATA", ""), "uv", "tools", "hermes-agent", "Lib", "site-packages"
)
if os.path.isdir(_hermes_site_packages) and _hermes_site_packages not in sys.path:
    sys.path.insert(0, _hermes_site_packages)

import yaml
from sync_credential_pool import gt, request_with_retry

SCRIPT_DIR = Path(__file__).parent.resolve()
SKILL_DIR = SCRIPT_DIR.parent


def get_hermes_home():
    return Path.home() / "AppData" / "Local" / "hermes"


def get_config_path():
    return get_hermes_home() / "config.yaml"


def get_auth_path():
    return get_hermes_home() / "auth.json"


def get_cron_jobs_path():
    return get_hermes_home() / "cron" / "jobs.json"


def get_cron_scripts_dir():
    """cron job 脚本的稳定存放目录（不随技能目录移动）。"""
    return get_hermes_home() / "scripts"


def load_config():
    path = get_config_path()
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(config):
    path = get_config_path()
    tmp = path.with_suffix(f".yaml.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


# ─── Step 1: 飞书凭证检查 ─────────────────────────────────────────

def check_feishu_credentials():
    """检查飞书凭证是否可用。返回 (ok, source, app_id)。"""
    # 1. 环境变量
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return True, "env", app_id

    # 2. config.yaml 各候选位置
    config = load_config()
    candidates = [
        ("feishu", config.get("feishu")),
        ("secrets.feishu", (config.get("secrets") or {}).get("feishu")),
        ("channels.feishu", (config.get("channels") or {}).get("feishu")),
        ("platforms.feishu.extra", ((config.get("platforms") or {}).get("feishu") or {}).get("extra")),
    ]
    for source, candidate in candidates:
        if isinstance(candidate, dict):
            cid = str(candidate.get("app_id") or candidate.get("appId") or "").strip()
            csec = str(candidate.get("app_secret") or candidate.get("appSecret") or "").strip()
            if cid and csec:
                return True, source, cid

    return False, "none", ""


# ─── Step 2: 飞书表格准入与引导 ────────────────────────────────────

STANDARD_FIELDS = (
    ("Provider", 1, "供应商展示名称；仅用于飞书识别。"),
    ("Label", 1, "凭证的人类可读标签。"),
    ("API Key", 1, "API 密钥。"),
    ("Base URL", 1, "服务基础 URL，不包含 /chat/completions。"),
    ("模型", 1, "模型名称。"),
    ("优先级", 2, "0–9；0 最高。"),
    ("状态", 1, "由技能维护，请勿手动覆盖。"),
    ("备注", 1, "最近一次健康检查结果。"),
)


def _bitable_request(token, path, method="GET", body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = __import__("urllib.request", fromlist=["Request"]).Request(
        f"https://open.feishu.cn/open-apis/bitable/v1{path}", data=data, headers=headers, method=method
    )
    try:
        result = request_with_retry(request, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"飞书接口请求失败（{method} {path}）：{exc}") from exc
    if result.get("code") != 0:
        raise RuntimeError(f"飞书接口拒绝请求（{method} {path}）：{result.get('msg') or result}")
    return result.get("data") or {}


def _configured_bitable_ids():
    config = load_config()
    mapping = config.get("credential_pool_sync") or {}
    return (
        str(os.environ.get("FEISHU_BITABLE_APP_TOKEN") or mapping.get("bitable_app_token") or "").strip(),
        str(os.environ.get("FEISHU_BITABLE_TABLE_ID") or mapping.get("bitable_table_id") or "").strip(),
    )


def _save_bitable_ids(app_token, table_id):
    config = load_config()
    section = config.get("credential_pool_sync") or {}
    if not isinstance(section, dict):
        section = {}
    section["bitable_app_token"] = app_token
    section["bitable_table_id"] = table_id
    config["credential_pool_sync"] = section
    save_config(config)


def provision_bitable(app_token, table_id=""):
    """Verify access and make one supplied Bitable ready for credential entry.

    The caller supplies an existing Bitable App Token.  The setup never creates
    a top-level Bitable because that requires a user-controlled folder and would
    otherwise create an inaccessible document.  If no table ID is supplied, it
    reuses a table named “凭证池” or creates one in that Bitable.
    """
    token = gt()
    encoded_app = quote(app_token, safe="")
    _bitable_request(token, f"/apps/{encoded_app}")  # Explicit read-permission check.
    tables = _bitable_request(token, f"/apps/{encoded_app}/tables").get("items") or []
    selected = next((item for item in tables if item.get("table_id") == table_id), None) if table_id else None
    if table_id and selected is None:
        raise RuntimeError(f"未找到指定的数据表 {table_id}；请确认它属于所提供的多维表格。")
    if selected is None:
        selected = next((item for item in tables if item.get("name") == "凭证池"), None)
    if selected is None:
        created = _bitable_request(token, f"/apps/{encoded_app}/tables", "POST", {"table": {"name": "凭证池"}})
        selected = created.get("table") or created
    actual_table_id = str(selected.get("table_id") or "").strip()
    if not actual_table_id:
        raise RuntimeError("飞书未返回新建数据表 ID，无法继续部署。")
    encoded_table = quote(actual_table_id, safe="")
    fields = _bitable_request(token, f"/apps/{encoded_app}/tables/{encoded_table}/fields").get("items") or []
    existing = {str(field.get("field_name") or "").strip() for field in fields}
    created_fields = []
    for name, field_type, description in STANDARD_FIELDS:
        if name in existing:
            continue
        _bitable_request(
            token,
            f"/apps/{encoded_app}/tables/{encoded_table}/fields",
            "POST",
            {"field_name": name, "type": field_type, "description": description},
        )
        created_fields.append(name)
    _save_bitable_ids(app_token, actual_table_id)
    return actual_table_id, selected.get("name") or "凭证池", created_fields


def prompt_for_bitable(app_token, non_interactive):
    if app_token:
        return app_token
    if non_interactive or not sys.stdin.isatty():
        raise RuntimeError(
            "缺少飞书多维表格。请创建或选择一个可编辑的空白/现有多维表格后，使用 "
            "--bitable-app-token app_xxx 重新运行；也可设置 FEISHU_BITABLE_APP_TOKEN。"
        )
    print("\n需要一个飞书多维表格来保存凭证。请在飞书中新建或选择一个可供机器人编辑的表格。")
    value = input("粘贴该多维表格的 App Token（留空取消）：").strip()
    if not value:
        raise RuntimeError("未提供 App Token；部署未激活，Hermes 不会注册凭证池自动任务。")
    return value


# ─── Step 2: 首次完整同步 ─────────────────────────────────────────

def run_first_sync():
    """Run first sync and require at least one health-checked credential."""
    sync_script = SCRIPT_DIR / "sync_credential_pool.py"
    if not sync_script.exists():
        return False, f"sync script not found: {sync_script}"

    try:
        result = subprocess.run(
            [sys.executable, str(sync_script)],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
            cwd=str(SCRIPT_DIR),
        )
        tail = result.stdout.strip().splitlines()[-10:] if result.stdout else []
        tail_text = "\n".join(tail)
        if result.returncode != 0:
            return False, f"exit code {result.returncode}\n{result.stderr[-500:] or tail_text}"
        records_line = next((line for line in reversed(result.stdout.splitlines()) if line.startswith("__RECORDS__")), "")
        try:
            valid_records = json.loads(records_line[len("__RECORDS__"):]) if records_line else []
        except json.JSONDecodeError:
            valid_records = []
        if not valid_records:
            return False, "未发现通过健康检查的凭证。请在飞书表中至少填写一条有效 API Key、Base URL 和模型后重新运行。"
        return True, tail_text
    except subprocess.TimeoutExpired:
        return False, "timeout (300s)"
    except Exception as e:
        return False, str(e)


# ─── Step 3: 注册 cron job ────────────────────────────────────────

CRON_JOB_NAME = "凭证池定时同步"
CRON_SCHEDULE = "0 */2 * * *"  # 每 2 小时


def register_cron_job():
    """
    注册定时同步 cron job。幂等：已存在同名则更新其 script/workdir 路径后写入。

    脚本会先复制到 hermes/scripts/credential_pool_sync.py（稳定位置，不随技能目录移动），
    cron job 的 script 字段用相对文件名，避免旧版绝对路径指向已迁移的技能目录导致失效。
    返回 (done, message)。
    """
    jobs_path = get_cron_jobs_path()
    if not jobs_path.parent.exists():
        jobs_path.parent.mkdir(parents=True, exist_ok=True)

    if jobs_path.exists():
        try:
            with open(jobs_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {"jobs": []}
    else:
        data = {"jobs": []}

    # 1. 把同步脚本复制到稳定位置
    sync_src = SCRIPT_DIR / "sync_credential_pool.py"
    cron_scripts_dir = get_cron_scripts_dir()
    cron_scripts_dir.mkdir(parents=True, exist_ok=True)
    sync_dst = cron_scripts_dir / "credential_pool_sync.py"
    shutil.copy2(sync_src, sync_dst)

    jobs = data.get("jobs", [])
    found = None
    for job in jobs:
        if job.get("name") == CRON_JOB_NAME:
            found = job
            break

    if found is not None:
        # 2. 修复旧版绝对路径 bug：更新 script 为相对文件名 + workdir 为稳定目录
        found["script"] = sync_dst.name
        found["workdir"] = str(cron_scripts_dir)
        data["jobs"] = jobs
        _write_jobs_json(data, jobs_path)
        return False, f"已存在同名 cron job，已更新 script 路径为 {sync_dst.name}（workdir={cron_scripts_dir.name}）"

    new_job = {
        "id": uuid.uuid4().hex[:12],
        "name": CRON_JOB_NAME,
        "prompt": "",
        "skills": [],
        "skill": None,
        "model": None,
        "provider": None,
        "base_url": None,
        "script": sync_dst.name,
        "no_agent": True,
        "context_from": None,
        "schedule": {
            "kind": "cron",
            "expr": CRON_SCHEDULE,
            "display": CRON_SCHEDULE,
        },
        "schedule_display": CRON_SCHEDULE,
        "repeat": {"times": None, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "paused_at": None,
        "paused_reason": None,
        "created_at": None,
        "next_run_at": None,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "last_delivery_error": None,
        "deliver": "local",
        "origin": None,
        "enabled_toolsets": None,
        "workdir": str(cron_scripts_dir),
        "profile": None,
        "fire_claim": None,
    }

    jobs.append(new_job)
    data["jobs"] = jobs
    _write_jobs_json(data, jobs_path)

    return True, f"已注册 cron job：{CRON_SCHEDULE}（每2小时同步一次，静默运行不推送）"


def _write_jobs_json(data, jobs_path):
    """原子写入 jobs.json。"""
    tmp = jobs_path.with_suffix(f".json.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, jobs_path)
    finally:
        if tmp.exists():
            tmp.unlink()


# ─── Step 4: 配置 gateway startup hook ────────────────────────────

def configure_bootstrap_hook():
    """
    在 config.yaml 中配置 gateway startup hooks，让 gateway 启动时自动跑 auto_bootstrap.py。
    幂等：已存在则跳过。返回 (done, message)。
    """
    config = load_config()
    gateway = config.get("gateway") or {}
    if not isinstance(gateway, dict):
        gateway = {}

    bootstrap_script = SCRIPT_DIR / "auto_bootstrap.py"
    hook_cmd = f"{sys.executable} \"{bootstrap_script}\""

    startup_hooks = gateway.get("startup_hooks") or []
    if not isinstance(startup_hooks, list):
        startup_hooks = []

    for hook in startup_hooks:
        if isinstance(hook, str) and "auto_bootstrap.py" in hook:
            return False, "startup hook 已配置，跳过"
        if isinstance(hook, dict):
            cmd = hook.get("command", "")
            if "auto_bootstrap.py" in cmd:
                return False, "startup hook 已配置，跳过"

    startup_hooks.append(hook_cmd)
    gateway["startup_hooks"] = startup_hooks
    config["gateway"] = gateway

    save_config(config)
    return True, f"已配置 gateway startup hook（gateway 启动时自动执行 auto_bootstrap.py）"


# ─── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="凭证池技能一键部署")
    parser.add_argument("--skip-cron", action="store_true", help="跳过 cron job 注册")
    parser.add_argument("--skip-bootstrap", action="store_true", help="跳过 gateway startup hook 配置")
    parser.add_argument("--sync-only", action="store_true", help="只做首次同步（= --skip-cron --skip-bootstrap）")
    parser.add_argument("--bitable-app-token", help="用户提供的飞书多维表格 App Token")
    parser.add_argument("--bitable-table-id", help="可选：在该多维表格中使用的凭证表 Table ID")
    parser.add_argument("--non-interactive", action="store_true", help="缺少必要信息时不等待输入，直接输出下一步指引")
    args = parser.parse_args()

    skip_cron = args.skip_cron or args.sync_only
    skip_bootstrap = args.skip_bootstrap or args.sync_only

    print("=" * 60)
    print("🔧 凭证池技能一键部署 v7.17.0")
    print("=" * 60)
    print(f"技能目录: {SKILL_DIR}")
    print(f"Hermes 配置: {get_config_path()}")
    print()

    # Step 1: 检查飞书机器人凭证
    print("📋 Step 1/5: 检查飞书机器人凭证 ...")
    feishu_ok, feishu_source, feishu_id = check_feishu_credentials()
    if feishu_ok:
        print(f"   ✅ 飞书凭证可用（来源: {feishu_source}, app_id: {feishu_id[:10]}...）")
    else:
        print("   ❌ 未找到飞书凭证")
        print()
        print("   请用以下任一方式配置飞书凭证：")
        print("   1. 环境变量: FEISHU_APP_ID + FEISHU_APP_SECRET")
        print("   2. config.yaml: platforms.feishu.extra.app_id / app_secret")
        print("   3. config.yaml: secrets.feishu.app_id / app_secret")
        print()
        print("   配置完成后重新运行本脚本。")
        return 1
    print()

    # Step 2: 表格权限、结构与持久化配置
    print("🗂️  Step 2/5: 检查飞书表格权限并准备凭证表 ...")
    configured_app, configured_table = _configured_bitable_ids()
    app_token = args.bitable_app_token or configured_app
    table_id = args.bitable_table_id or configured_table
    try:
        app_token = prompt_for_bitable(app_token, args.non_interactive)
        table_id, table_name, created_fields = provision_bitable(app_token, table_id)
        print(f"   ✅ 表格权限可用：{table_name}（{table_id}）")
        if created_fields:
            print(f"   ✅ 已创建标准字段：{', '.join(created_fields)}")
        else:
            print("   ✅ 标准字段已齐全")
    except RuntimeError as exc:
        print(f"   ❌ {exc}")
        print("   需要机器人具备多维表格的读取、写入、建表和新增字段权限，并已被授予目标表格的编辑权限。")
        print("   部署未激活：不会注册 cron job 或 Gateway 启动钩子。")
        return 2
    print()

    # Step 3: 首次完整同步，作为启用门禁
    print("🔄 Step 3/5: 运行首次完整同步（至少需要一条健康凭证）...")
    sync_ok, sync_output = run_first_sync()
    if sync_ok:
        print("   ✅ 同步成功")
        if sync_output:
            print(f"   {sync_output.splitlines()[-1]}")
    else:
        print(f"   ❌ 同步失败: {sync_output}")
        print("   请填写有效凭证并检查网络与飞书权限后重试。")
        print("   部署未激活：不会注册 cron job 或 Gateway 启动钩子。")
        return 2
    print()

    # Step 3: 注册 cron
    if not skip_cron:
        print("⏰ Step 4/5: 注册定时同步 cron job ...")
        cron_done, cron_msg = register_cron_job()
        if cron_done:
            print(f"   ✅ {cron_msg}")
        else:
            print(f"   ℹ️  {cron_msg}")
        print()
    else:
        print("⏰ Step 4/5: 跳过 cron job 注册（--skip-cron）")
        print()

    # Step 4: gateway startup hook
    if not skip_bootstrap:
        print("🚀 Step 5/5: 配置 gateway startup hook ...")
        boot_done, boot_msg = configure_bootstrap_hook()
        if boot_done:
            print(f"   ✅ {boot_msg}")
        else:
            print(f"   ℹ️  {boot_msg}")
        print()
    else:
        print("🚀 Step 5/5: 跳过 gateway startup hook（--skip-bootstrap）")
        print()

    # 验证汇总
    print("=" * 60)
    print("✅ 部署完成")
    print("=" * 60)
    print()
    print("📊 当前状态：")

    config = load_config()
    model = config.get("model") or {}
    if isinstance(model, dict):
        print(f"   主模型: {model.get('default', '?')} ({model.get('provider', '?')})")
    fb = config.get("fallback_providers") or []
    if isinstance(fb, list):
        print(f"   Fallback: {len(fb)} 个")

    auth_path = get_auth_path()
    if auth_path.exists():
        try:
            with open(auth_path, "r", encoding="utf-8") as f:
                auth = json.load(f)
            pool = auth.get("credential_pool", {})
            total = sum(len(v) for v in pool.values() if isinstance(v, list))
            print(f"   凭证池: {len(pool)} 个 provider，共 {total} 条凭证")
        except Exception:
            pass

    if not skip_cron:
        cron_path = get_cron_jobs_path()
        if cron_path.exists():
            try:
                with open(cron_path, "r", encoding="utf-8") as f:
                    cron_data = json.load(f)
                names = [j.get("name", "") for j in cron_data.get("jobs", []) if j.get("enabled")]
                print(f"   Cron jobs: {len(names)} 个启用中")
            except Exception:
                pass

    print()
    print("💡 提示：")
    print("   - 下次 gateway 重启时 auto_bootstrap 会自动选健康凭证")
    print("   - 每 2 小时自动同步一次飞书凭证状态")
    print("   - 手动切换凭证: python scripts/switch_next.py")
    print("   - 手动全量同步: python scripts/sync_credential_pool.py")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
