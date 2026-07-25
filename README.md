# 凭证池同步工具 (Credential Pool Sync)

将飞书多维表格中的 API Key 自动同步到 Hermes auth.json 凭证池，支持连通性验证和状态回写。

## 功能特性

### 🔑 凭证管理
- 从飞书多维表格读取 API Key 配置
- 自动写入 Hermes auth.json 凭证池
- 支持多个 Provider（custom、xiaomi、anthropic 等）
- 按优先级自动排序

### ✅ 连通性验证
- 同步时自动测试每个 API Key 是否有效
- OpenAI 兼容 / Anthropic 兼容双协议支持
- 验证结果回写飞书表格（正常/无效/限流）

### 📊 状态回写
- 飞书表格"状态"字段实时显示验证结果
- ✅ 正常 / ❌ 无效 / ⚠️ 限流 / ⏳ 未验证
- 无效 Key 不会写入凭证池

## 快速开始

### 环境要求
- Python 3.10+
- Hermes Agent（凭证池功能）
- 飞书多维表格访问权限

### 安装

```bash
git clone https://github.com/flamebird07/credential-pool-sync.git
cd credential-pool-sync
```

### 配置飞书凭证

在 `scripts/sync_credential_pool.py` 中配置：
```python
FEISHU_APP_ID = "your_app_id"
FEISHU_APP_SECRET = "your_app_secret"
BASE_TOKEN = "your_base_token"
TABLE_ID = "your_table_id"
```

### 手动同步

```bash
python scripts/sync_credential_pool.py
```

### 定时自动同步

```bash
hermes cron create "0 6 * * *"   --name "credential-pool-sync"   --prompt "运行凭证池同步脚本"   --deliver local
```

## 飞书表格结构

### 凭证池子表

| 字段名 | 类型 | 说明 |
|--------|------|------|
| Provider | 文本 | provider 名称 |
| Label | 文本 | 显示标签 |
| 模型 | 文本 | 模型名称 |
| API Key | 文本 | API 密钥 |
| Base URL | 文本 | API 端点地址 |
| 优先级 | 数字 | 优先级（0=最高） |
| 状态 | 单选 | ✅正常/❌无效/⚠️限流/⏳未验证 |
| 备注 | 文本 | 用途说明或错误信息 |

### 链接

https://zcnrpnpxvcyt.feishu.cn/base/YedtbFYKZatu2QsGti9ch7xbnGc?table=tblOSK9HexYVOHBW

## 项目结构

```
credential-pool-sync/
├── scripts/
│   └── sync_credential_pool.py   # 主同步脚本（含验证）
├── README.md                     # 项目说明
├── AGENTS.md                     # 开发规范（4步法）
└── .gitignore                    # Git 忽略规则
```

## 验证流程

同步脚本执行时会：
1. 读取飞书表格所有记录
2. 全部标记为"⏳ 未验证"
3. 逐条测试连通性（HTTP 请求最小 token）
4. 有效 Key → 写入 auth.json + 标记"✅ 正常"
5. 无效 Key → 不写入 + 标记"❌ 无效" + 记录错误信息
6. 限流 Key → 不写入 + 标记"⚠️ 限流"

## 开发规范

本项目采用 **4步法** 开发流程，详见 [AGENTS.md](AGENTS.md)。

## 许可证

MIT License

## 联系方式

- GitHub: [flamebird07](https://github.com/flamebird07)
- 项目地址: https://github.com/flamebird07/credential-pool-sync
