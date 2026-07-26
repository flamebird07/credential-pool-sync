# 凭证池同步工具 (Credential Pool Sync)

将飞书多维表格中的 API Key 自动同步到 Hermes 凭证池，支持连通性验证、状态回写和定时同步。

## 适用场景

- 多台机器共享同一套 API Key 配置
- 通过飞书表格集中管理 API Key（增/改/删）
- 自动检测无效或限流的 Key

## 前置条件

| 条件 | 说明 |
|------|------|
| Hermes Agent | v0.144+（凭证池功能） |
| Python | 3.10+ |
| 飞书访问权限 | 凭证池子表的阅读+写入权限 |

## 安装

### 1. 克隆仓库

```bash
git clone https://github.com/flamebird07/credential-pool-sync.git
cd credential-pool-sync
```

### 2. 配置飞书环境变量

```bash
cp .env.example ~/.hermes/.env
# 编辑 ~/.hermes/.env，填入真实值
chmod 600 ~/.hermes/.env
```

飞书应用需要具备以下权限：
- bitable:app（多维表格）
- 凭证池子表的读取和写入权限

### 3. 配置 Hermes

在 Hermes config.yaml 的 custom_providers 中注册 Provider：

```yaml
custom_providers:
  - name: ARK
    base_url: https://ark.cn-beijing.volces.com/api/plan/v3
  - name: longcat
    base_url: https://api.longcat.chat/anthropic
  - name: xiaomi
    base_url: https://api.xiaomimimo.com/anthropic
  - name: Z.AI
    base_url: https://open.bigmodel.cn/api/paas/v4
```

Provider 的 name 必须与飞书表格中的 Provider 字段值大小写完全一致。

## 使用

### 手动同步

```bash
cd credential-pool-sync
python scripts/sync_credential_pool.py
```

### 定时自动同步

```bash
hermes cron create credential-pool-sync --schedule "*/30 * * * *" --script cron_sync.sh --no-agent --deliver origin
```

## 飞书表格结构

| 字段名 | 类型 | 说明 | 必填 |
|--------|------|------|:----:|
| Provider | 文本 | Provider 名称 | ✅ |
| Label | 文本 | 显示标签 | |
| API Key | 文本 | API 密钥原文 | ✅ |
| Base URL | 文本 | API 端点地址 | ✅ |
| 优先级 | 数字 | 优先级（0=最高） | |

优先级数值越小，凭证同步顺序越靠前；未填写或无法解析时按 999 处理。同一 Provider 和 Label 的重复记录仅保留优先级最高（数值最小）的一条，优先级相同时保留飞书原始顺序最靠前的一条。

## 安全性说明

| 风险 | 缓解措施 |
|------|----------|
| API Key 明文存储在飞书表格 | 确保飞书表格的访问权限最小化 |
| 飞书 App Secret 存储在 ~/.hermes/.env | 文件权限设为 600 |
| 脚本硬编码 Base Token | 如需更换飞书表格需修改脚本 |
| 凭证传输到 Hermes | 通过 hermes auth add CLI 添加 |

## 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| hermes auth list 看不到新增的 Provider | Hermes 未自动发现 | 检查 config.yaml custom_providers |
| 同步脚本报 401 | 飞书 App Secret 错误 | 检查 ~/.hermes/.env |
| 同步脚本报 429 | API Key 已限流 | 飞书表格状态会更新 |
| Cron job 不执行 | Gateway 未运行 | 运行 hermes gateway run |
| Provider Key 不匹配 | 大小写不一致 | 确保与 config.yaml 完全一致 |

## 项目结构

```
credential-pool-sync/
├── scripts/
│   ├── sync_credential_pool.py
│   └── cron_sync.sh
├── .env.example
├── README.md
└── .gitignore
```

## 许可证

MIT
