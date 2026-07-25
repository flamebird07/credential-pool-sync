# 开发规范

## 4步法开发流程

本项目严格执行 4 步法：

### Step 1: Codex CLI 审查
- 识别问题
- 分析代码质量
- 不出方案、不改代码

### Step 2: Codex CLI 出方案
- 生成修复方案
- 明确修改范围
- 不允许修改代码

### Step 3: MiMo Code 执行
- 按批准方案修改代码
- 不得自行增减功能

### Step 4: Codex CLI 复审
- 验证修改是否正确
- 结论：PASS 或 FAIL
- FAIL 则回到 Step 2

## 循环机制

```
Step 1 → Step 2 → Step 3 → Step 4
                ↑               │
                │    FAIL       │
                └───────────────┘
```

## 工具角色

| 工具 | 职责 |
|------|------|
| Codex CLI | 审查、方案、复审 |
| MiMo Code | 执行修改 |
| Hermes | 调度和项目管理 |
