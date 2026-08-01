# 2026-07-31 凭证池崩溃事后分析

## 根因时间线
1. ARK 账号 2103691131（key `44e38313…`）触发火山引擎推理上限，账号级模型服务暂停（429 `SetLimitExceeded`），波及该 key 下所有模型
2. `config.yaml` fallback 链共 8 条逐一失败：3 条同属被封账号（429）、1 条周配额耗尽（429）、1 条 tokenhub `kimi-k3` 返回 402 试用额度耗尽——**402 被 Hermes 核心当作不可重试错误抛出的，整条 fallback 链中断**，导致排在第 6 位的健康凭证永远轮不到
3. 17:09 前后 Hermes 跑凭证池同步自救，但执行的是**旧版 v7.2.0 脚本副本**（`C:\Users\Administrator\credential-pool-sync\scripts\`），直接 Traceback 崩溃
4. 结果：主模型坏、fallback 链断、同步脚本崩，机器人陷入持续报错循环

## Bug 清单

| # | 严重度 | 问题 | 修复状态 |
|---|--------|------|---------|
| B1 | 致命 | fallback 链被单条 4xx 中断（402 不可重试） | Hermes 核心问题，非脚本范围 |
| B2 | 高 | fallback 链数据腐烂（含重复/陈旧条目） | 已修复：Loop 2 report_exhaustion() 清空 fallback_providers |
| B3 | 高 | 新旧两版同步脚本并存，Agent 跑错版本 | 需手动清理旧副本 |
| B4 | 高 | auth.json 凭证池与实际脱节 | 已修复：Loop 3 同步后校验 model.default 是否在池 |
| B5 | 中 | 飞书状态与实测不符 | 已修复：Loop 4 备注策略（PASS 写"验证通过"） |
| B6 | 中 | 版本信息不统一 | 已修复：Loop 1 统一到 v7.11.0 |
| B7 | 低 | 残留锁文件、stackdump | 待清理 |
| B8 | 低 | Gateway spawn 启动失败 | 需通过计划任务启动 |

## 已执行的最小修复（用户手动）
1. 主模型切换为 `glm-5-2-260617 + ark-65b0c9fc… @ ark/api/v3`
2. fallback 链精简为 3 条实测 200 的凭证
3. 通过计划任务重启 gateway
4. 飞书状态校准（8 条记录按实测结果更新）

## 代码修复（本次四步法执行）
参见各 Loop 完成记录。