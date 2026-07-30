# 主模型切换功能参考

## 背景

`switch_next.py` 原来的逻辑只删除凭证池中的活跃凭证，让 Hermes 自动选下一个。但这**不改变运行时 config.yaml 的主模型**，切换后 Hermes 仍然用原来的模型和 API Key 调用——切换无效。

## 修复方案

### 1. sync_credential_pool.py 输出 records

在 `sync()` 末尾添加 `__RECORDS__` JSON 行，输出飞书记录的字段供 `switch_next.py` 使用：

```python
print('__RECORDS__' + json.dumps([{
    'provider': r['provider'],
    'provider_name': r['provider_name'],
    'label': r['label'],
    'model': r['model'],
    'base_url': r['base_url'],
    'api_key': r['api_key'],
} for r in records], ensure_ascii=False, default=str))
```

### 2. switch_next.py 新增 update_runtime_main_model()

```python
def update_runtime_main_model(model, base_url, api_key):
    runtime_config = Path.home() / 'AppData' / 'Local' / 'hermes' / 'config.yaml'
    with open(runtime_config, encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    config['model'] = {
        'default': model,
        'provider': 'custom',
        'base_url': base_url,
        'api_key': api_key,
    }
    temp_path = runtime_config.with_suffix('.yaml.tmp')
    with open(temp_path, 'w', encoding='utf-8', newline='\n') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    os.replace(temp_path, runtime_config)
    return str(runtime_config)
```

### 3. 匹配逻辑

在 main() 中切换凭证成功后，按 `provider_name`（归一化小写） + `label` 精确匹配飞书记录，找到后更新主模型。

### 4. 原子写入

使用 `temp file + os.replace` 模式，与 `cleanup_fallback_chain()` 一致。

## 已知 Bug 与修复

### Bug 1: 同名 Label 验证失败 + 匹配飞书记录失败

**问题**: 凭证池可以有多个同名 label 的凭证（如两个"GLM免费模型"）。原来的验证用 `new_active["label"] != old_label` 比较，导致同 label 时判断失败。同时，匹配飞书记录时直接用 `new_active["label"]` 查找，同名 label 会匹配到错误的 record。

**修复（分两步）**:

1. **验证**（line 104）: 改用 idx（凭证索引）比较 `new_active["idx"] != old_idx`。hermes auth remove 后不会重新索引，凭证保持原有 idx，因此 idx 比较唯一且可靠。

2. **匹配飞书记录**（line 107-123）: 先用 `new_active["idx"]` 在 `target_creds`（移除前的原始凭证列表）中找到对应的凭证，再用该凭证的 label 匹配飞书记录。这样即使多个凭证 label 相同，也能通过 idx 找到正确的那个：

```python
# 用 idx 在 target_creds 中找到对应的凭证，再用其 label 匹配飞书记录
matched_cred = next(
    (cred for cred in target_creds if cred["idx"] == new_active["idx"]),
    None,
)
if matched_cred:
    matching_record = next(
        (record for record in records
         if provider_name_normalized match
         and record.get("label") == matched_cred["label"]),
        None,
    )
else:
    matching_record = None  # 安全降级
```

### Bug 2: sync 自动移除限流凭证导致无可轮转

**问题**: `sync_credential_pool.py` 的 `check_and_rotate()` 在同步时会自动移除 429 限流的凭证，导致 `switch_next.py` 找不到可轮转的 provider。

**修复**: 添加 `--skip-health-rotate` 参数，`switch_next.py` 调用同步时传入，跳过自动轮转：

```python
# switch_next.py run_sync()
subprocess.run([..., "--skip-health-rotate"], ...)

# sync_credential_pool.py
def sync(skip_rotate=False):
    ...
    check_and_rotate(skip_rotate)

def check_and_rotate(skip_rotate=False):
    if skip_rotate:
        return
    ...
```

### Bug 3: 匹配不到 record 时静默跳过

**问题**: 在 `switch_next.py` 中，如果找不到匹配的飞书记录，主模型更新被静默跳过，用户不知道。

**修复**: 添加 WARNING 输出到 stderr。

## Pitfalls

### 凭证池只有 1 个凭证时无法切换
`switch_next.py` 要求 `len(info["creds"]) > 1` 才认为可轮转。如果每个 provider 都只剩 1 个凭证，脚本报"无活跃凭证"。需要先在飞书表格添加更多凭证。

### 同名 Label 问题（已修复）
飞书表格中同一 provider 下允许同名 Label。验证和匹配逻辑已修复：先用 idx 找到凭证再用 label 匹配 record。但建议用不同 Label 区分（如"GLM免费模型-主"、"GLM免费模型-备"）以便人工排查。

### check_and_rotate 干扰
手动切换时必须跳过健康轮转，否则同步过程会移除限流凭证，破坏切换逻辑。`--skip-health-rotate` 参数已内置在 `switch_next.py` 的 `run_sync()` 中。

## 关键文件

- `scripts/switch_next.py` — 切换逻辑 + 主模型更新
- `scripts/sync_credential_pool.py` — 同步 + __RECORDS__ 输出 + check_and_rotate
- `~/AppData/Local/hermes/config.yaml` — 目标运行时配置
