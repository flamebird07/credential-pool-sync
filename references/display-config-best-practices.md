# 显示配置最佳实践

## 背景

Hermes 的 `config.yaml` 中 `display` 部分控制 UI 多语言、会话启动显示、TTS 语音等。默认英文且信息密集，中文用户体验差。

## 推荐配置

### 必改项

| 配置项 | 默认值 | 推荐值 | 说明 |
|---|---|---|---|
| `display.language` | `en` | `zh` | UI 静态消息（审批提示、网关回复）显示中文 |
| `display.resume_display` | `full` | `minimal` | 新会话只显示一行摘要，不刷屏 |
| `tts.edge.voice` | `en-US-AriaNeural` | `zh-CN-XiaoxiaoNeural` | TTS 语音切换为中文女声 |

### 可选优化项

| 配置项 | 推荐值 | 说明 |
|---|---|---|
| `display.show_reasoning` | `false` | 关闭模型推理过程显示，减少噪音 |
| `display.turn_summary` | `false` | 关闭每轮后的统计摘要 |
| `display.streaming` | `true` | 开启流式输出，响应更快感知 |
| `display.timestamps` | `true` | 显示时间戳，方便回溯 |

## CLI 一键设置

```bash
# 找到 hermes 路径（Windows）
find /c/Users/Administrator/AppData -name "hermes.exe" -type f 2>/dev/null | head -1

# 设置中文 + 精简显示
HERMES="/c/Users/Administrator/AppData/Roaming/uv/tools/hermes-agent/Scripts/hermes.exe"
"$HERMES" config set display.language zh
"$HERMES" config set display.resume_display minimal
"$HERMES" config set tts.edge.voice zh-CN-XiaoxiaoNeural
```

## display.language 支持的语言

`en`（默认）、`zh`（简体中文）、`zh-hant`（繁体中文）、`ja`（日语）、`de`（德语）、`es`（西班牙语）、`fr`（法语）、`tr`（土耳其语）、`uk`（乌克兰语）、`af`（南非荷兰语）、`ko`（韩语）、`it`（意大利语）、`ga`（爱尔兰语）、`pt`（葡萄牙语）、`ru`（俄语）、`hu`（匈牙利语）

**注意**：`display.language` 只翻译静态 UI 消息（审批提示、网关回复等），**不翻译** agent 回复、日志、工具输出。让 agent 用中文回复需要在 prompt 或 system message 中指定。

## resume_display 选项

| 值 | 效果 |
|---|---|
| `full` | 显示之前的对话摘要（默认，信息密集） |
| `minimal` | 只显示一行摘要（推荐，简洁） |

## 验证方法

修改后重启 gateway 生效。新会话启动时应显示中文摘要而非英文详情。

## 常见问题

### Q: 改了 language 但还是英文？
A: 需要重启 gateway。`config.yaml` 修改后不会热加载。

### Q: agent 回复还是英文？
A: `display.language` 只影响 UI 静态消息。agent 回复语言由 prompt/system message 控制。在对话中直接说"请用中文回复"即可。

### Q: resume_display 改了 minimal 但还是显示很多？
A: `minimal` 只在 CLI 模式生效。飞书等消息平台不受此设置影响。
