# -*- coding: utf-8 -*-
content = u"""# 凭证池同步工具 (Credential Pool Sync)

## 概述

将飞书多维表格中的 API Key 自动同步到 Hermes auth.json 凭证池，支持连通性验证和状态回写。

## 快速链接

| 项目 | 链接 |
|------|------|
| GitHub | https://github.com/flamebird07/credential-pool-sync |
| 飞书子表 | https://zcnrpnpxvcyt.feishu.cn/base/YedtbFYKZatu2QsGti9ch7xbnGc?table=tblOSK9HexYVOHBW |

## 飞书表格结构

### 凭证池子表

字段：Provider / Label / 模型 / API Key / Base URL / 优先级 / 状态 / 备注

### 当前记录

| Provider | Label | 模型 | 优先级 |
|----------|-------|------|--------|
| ARK | 火山引擎 ARK | deepseek-v4-flash | 0 |
| longcat | LongCat-2.0 | LongCat-2.0 | 1 |
| xiaomi | mimo-v2.5 | mimo-v2.5 | 2 |

## 使用方式

### 手动同步
python ~/credential-pool-sync/scripts/sync_credential_pool.py

### 添加新 Key
1. 在飞书凭证池子表新增行
2. 填写 Provider / Label / 模型 / API Key / Base URL / 优先级
3. 运行同步脚本

## GitHub
https://github.com/flamebird07/credential-pool-sync
"""
import os
path = os.path.join('//10.0.0.50/obsidian/obs', u'\u51ed\u8bc1\u6c60\u540c\u6b65\u5de5\u5177', 'README.md')
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('OK')
