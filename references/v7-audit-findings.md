# v7.0.0 审查发现与修复记录

## 背景

2026-07-28 用户报告"config.yaml 为空"，引发了对整个凭证池同步系统的全面审查。
审查工具：Codex CLI `codex exec -s danger-full-access --ephemeral`
执行机器：本地 10.0.0.87

## 审查发现的问题

### P0 级

1. **clear_current() 前置导致飞书状态不可逆丢失**
   - 位置：`sync_credential_pool.py` 的 `sync()` 函数
   - 问题：在同步开始时（健康检查前）就调用 `clear_current(tok)` 清除飞书上的"使用中"标记
   - 影响：如果后续健康检查、auth.json 写入等任何一步失败，飞书状态已经被清除且无法恢复
   - 修复：将所有飞书状态变更（包括 clear_current 和 status updates）延后到 auth.json 原子写入成功之后

2. **auth.json 损坏时无法从飞书重建**
   - 问题：`auth.json` 文件损坏（JSON 解析失败、编码错误、非对象根）时，同步直接抛异常退出
   - 影响：飞书是权威源，但本地缓存损坏后系统无法自愈
   - 修复：新增 `_read_existing_auth()` 函数，遇到不可读文件时以最小有效结构重建，保留可解析的顶层字段

### P1 级

3. **cleanup_feishu_status.py 名不副实**
   - 问题：从不读取 config.yaml，反而把所有健康 key 都标记为"使用中"
   - 影响：执行清理会破坏飞书状态语义，所有健康 key 都显示为当前 Agent 使用中
   - 修复：新增 `active_identity()` 从 config.yaml 读取当前模型，用 `(api_key, base_url, model)` 三元组匹配，只标记活跃凭证

4. **技能目录不自包含**
   - 问题：安装的技能目录只有 SKILL.md + references/，scripts/ 在外部 `%USERPROFILE%\credential-pool-sync` 仓库
   - 影响：技能不是自包含的，start_gateway.py 集成依赖硬编码外部路径
   - 修复：将所有脚本复制到技能目录 `scripts/` 下，更新 start_gateway.py 指向技能目录

5. **飞书凭证硬编码**
   - 问题：`FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 有硬编码默认值
   - 影响：源码泄露 = 凭证泄露
   - 修复：移除硬编码值，新增 `load_feishu_credentials()` 按优先级从环境变量 → Hermes config (secrets.feishu / feishu / channels.feishu) 加载，缺失时报明确错误

### P2 级

6. **切换后无全量刷新（用户核心需求）**
   - 问题：`switch_next.py` 切换成功后，凭证池内其他 key 的健康状态是旧数据
   - 用户要求：每次切换之后重新从飞书读取、健康检查每个 key、刷新飞书状态
   - 修复：切换成功后执行 `run_sync(full=True)` 做全量刷新，失败只告警不回滚

7. **切换失败时只有轻量重试**
   - 问题：首次选不到健康 key 时，`run_sync()` 用的是 `--skip-health-rotate`（只重读飞书，不做健康检查）
   - 影响：失败重试没有实质新信息
   - 修复：失败时执行完整同步（健康检查 + 飞书回写），然后重新选

8. **文档与实现版本脱节**
   - 问题：SKILL.md 写 v6.0，脚本内输出 v2.0，多处功能描述与实际代码不符
   - 修复：统一升到 v7.0.0，文档与实现同步

## "config.yaml 为空"的真相

config.yaml **实际不为空**。造成错觉的原因：
- 三段式部署：技能目录（文档）/ 外部仓库（代码）/ AppData（配置）
- 用户在技能目录或其他地方找 config.yaml，找到空文件或不存在
- 技能文档没有清楚说明运行时配置路径

## 修复后的同步触发矩阵

| 触发时机 | 同步模式 | 实现位置 |
|---------|---------|---------|
| Gateway 启动 | 轻量发现 + 逐候选检查 | auto_bootstrap.py |
| 切换前（默认） | 轻量 | switch_next.py run_sync(full=False) |
| 切换失败重试 | 全量 | switch_next.py run_sync(full=True) |
| 切换成功后 | 全量（best-effort） | switch_next.py 末尾 |
| 手动同步 | 全量 | sync_credential_pool.py |
| 手动同步（快） | 轻量 | sync_credential_pool.py --skip-health-rotate |
| 飞书状态清理 | 全量健康扫描 | cleanup_feishu_status.py |

## 未解决项（本次范围外）

1. **故障触发同步的通用钩子机制**：需要 Hermes 运行时集成（如 gateway 检测到模型错误后自动调用 switch_next），本次只做了脚本层面
2. **POSIX 文件锁支持**：当前仅 Windows (msvcrt.locking)，macOS/Linux 需要 fcntl.flock
3. **单元测试**：本次未添加自动化测试
