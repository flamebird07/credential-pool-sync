# 凭证池同步工具

将飞书多维表格中的 API 凭证同步到 Hermes `auth.json`；支持健康检查、优先级分层、故障切换和飞书状态回写。

## 前置条件

- Windows 上的 Hermes Agent 和 Python 3.10+。
- 飞书应用对目标多维表格具有读写权限。
- Hermes `config.yaml` 中已配置飞书应用凭证，或提供 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。

## 配置

凭证和飞书表格位置都不写入仓库。优先使用环境变量；也可写入本机 `%LOCALAPPDATA%\hermes\config.yaml`：

```yaml
credential_pool_sync:
  bitable_app_token: <飞书多维表格 App Token>
  bitable_table_id: <凭证表 Table ID>
```

对应的环境变量为：

```powershell
$env:FEISHU_APP_ID = "..."
$env:FEISHU_APP_SECRET = "..."
$env:FEISHU_BITABLE_APP_TOKEN = "..."
$env:FEISHU_BITABLE_TABLE_ID = "..."
```

飞书表至少需要以下字段：`Provider`、`Label`、`API Key`、`Base URL`、`模型`、`优先级`、`状态`、`备注`。优先级为 0–9，0 最高；同步只加载第一个拥有健康凭证的优先级档位。

## 使用

```powershell
# 一次性部署：首次同步、每两小时定时任务、Gateway 启动钩子
python scripts/setup.py

# 完整同步（健康检查、auth.json、飞书状态回写）
python scripts/sync_credential_pool.py

# 快速同步（不做健康检查和飞书回写）
python scripts/sync_credential_pool.py --skip-health-rotate

# 切换到下一个健康凭证
python scripts/switch_next.py

# 修复飞书状态或 Provider 展示字段
python scripts/cleanup_feishu_status.py
python scripts/cleanup_feishu_status.py --repair-provider
```

## 安全与运行规则

- API Key 和飞书应用密钥只保存在飞书、本机环境变量或 Hermes 本机配置中；不要提交 `.env`、`config.yaml` 或 `auth.json`。
- `Provider` 的 URL 反推仅用于飞书展示；非内置 Hermes Provider 在 `auth.json` 和 `config.yaml` 中一律使用 `custom`。
- 活跃凭证身份由 `(model, api_key, base_url)` 决定，模型和 URL 会规范化后比较。
- `auth.json` 使用原子写入；当健康检查出现大面积不可用时，完整性保护会保留现有池。

## 项目结构

```
credential-pool-sync/
├── SKILL.md
├── scripts/
│   ├── sync_credential_pool.py
│   ├── switch_next.py
│   ├── auto_bootstrap.py
│   ├── cleanup_feishu_status.py
│   └── setup.py
└── references/
```

## 新机器首次部署

首次执行 `python scripts/setup.py` 会检查飞书机器人能否读取、写入、建表和新增字段。给它一个已有的空白或成熟多维表格：

```powershell
python scripts/setup.py --bitable-app-token app_xxx
```

若该多维表格没有名为 `凭证池` 的数据表，技能会新建该表并补齐标准字段。它会在飞书中提示用户填写 API Key、Base URL 和模型；只有至少一条凭证通过健康检查后，才会启用定时同步和 Gateway 启动钩子。权限、表格或有效凭证不满足时，部署以未激活状态退出，不会让 Hermes 因空凭证池进入自动运转。

当前版本：v7.19.0。
