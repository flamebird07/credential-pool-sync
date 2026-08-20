
---
name: credential-pool-sync
description: "Synchronize, health-check, rotate, and reconcile credentials stored in Feishu Bitable for Hermes and Claude Code. Use when enabling the Claude Code credential pool, switching exhausted credentials, or managing Feishu credential-pool health."
version: 7.22.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [credential-pool, feishu, sync, devops]
    related_skills: [feishu-bitable, hermes-agent]
---

# Credential Pool Sync v7.21.0

This skill synchronizes API credentials from Feishu into Hermes `auth.json`, rotates the active model in `config.yaml`, and reconciles Feishu health/in-use status. The installed skill directory is canonical at:

`%LOCALAPPDATA%\hermes\skills\devops\credential-pool-sync`

The external `credential-pool-sync` repository is the development source only. Runtime scripts live in this skill's own `scripts/` directory, so the installed skill is standalone.

## Claude Code 独立凭证池

当用户说“启用 Claude 凭证池”时，执行：

```powershell
python scripts/enable_claude_credential_pool.py
```

脚本复用 Hermes 已配置的飞书机器人和多维表格文档，新建或复用独立的 `Claude Code 凭证池` 数据表。该表字段与主凭证池一致，但凭证、状态和轮换完全独立。它会更新 Claude Code 设置，并注册 Windows 登录后后台启动的本地代理。代理遇到额度不足、余额不足或 429 限流，会换用下一条 Claude 专用凭证并重试同一请求。

首次启用后，Hermes 与 OpenCode 四步法调用同一台机器上的 Claude Code CLI，无需重复配置。不要把 Claude 专用表的记录写回 Hermes `auth.json` 或主凭证池表。

Feishu application credentials and Bitable identifiers must not be embedded in source. Read `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_BITABLE_APP_TOKEN`, and `FEISHU_BITABLE_TABLE_ID` from the environment, or place them in the local Hermes `config.yaml` under `credential_pool_sync`.

## Four-Step Method Execution

This skill was optimized using the user's required four-step method:

1. **Step 1: Codex CLI Review** - Identified Feishu UI status management issues
2. **Step 2: Codex CLI Solution** - Created detailed 11-point fix plan (--ephemeral)
3. **Step 3: Codex CLI Implementation** - Executed fixes (-s danger-full-access)
4. **Step 4: MiMo Code Review** - Verified implementation and identified issues

The method requires strict separation: reviewers cannot be implementers, and each step must be completed before the next. See `references/four-step-method-execution.md` for detailed execution patterns.

## Setup / Deployment

After installing the skill on a new machine, run **once** to complete full deployment:

```bash
cd %LOCALAPPDATA%\hermes\skills\devops\credential-pool-sync
python scripts/setup.py
```

This guided setup automatically does:
1. Checks Feishu robot credentials and access to the user-provided Bitable.
2. Reuses a `凭证池` table or creates one in an empty Bitable, then adds every required field that is missing.
3. Runs a full first sync and requires at least one health-checked credential.
4. Only after that gate succeeds, registers a cron job every 2 hours (`0 */2 * * *`) and configures the Gateway startup hook.

If the table, required permissions, or a healthy credential is missing, setup exits without enabling either automation. Give the App Token with `--bitable-app-token app_xxx` (and, if needed, `--bitable-table-id tbl_xxx`), or configure the corresponding environment variables/local Hermes configuration. The user must grant the Feishu bot access to the supplied Bitable; this skill never creates a top-level document without a user-controlled folder.

Additional options:
- `--skip-cron` - Skip registering the cron job
- `--skip-bootstrap` - Skip configuring the gateway startup hook
- `--sync-only` - Only run the first sync (equivalent to both above)

The script is idempotent—re-running it is safe and won't duplicate cron jobs or startup hooks.

## Trigger matrix

| User intent / event | Command | Sync mode | Result |
|---|---|---|---|
| Synchronize or refresh all credentials | `python scripts/sync_credential_pool.py` | Full | Reads Feishu, health-checks every key, atomically writes `auth.json`, then reconciles Feishu |
| Refresh local pool quickly | `python scripts/sync_credential_pool.py --skip-health-rotate` | Lightweight | Reads Feishu and writes `auth.json`; skips health checks, Feishu writes, and fallback cleanup |
| Switch to the next credential | `python scripts/switch_next.py` | Full sync + health-check | Reads Feishu, writes `auth.json` via sync_credential_pool.py, then health-checks and rotates to next healthy credential; post-switch updates Feishu status for both old and new credential |
| Switch using the existing local pool | `python scripts/switch_next.py --skip-sync` | No sync subprocess; reads Feishu directly | Reads Feishu records directly without updating `auth.json`; full sync retry on first pass failure |
| Gateway startup | `python scripts/auto_bootstrap.py` | Lightweight discovery plus per-candidate checks | Selects the first healthy non-current credential |
| Repair Feishu status | `python scripts/cleanup_feishu_status.py` | Full health scan | Matches active `(api_key, base_url, model)` from `config.yaml`; only that credential is marked in-use |
| Repair Feishu Provider field | `python scripts/cleanup_feishu_status.py --repair-provider` | Read-only (no health check) | Infers standard Provider (ARK, OPENAI, etc.) from Base URL; updates Feishu when current is empty or "custom" |

## Full versus lightweight sync

A full sync reads Feishu, health-checks all records, builds the complete local credential pool, atomically commits `auth.json`, and only afterward updates Feishu statuses. This ordering prevents a failed health check or local write from corrupting Feishu's derived state.

A lightweight sync (`--skip-health-rotate`) reads Feishu and rebuilds `auth.json` without health checks, Feishu status writes, or fallback cleanup. It is intended for fast discovery, not authoritative reconciliation.

## Rotation behavior

`switch_next.py` identifies the current credential by the `(model, api_key, base_url)` triple-key (model + API key + base URL, all normalized). This is the user's preferred uniqueness rule — see "Pitfall: config.yaml 'default' key vs 'model' key identity mismatch" below for why this matters. Candidates are ordered by Feishu return order (not priority), and each candidate is health-checked before switching. `config.yaml` is written atomically with a UUID temporary file, flush, `fsync`, `os.replace`, and a Windows `msvcrt.locking` lock.

If the first pass finds no healthy candidate, the script runs one full sync and retries selection exactly once. This applies even with `--skip-sync`; that flag skips only the initial sync subprocess.

**Note** (v7.11.0): The default `switch_next.py` behavior now runs a full sync (`run_sync(full=True)`) before each switch, ensuring `auth.json` is always up-to-date. The post-switch Feishu status update cleanup was also added for `auto_bootstrap.py` — it now removes the old credential's Agent marker after switching.

## Pitfall: URL healing causes identity mismatch in fallback chain

When `tk()` discovers a corrected URL (e.g., `api/plan` → `api/plan/v1`), the `_sync_unlocked()` function stores the health result with the **healed** URL. However, `sync_fallback_providers()` re-normalizes the original Feishu records (which still have the **old** URL), so the `health_results` lookup misses. The result: invalid/rate-limited credentials can be added to the fallback chain.

**Fix** (v7.3.1): A `healed_urls` dict is passed from `_sync_unlocked()` to `sync_fallback_providers()`. Before each health_results lookup, if the original identity matches a healed entry, the healed URL is used. This ensures the lookup key matches what was stored.

## Pitfall: Model name case sensitivity in identity matching

The `identity()` function in `cleanup_feishu_status.py` returns `(api_key, base_url, model)` without normalizing the model name. If `config.yaml` has `DeepSeek-V4` but Feishu has `deepseek-v4`, the identity comparison fails, causing incorrect usage-status reconciliation.

**Fix** (v7.3.1): `identity()` now normalizes model: `str(model or "").strip().lower()`.

## Pitfall: 'custom' provider write-back inconsistency across scripts

When `_normalise_record()` correctly infers a standard Provider (e.g., "ARK") from a Base URL, but the Feishu table still shows "custom", the root cause is usually a condition in **another script** that only checks for empty provider, not "custom".

**Symptoms**: Feishu UI shows "Provider: custom" while the local `__RECORDS__` output already shows "ARK".

**Root cause**: The `_normalise_record()` function (in `sync_credential_pool.py`) correctly handles "custom" → "ARK" inference for local processing, but the Feishu write-back condition in `_sync_unlocked()` (`url_updates`) or switch scripts only checks `not original_provider` — missing "custom" entirely.

**Scripts affected and their fix pattern**:

| Script | Function | Wrong condition | Correct condition |
|---|---|---|---|
| `sync_credential_pool.py` | `url_updates` | `if not original_provider and detected_provider:` | `if detected_provider and (not original_provider or original_provider.lower() == "custom"):` |
| `switch_next.py` | `_normalise_record()` | `if not provider:` | `if not provider or provider.lower() == "custom":` |
| `switch_next.py` | `update_runtime_main_model()` | `"provider": detect_provider(...) or "custom"` | `"provider": HERMES_CUSTOM_PROVIDER` (hardcoded, v7.9.0+) |
| `auto_bootstrap.py` | `write_runtime_config()` | `"provider": detect_provider(...) or "custom"` | `"provider": "custom"` (hardcoded, already correct) |
| `cleanup_feishu_status.py` | `cleanup_feishu_status()` | Provider never updated | Infer Provider, pass to `us()` alongside status |

**Fix**: Every script that reads from Feishu and writes back must check for both empty and "custom" before inferring Provider. The `detect_provider()` fallback chain should be: `record.get("provider") or detect_provider(base_url) or "custom"`.

**Prevention**: When adding a new script that reads Feishu records and writes to config.yaml or back to Feishu, copy the Provider inference pattern from `sync_credential_pool.py._normalise_record()` — do not re-invent the condition.

## Pitfall: 'Wrong model displayed' ≠ model switch needed

When a user reports that Hermes shows the wrong model (e.g. a 429'd model name in the session banner), **always read `config.yaml` first before attempting any credential switch or health check**. The fallback chain may have already rotated the active model in `config.yaml`. The display discrepancy is often a session-cache or Hermes display-layer issue, not a config problem. Running `switch_next.py` or health checks prematurely wastes time and may rotate to a worse credential. Steps:

1. Read `config.yaml` → check `model.default` and `model.api_key`.
2. If the model matches what the user expects, the issue is display-layer, not credential-pool.
3. Only if `config.yaml` still points to the 429'd model should you proceed with `switch_next.py` or a full sync.

## Pitfall: config.yaml 'default' key vs 'model' key identity mismatch (switch_next)

**Problem**: `get_current_model_config()` in `switch_next.py` returns the model dict from `config.yaml`, which uses `default` as the key for the model name (e.g. `{default: "DeepSeek-V4-Flash", provider: "ARK", ...}`). But `identity()` reads `record.get("model")` — so when the current config is passed to `identity()`, the model name is always `None` because the config has `default` not `model`.

**Root cause chain**: `get_current_model_config()` returns `{default: "DeepSeek-V4-Flash"}` → `identity()` reads `record.get("model")` → returns `None` → current credential is not found in Feishu records → `ordered_candidates()` starts from the first record → first healthy candidate is always selected → "switch succeeds" but never changes.

**Fix** (v7.6.0): `get_current_model_config()` now copies the model dict and maps `default` → `model`:
```python
current = model_config.copy()
current["model"] = str(model_config.get("default", "") or "").strip()
return current
```

**Design principle**: credential identity is `(model, api_key, base_url)` — model lowercased + API key stripped + base URL normalized via `normalise_base_url()`. This was explicitly chosen by the user because:
- Multiple Feishu records can share the same API key (different models under one account)
- `base_url` must be included to distinguish credentials on the same API key but different endpoints
- `normalise_base_url()` ensures stable comparison despite URL suffix variations

## Pitfall: Identity inconsistency across scripts (RESOLVED v7.7.0)

All scripts now use the unified `(model, api_key, base_url)` triple-key identity format:

| Script | Identity format | Status |
|--------|----------------|--------|
| `switch_next.py` | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `cleanup_feishu_status.py` | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `sync_credential_pool.py` (_sync_unlocked) | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `sync_credential_pool.py` (sync_fallback_providers) | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |

**Unified identity() function** (defined in `sync_credential_pool.py`, imported by others):
```python
def identity(model, api_key, base_url):
    return (str(model or '').strip().lower(), str(api_key or '').strip(), normalise_base_url(base_url))
```

**Design principle**: credential identity is `(model, api_key, base_url)` — model lowercased + API key stripped + base URL normalized. This was explicitly chosen by the user because:
- Multiple Feishu records can share the same API key (different models under one account)
- `base_url` must be included to distinguish credentials on the same API key but different endpoints
- `normalise_base_url()` ensures stable comparison despite URL suffix variations

## Corrupt auth.json recovery

If `auth.json` is missing, invalid JSON, unreadable, incorrectly encoded, or has a non-object root, full/lightweight synchronization treats it as an empty pool and rebuilds it from Feishu. When the existing document is parseable, unrelated top-level keys such as `version`, `providers`, and other metadata are preserved. A completely unusable file is replaced atomically with a minimal valid structure.

## Feishu credentials

No Feishu application credentials are embedded in source. Configure both environment variables:

```powershell
$env:FEISHU_APP_ID = "..."
$env:FEISHU_APP_SECRET = "..."
```

Alternatively configure `secrets.feishu.app_id` and `secrets.feishu.app_secret` in `%LOCALAPPDATA%\hermes\config.yaml`. Top-level `feishu` and `channels.feishu` mappings are also recognized for compatibility. Missing credentials produce a clear error without logging secret values.

## Cleanup semantics

`cleanup_feishu_status.py` reads the active model from Hermes `config.yaml` and compares the normalized `(model, api_key, base_url)` identity. A healthy active credential receives this agent's in-use marker in the status field (via `status_add()`). Other healthy credentials are marked `✅ 正常`, or retain another agent's in-use marker via `status_remove()`. Unhealthy records lose only this agent's marker and receive the health result. It never marks every healthy credential as in-use.

## Provider Reverse-Inference (Feishu display only — do NOT leak into Hermes config)

`detect_provider(base_url)` infers a provider label from the Base URL hostname. This is for **Feishu display/Provider field repair only** — the inferred value must NEVER be used in Hermes configuration (auth.json pool keys, config.yaml model.provider, or fallback_providers entry provider).

| URL Hostname | Inferred Label |
|---|---|
| `ark.cn-beijing.volces.com` | ARK |
| `open.bigmodel.cn` | Z.AI |
| `api.openai.com` | OPENAI |
| `api.anthropic.com` | ANTHROPIC |
| `api.deepseek.com` | DEEPSEEK |
| `api.moonshot.cn` | MOONSHOT |
| `dashscope.aliyuncs.com` | DASHSCOPE |

### Usage rules (v7.9.0+)

| Context | Provider value | Reason |
|---|---|---|
| Feishu table Provider field | `detect_provider()` result (e.g. "ARK") | For display/identification only |
| auth.json credential_pool key | `custom:{lowercase_label}` (e.g. `custom:ark`) | Hermes pool key format requires `{provider}:{name}` |
| config.yaml model.provider | `"custom"` | ARK is not a built-in Hermes provider |
| fallback_providers entry provider | `"custom"` | Same reason — URL-backed records are custom providers |

### Pitfall: detect_provider() leaking into Hermes config (v7.9.0 Critical)

**Problem**: `detect_provider()` returns "ARK" from the URL. When this value is used as the pool key prefix (instead of `custom:ark`), auth.json becomes `{"ark": [...]}` instead of `{"custom:ark": [...]}`. Hermes credential pool lookup uses `{provider}:{name}` keys — when the key format is wrong, ALL credentials become invisible, causing "Unknown provider 'ark'" errors and complete system crash.

**One-line rule**: `detect_provider()` is for Feishu only. Hermes config always uses `custom` for non-standard providers.

**Three leak paths fixed in v7.9.0**:
1. **Pool key** (line 721): `_hermes_pool_key(p)` adds `custom:` prefix for non-standard providers
2. **Fallback entry** (line 482): `"provider": HERMES_CUSTOM_PROVIDER` — hardcoded to `"custom"`
3. **Config write** (switch_next.py, auto_bootstrap.py): `record_provider = "custom"` — no longer uses `detect_provider()`

To repair existing Feishu records with empty or stale Provider fields:
```bash
python scripts/cleanup_feishu_status.py --repair-provider
```

### Pitfall: `custom:custom` credential pool key (v7.14.0)

**Problem**: When a record's provider is already the bare `custom` value, `_hermes_pool_key()` prepended the `custom:` prefix again, producing a `custom:custom` key in `auth.json` instead of `custom`. Hermes pool lookups expect `{provider}:{name}` keys where the provider namespace is `custom` — a `custom:custom` key is either invisible to lookup or treated as a distinct bogus provider.

**Root cause**: `_hermes_pool_key()` added `custom:` for every provider not in `STANDARD_PROVIDERS`. Since `custom` itself is not in the standard set, a provider already normalized to `custom` got double-prefixed.

**Fix** (v7.14.0): `_hermes_pool_key()` returns `custom` directly when `pk == HERMES_CUSTOM_PROVIDER` before the standard-provider check. Non-standard providers other than `custom` still get the `custom:` prefix as before.

```python
def _hermes_pool_key(provider):
    pk = str(provider or "").strip().lower().replace(" ", "-")
    if pk == HERMES_CUSTOM_PROVIDER:
        return HERMES_CUSTOM_PROVIDER
    if pk not in STANDARD_PROVIDERS:
        pk = f"custom:{pk}"
    return pk
```

**Prevention**: When normalizing a provider to a pool key, treat the `custom` sentinel as a terminal value — never re-name it. Only non-standard providers that are *not* already `custom` should receive the namespace prefix.

## Feishu Status Field Rules

The Feishu table's **状态** (status) column is the canonical display of which Hermes Agent is using each credential. Agent usage is written to the status field, NOT to the **备注** (notes) field.

### Status field assignment rules

Every credential's status field is determined by the following unified logic across all three scripts (`sync_credential_pool.py`, `cleanup_feishu_status.py`, `switch_next.py`):

| Condition | Action | Example |
|---|---|---|
| Valid + identity matches current Agent | `status_add(current_status, agent_name)` | `🔄 周公瑾使用中` |
| Valid + identity does NOT match current Agent | `status_remove(current_status, agent_name)` | `✅ 正常` (no Agents) / `🔄 甘宁使用中` (other Agent) |
| Invalid / rate-limited | `health_status(False, probe_status, error)` | `❌ 无效` / `⛔ 限流` |

### Multi-Agent support

`status_add()` concatenates multiple Agents with `+`:

```python
def status_add(s, name):
    names = "+".join(dict.fromkeys(agents(s) + [name]))
    return f"🔄 {names}使用中"
```

Example: `🔄 周公瑾+甘宁使用中` — both Agents are using the same credential.

### What the 备注 field should contain

The 备注 field is reserved for human-readable notes (e.g. "额度已用完", "验证通过", "HTTP 429: rate limited"). It should NOT contain Agent usage markers. Do not call `usage_add()`/`usage_remove()` on the 备注 field — these functions are deprecated and removed from `switch_next.py`.

### Pitfall: Stale 备注 notes survive across health check cycles

**Problem**: When a credential fails a health check (e.g. returns 429 → "额度已用完"), the error is written to 备注. If the same credential later passes the health check (e.g. quota resets, endpoint corrected), the status field correctly shows "✅ 正常" but the old error note in 备注 is never cleared. The user sees "status: ✅ 正常, note: 额度已用完" — a contradictory and misleading state.

**Root cause**: Several scripts preserve the existing note via `f.get("备注", "")` or `current_note` on the success path, never updating it to "验证通过". On the failure path, some scripts prefer the old note over the current error (`current_note or error`), so the note never reflects the current health state.

**Affected locations and fix pattern** (unified note policy):

| Script | Success path | Failure path |
|---|---|---|
| `sync_credential_pool.py` `_sync_unlocked()` iv=True branch | `new_note = "验证通过"` | `new_note = e or status_specific_fallback` |
| `sync_credential_pool.py` `_sync_unlocked()` S_R branch | n/a | `new_note = e or "额度已用完"` |
| `sync_credential_pool.py` `_sync_unlocked()` else (S_I/S_U) branch | n/a | `new_note = e or ("Key 无效" if s==S_I else "验证失败")` |
| `sync_credential_pool.py` `_normalise_record()` reject path | n/a | Must write note, not `None` |
| `cleanup_feishu_status.py` `cleanup_feishu_status()` | `new_note = "验证通过"` | `new_note = error or status_specific_fallback` |
| `switch_next.py` `first_healthy()` failed candidates | n/a | `note=error or status_specific_fallback` (not bare `error`) |
| `switch_next.py` `main()` old credential | n/a | Derive note from old_status, not hardcoded "额度已用完" |
| `auto_bootstrap.py` `try_switch()` | Writes "验证通过" (correct) | **Missing**: failed candidates never update Feishu |

**Unified note policy** (must be consistent across all scripts):
- Health check PASS → `"验证通过"`
- S_R / 限流 → `error or "额度已用完"`
- S_I / 无效 → `error or "Key 无效"`
- S_U / 不可用 → `error or "验证失败"`

**Prevention rule**: Never preserve the existing 备注 value when writing a health-check result. The note must always reflect the *current* health check outcome, not historical state. Human-edited notes are not preserved — this is acceptable because the note's purpose is to show the latest automated health check result.

### Script-specific implementation

| Script | Function | How it sets status | Notes |
|---|---|---|---|
| `sync_credential_pool.py` | `_sync_unlocked()` | Per-record `status_add`/`status_remove`/`health_status` | `clear_current()` was removed in v7.3.3 — status is now maintained per-record |
| `cleanup_feishu_status.py` | `cleanup_feishu_status()` | `status_add`/`status_remove`/`health_status` | Uses `probe_status` (not `health_status`) to avoid fn name collision |
| `switch_next.py` | Post-switch update | `status_remove` on old, `status_add` on new | `usage_add`/`usage_remove` functions removed in v7.3.3 |
| `auto_bootstrap.py` | `try_switch()` → success | `status_remove` on old, `status_add` on new | Fixed v7.11.0: now also cleans up old credential's Agent marker via `status_remove` |

## References

- `references/2026-07-31-crash-postmortem.md` — 2026-07-31 凭证池崩溃事后分析（B1-B8 根因、修复状态、崩溃时间线、经验教训）
- `references/four-step-method-execution.md` — 四步法执行模式文档
- `references/main-model-switch.md` — 主模型切换流程
- `references/v7-audit-findings.md` — 2026-07-28 v7.0.0 全面审查发现的问题清单、修复详情和触发矩阵
- `references/v7.1-audit-findings.md` — 2026-07-29 v7.1.0 审查：subprocess编码修复、cron_sync.sh路径修正、auto_bootstrap飞书回写补全
- `references/v7.3-crash-analysis.md` — 2026-07-29 429 级联崩溃分析
- `references/v7.3.2-provider-writeback-fix.md` — 2026-07-30 v7.3.2 Provider 字段回写不一致修复
- `references/v7.3.3-status-field-agent-display.md` — 2026-07-30 v7.3.3 状态栏 Agent 标记显示修复
- `references/v7.4.0-integrity-protection.md` — 2026-07-30 v7.4.0 凭证池完整性保护
- `references/v7.6.0-identity-mismatch.md` — 2026-07-30 v7.6.0 identity 双重确认修复
- `references/v7.8.0-stale-note-fix.md` — 2026-07-30 v7.8.0 备注栏残留旧值修复
- `references/v7.9.0-detect-provider-leak.md` — 2026-07-30 v7.9.0 detect_provider 泄漏修复
- `references/v7.9.1-endpoint-base-url-fix.md` — 2026-07-30 v7.9.1 endpoint_base_url() 修复
- `references/v7.10.0-comprehensive-fix.md` — 2026-07-30 v7.10.0 综合修复（BUG-01~17）

## GitHub Synchronization

The remote repository is at `github.com/flamebird07/credential-pool-sync`. The installed skill is the canonical runtime — sync it to GitHub when version increments.

### Sync procedure

```bash
# 1. Clone fresh (avoids local repo corruption)
git clone https://github.com/flamebird07/credential-pool-sync.git /tmp/credential-pool-clone
cd /tmp/credential-pool-clone

# 2. Compare remote vs installed skill versions
# Check SKILL.md version and all script sizes
diff <(git ls-tree -r --name-only HEAD | sort) \
     <(ls %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync/**/*)

# 3. Overwrite with installed skill files
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\SKILL.md .
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\scripts\\*.py scripts/
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\scripts\\*.sh scripts/
# Copy any new reference files
for f in %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\references\\*.md; do
  cp "$f" references/
done

# 4. Commit and push
git add -A
git commit -m "vX.Y.Z: <description>"
git push origin master
git tag vX.Y.Z
git push origin vX.Y.Z
```

### Pitfall: Local git repo corruption

**Symptoms**: `git status` reports `fatal: unable to read <hash>`, `.git/config` is missing, or `git remote -v` returns empty despite `remotes/origin/master` existing.

**Root cause**: The `.git/config` file can be deleted independently from the git objects directory. If the index references missing blob objects, all git operations fail.

**Fix**: Never try to repair a corrupted local repo. Clone fresh:
```bash
rm -rf credential-pool-sync
git clone https://github.com/flamebird07/credential-pool-sync.git
```
Then overwrite with the installed skill files as described above.

### Pitfall: Remote tag says v7.11.0 but files are outdated

The remote `v7.11.0` tag may have been set on a commit that only updates the tag name, not the actual script files. Always verify file content (not just the tag) by comparing the installed skill's script sizes and version strings with the remote's `HEAD`.

### Terminal failure recovery for git operations

When the Hermes `terminal` tool is completely unresponsive (all commands time out), use `execute_code` with `subprocess.run` and direct paths to `git.exe`:

```python
import subprocess
git = r"C:\Program Files\Git\bin\git.exe"
result = subprocess.run([git, "status"], capture_output=True, text=True, timeout=10, cwd=repo_path)
print(result.stdout)
```

This bypasses the broken shell session entirely. The `execute_code` tool uses a Python sandbox with its own process management, independent of the terminal's shell state.

## Files

```text
credential-pool-sync/
├── SKILL.md
├── references/
│   ├── 2026-07-31-crash-postmortem.md
│   ├── four-step-method-execution.md
│   ├── main-model-switch.md
│   ├── v7-audit-findings.md
│   ├── v7.1-audit-findings.md
│   ├── v7.3-crash-analysis.md
│   ├── v7.3.2-provider-writeback-fix.md
│   ├── v7.3.3-status-field-agent-display.md
│   ├── v7.4.0-integrity-protection.md
│   ├── v7.6.0-identity-mismatch.md
│   ├── v7.8.0-stale-note-fix.md
│   ├── v7.9.0-detect-provider-leak.md
│   ├── v7.9.1-endpoint-base-url-fix.md
│   └── v7.10.0-comprehensive-fix.md
└── scripts/
    ├── auto_bootstrap.py
    ├── cleanup_feishu_status.py
    ├── cron_sync.sh
    ├── four-step-template.py
    ├── switch_next.py
    └── sync_credential_pool.py
```

## Version history

### v7.22.0 (2026-08-19)

- **Fixed Claude Code credential pool health check and status sync (open code / 硅基流动 / ARK flapping)**:
  - **P1**: Fixed `join_target` URL deduplication for base URLs containing `/v1/messages` mid-path.
  - **P2**: Extended `is_claude_model` / `CLAUDE_MODEL_KEYWORDS` to include `glm`, `zai-org`, `deepseek`.
  - **P3**: Introduced `is_anthropic_endpoint(base_url)` and `_auth_scheme(model, base_url)`.
  - **P4**: Added `UPSTREAM_USER_AGENT` and `User-Agent: OpenAI/Python` fallback.
  - **P5**: Added `PROBE_INTERVAL` throttling and `_probe_credential()` real-time verify.
  - **P6**: Fixed `_write_ui_state` retry and `forward()` TimeoutError handling.
  - **P7**: Fixed `_consecutive_failures` hysteresis and `_known_status` update order.
- **Integrated Claude Code credential pool scripts** into workdir for GitHub sync.
- **Bumped SKILL.md version** to `v7.22.0`.

### v7.21.0 (2026-08-17)

- **Fixed MiniMax health check false failure (root cause: cold start timeout)**:
  - **F-1**: Health check timeout configurable: default 15s (was hardcoded 8s), with 25s override for `MINIMAX`/`XIAOMI` providers via `_PROVIDER_TIMEOUT_OVERRIDE` + `_health_timeout()`. Also fixed `request_with_retry` default to reference `HEALTH_CHECK_TIMEOUT`.
  - **F-2**: Timeout/connection failure now `break` out of the endpoint loop instead of `continue` — reaching the host means the endpoint is likely correct; trying the next one (e.g. `/messages` on MiniMax) only produces misleading 404s.
  - **F-3**: Introduced `root_cause` to preserve the first real failure. 404/405 (endpoint-missing) no longer overwrite the true root cause. Final return prioritizes `root_cause or last_error`.
  - **F-4**: `endpoint_candidates()` now accepts `provider` parameter and only generates `/messages` for Anthropic-family providers (`anthropic`/`longcat` or URL containing `/messages`/`/anthropic`). OpenAI-family providers skip the doomed `/messages` probe entirely.
  - **F-5**: New `_health_request()` helper adds 2-attempt exponential-backoff retry for connection-level failures (`URLError`/`OSError`). HTTP status codes pass through to `tk()` for branch judgement — avoids conflating endpoint-missing 404s with network jitter.
  - **F-6**: Returns the actual failed endpoint via `last_endpoint` instead of `candidates[-1]`. 404/405 do not update `last_endpoint` (those endpoints aren't the real failure).
  - **F-7**: Every failure branch now explicitly sets `last_status = S_U` instead of relying on initial value — eliminates implicit dependency.
- **Reordered constants**: Moved `HEALTH_CHECK_TIMEOUT`/`_PROVIDER_TIMEOUT_OVERRIDE` declarations before `request_with_retry()` to fix `NameError` at import time.
- **Verified**: MiniMax 5 consecutive `tk()` calls now all return `(True, S_A, None, ...)` after the fix (previously ~40% failure rate due to cold-start timeouts).
- **Bumped script versions** to `v7.21.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, `auto_bootstrap.py`, and `SKILL.md`.

### v7.20.0 (2026-08-16)

- **Fixed `mark_runtime_failure` multi-match bug**: When multiple Feishu records share the same `(model, base_url)` (e.g. two ARK records with `doubao-seed-2-1-turbo`), the function previously raised `ValueError` because `len(matches) != 1`. The runtime failure was never written to Feishu, so records stayed "✅ 正常" and kept being selected on next sync, causing infinite 429 loops. Now all matching records are marked as failed (they share the same model endpoint and would all fail the same way).
- **Bumped all script version strings** to `v7.20.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.19.0 (2026-08-15) Claude Code 凭证池按优先级自动切换
- **实现 active 档（收窄最高有效档）语义**：`refresh()` 健康检查后 `_group_by_tier` 按 0-9 优先级分档 → `_select_active_tier` 只把最高有效档（含 ≥1 健康未耗尽凭证的最低档）的凭证进 `_credentials`，低优先级档不再混入活动池。
- **档内优先切换、档内耗尽才推进**：`next_after`/`rotate` 先在 active 档内按优先级找下一个健康凭证，档内全耗尽才 `_advance_tier()` 推进下一档；`current()` 档内跳过 `_bad`。
- **`_bad` 跨 refresh 保留并自动恢复**：当前档位内仍探活失败的 key 保留标记，重新探活通过（进入 healthy）的 key 自动解除 `_bad`，恢复为可选用，避免恢复凭证永久滞留被排除。
- **重试上限跨档**：`do_POST` 重试循环上限改为全部健康凭证总数，跨档推进时不提前退出。
- 涉及脚本：`claude_credential_proxy.py`。

### v7.18.0 (2026-08-15)  Claude Code 凭证池恢复与按优先级切换完善
- **凭证池不再永久卡死在"限流/额度耗尽"**：`refresh()` 只剔除硬性失效（无效/停用），不再永久排除"额度耗尽/限流"；`_sync_dashboard` 探活通过即把"⛔ 限流/⚠️ 额度耗尽"恢复写回"✅ 正常"。
- **区分可恢复与永久状态**：`mark_exhausted` 改为 `mark_failure(credential, status, reason)`——429 标"⛔ 限流"，402/403 标"⚠️ 额度耗尽"，均记入 `_bad`。
- **按优先级切换只到健康凭证**：`next_after` 与 `rotate` 跳过 `_bad` 中的已耗尽 key，避免下一请求再打中失效 key；自动/手动切换均写回"🔄 使用中"。
- **新增 `_bad` 集合**：运行时累积失败 key，周期 `refresh` 探活前清空，实现恢复后重新可用。
- 涉及脚本：`claude_credential_proxy.py`。

### v7.17.0 (2026-08-14)
- **Fixed MiniMax/DeepSeek health check false failures (S_U)**:
  1. Removed `/anthropic` gate in `endpoint_candidates()`; now `/v1/chat/completions` is added for *any* base URL without a version segment.
  2. Changed `_strip_endpoint_suffix()` to only strip method suffixes (`/chat/completions`, `/messages`), preserving version prefixes like `/v1`, `/v3`.
  3. Fixed `tk()`: 400-499 errors (400/406/422/...) now `continue` to try other candidate endpoints instead of returning `S_U` immediately.
  4. Enhanced `try_url_variants()`: now also generates a `/v1`-prefixed variant when the stored base has no explicit version segment.
  5. Added `api.minimax.chat` → `MINIMAX` mapping in `detect_provider()`, covering both MiniMax official domains.
- **Bumped all script version strings** to `v7.17.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, `auto_bootstrap.py`, and `SKILL.md`.

### v7.15.0 (2026-08-13)
- Reconciled README and operational documentation with the actual priority-tier and provider behavior.

### v7.14.2 (2026-08-10)

- **Fixed `switch_next.py` false-failure when current credential is already optimal**: The switch script previously reported a failure when the current credential was already the best available. It now detects this case and exits cleanly instead of raising a spurious failure.
- **Bumped version strings** to `v7.14.2` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.14.1 (2026-08-10)

- **Fixed `register_cron_job()` script path sandbox violation**: `setup.py` previously registered the cron job with an absolute path pointing into the skills directory (`SCRIPT_DIR/sync_credential_pool.py`), which the cron sandbox rejected with "Blocked: script path resolves outside the scripts directory". Now copies the script to `~/AppData/Local/hermes/scripts/credential_pool_sync.py` via `shutil.copy2` and uses the relative filename. Also sets `workdir` to the scripts directory. Existing jobs are migrated on re-run.
- **Added `get_cron_scripts_dir()` helper**: Returns the canonical `~/AppData/Local/hermes/scripts` path for cron-compatible script placement.

### v7.14.0 (2026-08-07)

- **Fixed `custom:custom` credential pool key**: `_hermes_pool_key()` previously produced `custom:custom` for the `custom` provider (the `custom:` prefix was prepended to a provider that was already "custom"). Now it returns bare `custom` directly when `pk == HERMES_CUSTOM_PROVIDER`. Prior to this, Hermes could show a `custom:custom` key in `auth.json`.
- **Added version header to `auto_bootstrap.py`**: Docstring now carries the `v7.14.1` version, matching the other scripts.
- **Bumped version strings** to `v7.14.1` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.13.0 (2026-08-06)

- **Added priority tier-based reading**: Records in the Feishu table must have a priority integer 0-9 (smallest = highest priority). Synchronization now reads only one tier at a time, starting from tier 0. If all records in a tier are invalid, advances to the next tier (1, 2, ... 9). When a lower tier has valid credentials, higher tier credentials are never loaded locally.
- **Added `priority()` function**: Unified 0-9 normalization across all scripts. Out-of-range or non-integer values are clamped to tier 9 (lowest) with a stderr warning. Never drops records or promotes priority.
- **Added `group_by_priority()` function**: Groups normalized records by priority tier (0-9), returning `{tier: [records]}`.
- **Added `collect_active_tier()` function**: Shared tier-by-tier health check. Starting from tier 0, health-checks each tier's records and returns the first tier with ≥1 valid credential. Tiers above the active tier are never health-checked or read. Handles `skip_health_rotate` mode (all records valid, active tier = lowest non-empty tier).
- **Refactored `_sync_unlocked()`**: Replaced full-scan loop with `collect_active_tier()` call. auth.json credential_pool, fallback_providers, and main model switch candidates are now limited to the active tier only.
- **Updated `sync_fallback_providers()`**: Now receives only active-tier raw records instead of all records.
- **Updated `switch_next.py`**: Imports `priority`, `group_by_priority`, `collect_active_tier` from `sync_credential_pool`. `first_healthy()` narrows candidates to active tier before selection.
- **Updated `auto_bootstrap.py`**: Same imports. `try_switch()` narrows to active tier. `main()` triggers a full sync (without `--skip-health-rotate`) to advance tiers when active tier fails.

### v7.12.0 (2026-08-03)

- **Added Responses API support to health checks**: endpoint_candidates() now probes /responses before /chat/completions and /messages. tk() sends Responses API request body for /responses endpoints. Fixes glm-4-7-251222 and other ARK/OpenAI Responses API models being incorrectly classified as "不可用".
- **Updated _strip_endpoint_suffix()**: Added /v1/responses, /v3/responses, /responses suffix stripping.
- **Updated endpoint_base_url()**: Added /responses suffix recognition.
- **Fixed HTTP 404 handling in tk()**: Changed from immediate return to continue, so a 404 on one protocol does not prevent trying other protocols.

### v7.11.1 (2026-08-01)

- **Added GitHub Sync section**: Documented the full sync procedure, git corruption recovery, and terminal failure workaround. The installed skill is the canonical source — the GitHub repo is a mirror, not the authoritative copy.
- **Updated Files tree**: Added missing `v7.10.0-comprehensive-fix.md` and `2026-07-31-crash-postmortem.md` to the reference files listing.
- **Added pitfall: Remote tag vs file content mismatch**: The remote `v7.11.0` tag may reference outdated files — always verify actual content before assuming the remote is up to date.

### v7.11.0 (2026-07-31)

- **Fixed switch_next.py default sync**: Default `run_sync(full=False)` → `run_sync(full=True)`, ensuring `auth.json` is always updated before each switch. Previously, normal switches only read Feishu to memory without writing local `auth.json`.
- **Fixed switch_next.py --skip-sync help text**: Corrected misleading "直接读取 auth.json" to accurately describe the actual behavior (reads Feishu directly, no auth.json update).
- **Fixed auto_bootstrap.py old credential status cleanup**: Added `status_remove()` on the old credential after switching. Previously, `auto_bootstrap.py` only updated the new credential's status, leaving the old credential's Agent marker stale.
- **Updated `try_switch()` return value**: Now returns `(record, path, current_identity)` triple instead of `(record, path)` dual, enabling the caller to identify and clean up the previous credential.

### v7.10.0 (2026-07-30)

- **Fix BUG-01: exhausted status never recovers**: Add retry mechanism for exhausted credentials — before skipping, retry once and if successful, update health status and add to pool
- **Fix BUG-03: URL suffix missing**: Expand URL normalization to `/api/coding`→`/api/coding/v3` and `/api`→`/api/v3`
- **Fix BUG-04/05: case inconsistencies**: Provider normalization to lowercase, force to "custom" for Hermes config; all model names lowercased
- **Fix BUG-08/14: missing access_token**: Add `_backfill_token_fields()` helper to ensure every credential has both token fields
- **Fix BUG-12: model/endpoint mismatch**: Healthy main model selection — automatically switch to healthy model when current is unhealthy
- **Fix BUG-17: fallback skips same base_url**: Dedup fallback by base_url, prefer distinct endpoints to avoid Hermes official bug
- **Fix BUG-02: overwrites config**: `_sync_managed` flag — only update fallback_providers if not marked as managed
- **Critical loop fixes**: Fixed tk() tuple unpacking bug, ensure recovered credentials are added to pool

### v7.9.1 (2026-07-30)

- **Fixed Feishu status not showing "🔄 周公瑾使用中"**: `endpoint_base_url()` stripped the `/v3` version suffix from probed endpoints (e.g. `.../api/coding/v3/chat/completions` → `.../api/coding`), preventing URL healing. The identity comparison between Feishu record (without `/v3`) and config (with `/v3`) always failed, so no credential was ever marked as in-use.
- **Fix details**: `endpoint_base_url()` now only strips method suffixes (`/chat/completions`, `/messages`), preserving the version prefix. Also reordered `try_url_variants()` and `endpoint_candidates()` to prefer `/v3` over `/v1`.

### v7.9.0 (2026-07-30)

- **Fixed BUG-09: Pool key format**: `detect_provider()` leaked "ARK" into auth.json credential_pool key (producing `ark` instead of `custom:ark`). Added `_hermes_pool_key()` helper and `STANDARD_PROVIDERS` frozenset. Non-standard providers now get `custom:` prefix.
- **Fixed BUG-10: Provider name in config.yaml**: `detect_provider()` leaked "ARK" into fallback_providers entry provider and config.yaml model provider. Both now hardcode `HERMES_CUSTOM_PROVIDER = "custom"` for URL-backed records.
- **Fixed BUG-11: custom_providers cleared**: `cleanup_custom_providers()` removed entries whose Feishu base_urls lacked `/v3` suffix. Added `_strip_version_suffix()` for fuzzy matching of `/v1`/`/v3`-normalized URLs.
- **Fixed BUG-13: Exhausted credential re-added**: Rate-limited credentials with `额度已用完` or `quota exhausted` in error text are now skipped from the credential pool.
- **Bugs BUG-02/03/04/05 also resolved**: These were caused by the same `detect_provider()` leakage into pool key and provider fields.
- **Note**: BUG-12 (model/endpoint mismatch) is a Feishu data issue, not a code bug. The sync script correctly uses the model/endpoint from Feishu; the user must ensure compatible combinations in the table.
- **Added reference**: `references/v7.9.0-detect-provider-leak.md` with full bug inventory (BUG-09~13) and fix details.

### v7.8.0 (2026-07-30)

- **Fixed stale 备注 notes surviving health check cycles**: Health check success paths in 3 scripts (sync, cleanup, switch_next) now write "验证通过" instead of preserving old error notes. Failure paths now prefer the current error over stale notes, with status-specific fallbacks ("额度已用完" for S_R, "Key 无效" for S_I, "验证失败" for S_U).
- **Removed dead code**: `_usage_names`, `usage_remove`, `usage_add` functions deleted from sync_credential_pool.py — Agent markers have been in the status field since v7.3.3.
- **Fixed switch_next.py old credential note**: Old credential no longer unconditionally gets "额度已用完"; note is derived from its actual health status.
- **Added unified note policy**: Documented the 4-state note policy (PASS→验证通过, S_R→额度已用完, S_I→Key无效, S_U→验证失败) as the cross-script standard.
- **Known gap**: `auto_bootstrap.py` failed candidates still don't write back to Feishu — to be addressed in a future release.
- **Added reference**: `references/v7.8.0-stale-note-fix.md` with full bug inventory (10 items across 4 scripts) and fix details.

### v7.7.0 (2026-07-30)

- **Unified identity() across all scripts**: All scripts now use `(model, api_key, base_url)` triple-key format for credential identity. Previously `switch_next.py` used `(model, api_key)` dual-key while `sync_credential_pool.py` and `cleanup_feishu_status.py` used `(api_key, base_url, model)`.
- **Fixed switch_next.py endpoint discovery**: `tk()` now imported from `sync_credential_pool.py`, giving `switch_next.py` access to the full URL healing logic including /v3 endpoint support. Previously switch_next had its own stunted endpoint discovery that missed /v3 paths.
- **Fixed `normalise_base_url()` sharing**: `normalise_base_url()` is now a shared public function imported by both `switch_next.py` and `cleanup_feishu_status.py`, eliminating duplicate normalization logic.

### v7.6.0 (2026-07-30)

- **Fixed identity() dual-key mismatch**: `switch_next.py` `identity()` changed from `(api_key, base_url, model)` to `(model, api_key)` — the user's preferred "模型 + API key 双重确认唯一性" design principle. This fixes the bug where `switch_next.py` always claimed "切换成功" but never actually switched to a different credential.
- **Fixed `get_current_model_config()` model name extraction**: The function now maps `config.yaml`'s `default` key to `current["model"]` so that `identity()` can read the model name correctly. Previously, the model name was always `None` because `identity()` reads `record.get("model")` but the config uses `default`.
- **Added `ordered_candidates()` None-safety**: Explicitly handles `current_identity is None` to prevent false matches.
- **Added pitfall documentation**: Documented the `default` vs `model` key mismatch as a known pitfall, and the cross-script identity inconsistency (switch_next uses `(model, api_key)` but sync_credential_pool and cleanup_feishu_status still use `(api_key, base_url, model)`).
- **Added reference**: `references/v7.6.0-identity-mismatch.md` with full root cause analysis and verification steps.

### v7.4.0 (2026-07-30)

- **Added credential pool integrity protection**: New `MIN_POOL_RETENTION_RATIO = 0.5` constant guards against catastrophic pool shrinkage. When new candidate pool < 50% of existing AND > 50% of health checks returned `S_U` (unavailable), the existing pool is preserved with a warning instead of being replaced. Fixes the Trae-reported crash scenario where network-level 429 bursts caused all credentials to be deleted.
- **Extracted `normalise_base_url()` shared function**: ARK `api/plan` → `api/plan/v1` URL correction is now a reusable public function, eliminating the gap between `sync_credential_pool.py` and `switch_next.py` independent record normalizers. Both scripts now use the same normalization rule.
- **Fixed misleading comment in `sync_fallback_providers()`**: Comment said "跳过 429 的模型" but the code actually skips `S_I` (invalid), not `S_R` (rate limited). Updated to accurately describe the behavior.
- **Fixed edge case in pool protection**: When protection triggers and `fe` is empty, the final status message now says "保留现有 credential_pool" instead of misleading "已清空 credential_pool".

### v7.3.3 (2026-07-30)

- **Moved Agent usage marker from 备注 to 状态 field**: `_sync_unlocked()` now uses `status_add()`/`status_remove()` directly on the status field instead of writing "✅ 正常" for all valid credentials. Active credential shows "🔄 周公瑾使用中", others show "✅ 正常".
- **Removed `clear_current()` function**: The global clear-before-write pattern was replaced with per-record status maintenance. The function was dead code after the v7.3.3 refactor and is now deleted.
- **Fixed `health_status` variable name collision** in `cleanup_feishu_status.py`: The probe result variable was renamed from `health_status` to `probe_status` to avoid shadowing the imported `health_status()` function, which was causing a `NameError` when `S_U` was referenced.
- **Cleaned up `switch_next.py`**: Removed `_usage_names`/`usage_add`/`usage_remove` functions (no longer needed since Agent markers are in the status field, not 备注). Post-switch Feishu update now uses `status_remove()` on old credential and `status_add()` on new credential.
- **Added reference**: `references/v7.3.3-status-field-agent-display.md` with full fix details and cross-script consistency rules.

### v7.3.2 (2026-07-30)

- **Fixed 'custom' provider write-back inconsistency**: All 4 scripts now consistently handle the "custom" Provider value across read-normalize-write paths.
- **`sync_credential_pool.py`**: `url_updates` condition changed from `not original_provider` to `not original_provider or original_provider.lower() == "custom"` — ensures detected Provider is written back to Feishu even when the field says "custom" instead of empty.
- **`switch_next.py`**: `_normalise_record()` now checks `not provider or provider.lower() == "custom"` instead of just `not provider`.
- **`switch_next.py`**: `update_runtime_main_model()` now uses `record.get("provider") or detect_provider() or "custom"` — prefers the normalized record field over re-detection.
- **`auto_bootstrap.py`**: `write_runtime_config()` same triple-fallback pattern as switch_next.
- **`cleanup_feishu_status.py`**: Added `S_U` import and `cleanup_feishu_status()` now infers Provider from Base URL and writes it back to Feishu alongside status and note.
- **Added reference**: `references/v7.3.2-provider-writeback-fix.md` with full fix details and cross-script consistency rules.

### v7.3.1 (2026-07-29)

- **Fixed URL healing identity mismatch**: Added `healed_urls` dict to propagate healed URLs from `_sync_unlocked()` to `sync_fallback_providers()`. When URL healing occurs (e.g., `api/plan` → `api/plan/v1`), the healed URL is now used for health_results lookup in the fallback chain, preventing invalid/rate-limited credentials from being added.
- **Fixed model name normalization**: `identity()` in `cleanup_feishu_status.py` now normalizes model to `str(model or "").strip().lower()` for case-insensitive comparison, preventing config.yaml vs Feishu identity mismatches.
- **Added shared rotation lock**: `switch_next.py` and `auto_bootstrap.py` now share a `.rotation.lock` file with `sync_credential_pool.py`. The lock covers the full read-current → select → health-check → write-config sequence, preventing multi-process conflicts. The `run_sync()` subprocess is kept outside the lock to avoid reentrant locking.
- **`switch_next.py`**: `rotate_once()` function wraps the rotation lock. `main()` calls `run_sync()` before the lock, then `rotate_once()` inside it.
- **`auto_bootstrap.py`**: `try_switch()` function wraps the rotation lock. `main()` calls `run_sync()` before the lock, then `try_switch()` inside it.

### v7.3.0 (2026-07-29)

- **Provider Reverse-Inference**: `detect_provider()` now overrides empty or "custom" Provider values from Feishu with standard provider names (ARK, OPENAI, ANTHROPIC, DEEPSEEK, MOONSHOT, DASHSCOPE, Z.AI) based on Base URL hostname matching.
- **Removed `custom:` prefix**: Credential pool keys in `auth.json` no longer use the `custom:` prefix. Known providers use their standard name; unknown providers fall back to bare `custom`.
- **`repair_feishu_providers()` function**: New function in `cleanup_feishu_status.py` that reads all Feishu records, infers Provider from Base URL, and updates Feishu when current field is empty or "custom". Invoked via `--repair-provider` flag.
- **`switch_next.py`**: Now uses `detect_provider()` instead of hardcoded "custom". Added provider inference in `_normalise_record()` so empty-Provider records are not silently dropped.
- **`auto_bootstrap.py`**: Now uses `detect_provider()` instead of hardcoded "custom".
- **`sync_fallback_providers()`**: Fallback entry provider uses `rec.get("provider", detect_provider() or "custom")` instead of hardcoded "custom".
- **`_normalise_record()`**: Provider inference logic now prefers `detect_provider()` over "custom" fallback: `if inferred and (not provider or provider.lower() == "custom"): provider = inferred`

### v7.2.0 (2026-07-29)

- **Fix Feishu UI status management chaos**: Separated health status from usage status
- **Health status field**: Only shows "✅ 正常", "⛔ 限流", "❌ 无效", "🔄 检查中" 
- **Usage status field**: Agent ownership moved to 备注 field, no longer overwrites health status
- **Fixed core bug**: Health checks now correctly show failed status instead of "✅ 正常"
- **Auto-switch 429 fallback**: When main model is 429, automatically switches to first healthy non-vision model
- **Updated sync()**: Uses health_status() and usage_add/remove() functions for clean separation
- **Updated switch_next.py**: Now properly manages usage status in 备注 field
- **Updated cleanup_feishu_status.py**: Reconciles health and usage status separately
- **Added _cleanup_429_fallback_config()**: Removes 429 entries from config.yaml fallback_providers
- **Improved sync_fallback_providers()**: Filters 429 records and excludes current main model
- **Fixed subprocess encoding**: Added encoding='utf-8', errors='replace' to all subprocess calls

- Fixed `subprocess.run` missing `encoding='utf-8', errors='replace'` in all three scripts (sync, switch_next, auto_bootstrap). Windows default encoding caused `UnicodeDecodeError` on Chinese output.
- Fixed `sync_credential_pool.py` docstring version from v2.0 to v7.0.0.
- Rewrote `cron_sync.sh` to use the known skill directory path instead of a fragile cross-drive `find` search.
- `auto_bootstrap.py` now writes Feishu status (marks new credential as in-use) after a successful switch, matching `switch_next.py` behavior.
- Increased `auto_bootstrap.py` sync timeout from 20s to 60s to avoid premature timeout on slow networks.

### v7.0.0 (2026-07-28)

- Deferred all Feishu mutations until after the atomic `auth.json` commit.
- Added corrupt/missing `auth.json` recovery while preserving parseable top-level data.
- Fixed cleanup to mark only the exact active credential as in-use.
- Removed hardcoded Feishu application credentials and added Hermes config loading.
- Added one full-sync retry when candidate selection fails.
- Added best-effort full reconciliation after every successful switch.
- Made the installed skill self-contained and updated Gateway startup to use it.

### v6.0.0

- Added cross-provider rotation, URL normalization, model limits, Feishu status updates, cleanup tooling, and `--skip-sync`.

### v5.x and earlier

- Added atomic writes, Windows file locking, unified health checks, retries, fallback cleanup, and bootstrap integration.
---
name: credential-pool-sync
description: "Synchronize, health-check, rotate, and reconcile credentials stored in Feishu Bitable for Hermes and Claude Code. Use when enabling the Claude Code credential pool, switching exhausted credentials, or managing Feishu credential-pool health."
version: 7.22.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [credential-pool, feishu, sync, devops]
    related_skills: [feishu-bitable, hermes-agent]
---

# Credential Pool Sync v7.21.0

This skill synchronizes API credentials from Feishu into Hermes `auth.json`, rotates the active model in `config.yaml`, and reconciles Feishu health/in-use status. The installed skill directory is canonical at:

`%LOCALAPPDATA%\hermes\skills\devops\credential-pool-sync`

The external `credential-pool-sync` repository is the development source only. Runtime scripts live in this skill's own `scripts/` directory, so the installed skill is standalone.

## Claude Code 独立凭证池

当用户说“启用 Claude 凭证池”时，执行：

```powershell
python scripts/enable_claude_credential_pool.py
```

脚本复用 Hermes 已配置的飞书机器人和多维表格文档，新建或复用独立的 `Claude Code 凭证池` 数据表。该表字段与主凭证池一致，但凭证、状态和轮换完全独立。它会更新 Claude Code 设置，并注册 Windows 登录后后台启动的本地代理。代理遇到额度不足、余额不足或 429 限流，会换用下一条 Claude 专用凭证并重试同一请求。

首次启用后，Hermes 与 OpenCode 四步法调用同一台机器上的 Claude Code CLI，无需重复配置。不要把 Claude 专用表的记录写回 Hermes `auth.json` 或主凭证池表。

Feishu application credentials and Bitable identifiers must not be embedded in source. Read `FEISHU_APP_ID`, `FEISHU_APP_SECRET`, `FEISHU_BITABLE_APP_TOKEN`, and `FEISHU_BITABLE_TABLE_ID` from the environment, or place them in the local Hermes `config.yaml` under `credential_pool_sync`.

## Four-Step Method Execution

This skill was optimized using the user's required four-step method:

1. **Step 1: Codex CLI Review** - Identified Feishu UI status management issues
2. **Step 2: Codex CLI Solution** - Created detailed 11-point fix plan (--ephemeral)
3. **Step 3: Codex CLI Implementation** - Executed fixes (-s danger-full-access)
4. **Step 4: MiMo Code Review** - Verified implementation and identified issues

The method requires strict separation: reviewers cannot be implementers, and each step must be completed before the next. See `references/four-step-method-execution.md` for detailed execution patterns.

## Setup / Deployment

After installing the skill on a new machine, run **once** to complete full deployment:

```bash
cd %LOCALAPPDATA%\hermes\skills\devops\credential-pool-sync
python scripts/setup.py
```

This guided setup automatically does:
1. Checks Feishu robot credentials and access to the user-provided Bitable.
2. Reuses a `凭证池` table or creates one in an empty Bitable, then adds every required field that is missing.
3. Runs a full first sync and requires at least one health-checked credential.
4. Only after that gate succeeds, registers a cron job every 2 hours (`0 */2 * * *`) and configures the Gateway startup hook.

If the table, required permissions, or a healthy credential is missing, setup exits without enabling either automation. Give the App Token with `--bitable-app-token app_xxx` (and, if needed, `--bitable-table-id tbl_xxx`), or configure the corresponding environment variables/local Hermes configuration. The user must grant the Feishu bot access to the supplied Bitable; this skill never creates a top-level document without a user-controlled folder.

Additional options:
- `--skip-cron` - Skip registering the cron job
- `--skip-bootstrap` - Skip configuring the gateway startup hook
- `--sync-only` - Only run the first sync (equivalent to both above)

The script is idempotent—re-running it is safe and won't duplicate cron jobs or startup hooks.

## Trigger matrix

| User intent / event | Command | Sync mode | Result |
|---|---|---|---|
| Synchronize or refresh all credentials | `python scripts/sync_credential_pool.py` | Full | Reads Feishu, health-checks every key, atomically writes `auth.json`, then reconciles Feishu |
| Refresh local pool quickly | `python scripts/sync_credential_pool.py --skip-health-rotate` | Lightweight | Reads Feishu and writes `auth.json`; skips health checks, Feishu writes, and fallback cleanup |
| Switch to the next credential | `python scripts/switch_next.py` | Full sync + health-check | Reads Feishu, writes `auth.json` via sync_credential_pool.py, then health-checks and rotates to next healthy credential; post-switch updates Feishu status for both old and new credential |
| Switch using the existing local pool | `python scripts/switch_next.py --skip-sync` | No sync subprocess; reads Feishu directly | Reads Feishu records directly without updating `auth.json`; full sync retry on first pass failure |
| Gateway startup | `python scripts/auto_bootstrap.py` | Lightweight discovery plus per-candidate checks | Selects the first healthy non-current credential |
| Repair Feishu status | `python scripts/cleanup_feishu_status.py` | Full health scan | Matches active `(api_key, base_url, model)` from `config.yaml`; only that credential is marked in-use |
| Repair Feishu Provider field | `python scripts/cleanup_feishu_status.py --repair-provider` | Read-only (no health check) | Infers standard Provider (ARK, OPENAI, etc.) from Base URL; updates Feishu when current is empty or "custom" |

## Full versus lightweight sync

A full sync reads Feishu, health-checks all records, builds the complete local credential pool, atomically commits `auth.json`, and only afterward updates Feishu statuses. This ordering prevents a failed health check or local write from corrupting Feishu's derived state.

A lightweight sync (`--skip-health-rotate`) reads Feishu and rebuilds `auth.json` without health checks, Feishu status writes, or fallback cleanup. It is intended for fast discovery, not authoritative reconciliation.

## Rotation behavior

`switch_next.py` identifies the current credential by the `(model, api_key, base_url)` triple-key (model + API key + base URL, all normalized). This is the user's preferred uniqueness rule — see "Pitfall: config.yaml 'default' key vs 'model' key identity mismatch" below for why this matters. Candidates are ordered by Feishu return order (not priority), and each candidate is health-checked before switching. `config.yaml` is written atomically with a UUID temporary file, flush, `fsync`, `os.replace`, and a Windows `msvcrt.locking` lock.

If the first pass finds no healthy candidate, the script runs one full sync and retries selection exactly once. This applies even with `--skip-sync`; that flag skips only the initial sync subprocess.

**Note** (v7.11.0): The default `switch_next.py` behavior now runs a full sync (`run_sync(full=True)`) before each switch, ensuring `auth.json` is always up-to-date. The post-switch Feishu status update cleanup was also added for `auto_bootstrap.py` — it now removes the old credential's Agent marker after switching.

## Pitfall: URL healing causes identity mismatch in fallback chain

When `tk()` discovers a corrected URL (e.g., `api/plan` → `api/plan/v1`), the `_sync_unlocked()` function stores the health result with the **healed** URL. However, `sync_fallback_providers()` re-normalizes the original Feishu records (which still have the **old** URL), so the `health_results` lookup misses. The result: invalid/rate-limited credentials can be added to the fallback chain.

**Fix** (v7.3.1): A `healed_urls` dict is passed from `_sync_unlocked()` to `sync_fallback_providers()`. Before each health_results lookup, if the original identity matches a healed entry, the healed URL is used. This ensures the lookup key matches what was stored.

## Pitfall: Model name case sensitivity in identity matching

The `identity()` function in `cleanup_feishu_status.py` returns `(api_key, base_url, model)` without normalizing the model name. If `config.yaml` has `DeepSeek-V4` but Feishu has `deepseek-v4`, the identity comparison fails, causing incorrect usage-status reconciliation.

**Fix** (v7.3.1): `identity()` now normalizes model: `str(model or "").strip().lower()`.

## Pitfall: 'custom' provider write-back inconsistency across scripts

When `_normalise_record()` correctly infers a standard Provider (e.g., "ARK") from a Base URL, but the Feishu table still shows "custom", the root cause is usually a condition in **another script** that only checks for empty provider, not "custom".

**Symptoms**: Feishu UI shows "Provider: custom" while the local `__RECORDS__` output already shows "ARK".

**Root cause**: The `_normalise_record()` function (in `sync_credential_pool.py`) correctly handles "custom" → "ARK" inference for local processing, but the Feishu write-back condition in `_sync_unlocked()` (`url_updates`) or switch scripts only checks `not original_provider` — missing "custom" entirely.

**Scripts affected and their fix pattern**:

| Script | Function | Wrong condition | Correct condition |
|---|---|---|---|
| `sync_credential_pool.py` | `url_updates` | `if not original_provider and detected_provider:` | `if detected_provider and (not original_provider or original_provider.lower() == "custom"):` |
| `switch_next.py` | `_normalise_record()` | `if not provider:` | `if not provider or provider.lower() == "custom":` |
| `switch_next.py` | `update_runtime_main_model()` | `"provider": detect_provider(...) or "custom"` | `"provider": HERMES_CUSTOM_PROVIDER` (hardcoded, v7.9.0+) |
| `auto_bootstrap.py` | `write_runtime_config()` | `"provider": detect_provider(...) or "custom"` | `"provider": "custom"` (hardcoded, already correct) |
| `cleanup_feishu_status.py` | `cleanup_feishu_status()` | Provider never updated | Infer Provider, pass to `us()` alongside status |

**Fix**: Every script that reads from Feishu and writes back must check for both empty and "custom" before inferring Provider. The `detect_provider()` fallback chain should be: `record.get("provider") or detect_provider(base_url) or "custom"`.

**Prevention**: When adding a new script that reads Feishu records and writes to config.yaml or back to Feishu, copy the Provider inference pattern from `sync_credential_pool.py._normalise_record()` — do not re-invent the condition.

## Pitfall: 'Wrong model displayed' ≠ model switch needed

When a user reports that Hermes shows the wrong model (e.g. a 429'd model name in the session banner), **always read `config.yaml` first before attempting any credential switch or health check**. The fallback chain may have already rotated the active model in `config.yaml`. The display discrepancy is often a session-cache or Hermes display-layer issue, not a config problem. Running `switch_next.py` or health checks prematurely wastes time and may rotate to a worse credential. Steps:

1. Read `config.yaml` → check `model.default` and `model.api_key`.
2. If the model matches what the user expects, the issue is display-layer, not credential-pool.
3. Only if `config.yaml` still points to the 429'd model should you proceed with `switch_next.py` or a full sync.

## Pitfall: config.yaml 'default' key vs 'model' key identity mismatch (switch_next)

**Problem**: `get_current_model_config()` in `switch_next.py` returns the model dict from `config.yaml`, which uses `default` as the key for the model name (e.g. `{default: "DeepSeek-V4-Flash", provider: "ARK", ...}`). But `identity()` reads `record.get("model")` — so when the current config is passed to `identity()`, the model name is always `None` because the config has `default` not `model`.

**Root cause chain**: `get_current_model_config()` returns `{default: "DeepSeek-V4-Flash"}` → `identity()` reads `record.get("model")` → returns `None` → current credential is not found in Feishu records → `ordered_candidates()` starts from the first record → first healthy candidate is always selected → "switch succeeds" but never changes.

**Fix** (v7.6.0): `get_current_model_config()` now copies the model dict and maps `default` → `model`:
```python
current = model_config.copy()
current["model"] = str(model_config.get("default", "") or "").strip()
return current
```

**Design principle**: credential identity is `(model, api_key, base_url)` — model lowercased + API key stripped + base URL normalized via `normalise_base_url()`. This was explicitly chosen by the user because:
- Multiple Feishu records can share the same API key (different models under one account)
- `base_url` must be included to distinguish credentials on the same API key but different endpoints
- `normalise_base_url()` ensures stable comparison despite URL suffix variations

## Pitfall: Identity inconsistency across scripts (RESOLVED v7.7.0)

All scripts now use the unified `(model, api_key, base_url)` triple-key identity format:

| Script | Identity format | Status |
|--------|----------------|--------|
| `switch_next.py` | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `cleanup_feishu_status.py` | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `sync_credential_pool.py` (_sync_unlocked) | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |
| `sync_credential_pool.py` (sync_fallback_providers) | `(model, api_key, base_url)` | ✅ Fixed v7.7.0 |

**Unified identity() function** (defined in `sync_credential_pool.py`, imported by others):
```python
def identity(model, api_key, base_url):
    return (str(model or '').strip().lower(), str(api_key or '').strip(), normalise_base_url(base_url))
```

**Design principle**: credential identity is `(model, api_key, base_url)` — model lowercased + API key stripped + base URL normalized. This was explicitly chosen by the user because:
- Multiple Feishu records can share the same API key (different models under one account)
- `base_url` must be included to distinguish credentials on the same API key but different endpoints
- `normalise_base_url()` ensures stable comparison despite URL suffix variations

## Corrupt auth.json recovery

If `auth.json` is missing, invalid JSON, unreadable, incorrectly encoded, or has a non-object root, full/lightweight synchronization treats it as an empty pool and rebuilds it from Feishu. When the existing document is parseable, unrelated top-level keys such as `version`, `providers`, and other metadata are preserved. A completely unusable file is replaced atomically with a minimal valid structure.

## Feishu credentials

No Feishu application credentials are embedded in source. Configure both environment variables:

```powershell
$env:FEISHU_APP_ID = "..."
$env:FEISHU_APP_SECRET = "..."
```

Alternatively configure `secrets.feishu.app_id` and `secrets.feishu.app_secret` in `%LOCALAPPDATA%\hermes\config.yaml`. Top-level `feishu` and `channels.feishu` mappings are also recognized for compatibility. Missing credentials produce a clear error without logging secret values.

## Cleanup semantics

`cleanup_feishu_status.py` reads the active model from Hermes `config.yaml` and compares the normalized `(model, api_key, base_url)` identity. A healthy active credential receives this agent's in-use marker in the status field (via `status_add()`). Other healthy credentials are marked `✅ 正常`, or retain another agent's in-use marker via `status_remove()`. Unhealthy records lose only this agent's marker and receive the health result. It never marks every healthy credential as in-use.

## Provider Reverse-Inference (Feishu display only — do NOT leak into Hermes config)

`detect_provider(base_url)` infers a provider label from the Base URL hostname. This is for **Feishu display/Provider field repair only** — the inferred value must NEVER be used in Hermes configuration (auth.json pool keys, config.yaml model.provider, or fallback_providers entry provider).

| URL Hostname | Inferred Label |
|---|---|
| `ark.cn-beijing.volces.com` | ARK |
| `open.bigmodel.cn` | Z.AI |
| `api.openai.com` | OPENAI |
| `api.anthropic.com` | ANTHROPIC |
| `api.deepseek.com` | DEEPSEEK |
| `api.moonshot.cn` | MOONSHOT |
| `dashscope.aliyuncs.com` | DASHSCOPE |

### Usage rules (v7.9.0+)

| Context | Provider value | Reason |
|---|---|---|
| Feishu table Provider field | `detect_provider()` result (e.g. "ARK") | For display/identification only |
| auth.json credential_pool key | `custom:{lowercase_label}` (e.g. `custom:ark`) | Hermes pool key format requires `{provider}:{name}` |
| config.yaml model.provider | `"custom"` | ARK is not a built-in Hermes provider |
| fallback_providers entry provider | `"custom"` | Same reason — URL-backed records are custom providers |

### Pitfall: detect_provider() leaking into Hermes config (v7.9.0 Critical)

**Problem**: `detect_provider()` returns "ARK" from the URL. When this value is used as the pool key prefix (instead of `custom:ark`), auth.json becomes `{"ark": [...]}` instead of `{"custom:ark": [...]}`. Hermes credential pool lookup uses `{provider}:{name}` keys — when the key format is wrong, ALL credentials become invisible, causing "Unknown provider 'ark'" errors and complete system crash.

**One-line rule**: `detect_provider()` is for Feishu only. Hermes config always uses `custom` for non-standard providers.

**Three leak paths fixed in v7.9.0**:
1. **Pool key** (line 721): `_hermes_pool_key(p)` adds `custom:` prefix for non-standard providers
2. **Fallback entry** (line 482): `"provider": HERMES_CUSTOM_PROVIDER` — hardcoded to `"custom"`
3. **Config write** (switch_next.py, auto_bootstrap.py): `record_provider = "custom"` — no longer uses `detect_provider()`

To repair existing Feishu records with empty or stale Provider fields:
```bash
python scripts/cleanup_feishu_status.py --repair-provider
```

### Pitfall: `custom:custom` credential pool key (v7.14.0)

**Problem**: When a record's provider is already the bare `custom` value, `_hermes_pool_key()` prepended the `custom:` prefix again, producing a `custom:custom` key in `auth.json` instead of `custom`. Hermes pool lookups expect `{provider}:{name}` keys where the provider namespace is `custom` — a `custom:custom` key is either invisible to lookup or treated as a distinct bogus provider.

**Root cause**: `_hermes_pool_key()` added `custom:` for every provider not in `STANDARD_PROVIDERS`. Since `custom` itself is not in the standard set, a provider already normalized to `custom` got double-prefixed.

**Fix** (v7.14.0): `_hermes_pool_key()` returns `custom` directly when `pk == HERMES_CUSTOM_PROVIDER` before the standard-provider check. Non-standard providers other than `custom` still get the `custom:` prefix as before.

```python
def _hermes_pool_key(provider):
    pk = str(provider or "").strip().lower().replace(" ", "-")
    if pk == HERMES_CUSTOM_PROVIDER:
        return HERMES_CUSTOM_PROVIDER
    if pk not in STANDARD_PROVIDERS:
        pk = f"custom:{pk}"
    return pk
```

**Prevention**: When normalizing a provider to a pool key, treat the `custom` sentinel as a terminal value — never re-name it. Only non-standard providers that are *not* already `custom` should receive the namespace prefix.

## Feishu Status Field Rules

The Feishu table's **状态** (status) column is the canonical display of which Hermes Agent is using each credential. Agent usage is written to the status field, NOT to the **备注** (notes) field.

### Status field assignment rules

Every credential's status field is determined by the following unified logic across all three scripts (`sync_credential_pool.py`, `cleanup_feishu_status.py`, `switch_next.py`):

| Condition | Action | Example |
|---|---|---|
| Valid + identity matches current Agent | `status_add(current_status, agent_name)` | `🔄 周公瑾使用中` |
| Valid + identity does NOT match current Agent | `status_remove(current_status, agent_name)` | `✅ 正常` (no Agents) / `🔄 甘宁使用中` (other Agent) |
| Invalid / rate-limited | `health_status(False, probe_status, error)` | `❌ 无效` / `⛔ 限流` |

### Multi-Agent support

`status_add()` concatenates multiple Agents with `+`:

```python
def status_add(s, name):
    names = "+".join(dict.fromkeys(agents(s) + [name]))
    return f"🔄 {names}使用中"
```

Example: `🔄 周公瑾+甘宁使用中` — both Agents are using the same credential.

### What the 备注 field should contain

The 备注 field is reserved for human-readable notes (e.g. "额度已用完", "验证通过", "HTTP 429: rate limited"). It should NOT contain Agent usage markers. Do not call `usage_add()`/`usage_remove()` on the 备注 field — these functions are deprecated and removed from `switch_next.py`.

### Pitfall: Stale 备注 notes survive across health check cycles

**Problem**: When a credential fails a health check (e.g. returns 429 → "额度已用完"), the error is written to 备注. If the same credential later passes the health check (e.g. quota resets, endpoint corrected), the status field correctly shows "✅ 正常" but the old error note in 备注 is never cleared. The user sees "status: ✅ 正常, note: 额度已用完" — a contradictory and misleading state.

**Root cause**: Several scripts preserve the existing note via `f.get("备注", "")` or `current_note` on the success path, never updating it to "验证通过". On the failure path, some scripts prefer the old note over the current error (`current_note or error`), so the note never reflects the current health state.

**Affected locations and fix pattern** (unified note policy):

| Script | Success path | Failure path |
|---|---|---|
| `sync_credential_pool.py` `_sync_unlocked()` iv=True branch | `new_note = "验证通过"` | `new_note = e or status_specific_fallback` |
| `sync_credential_pool.py` `_sync_unlocked()` S_R branch | n/a | `new_note = e or "额度已用完"` |
| `sync_credential_pool.py` `_sync_unlocked()` else (S_I/S_U) branch | n/a | `new_note = e or ("Key 无效" if s==S_I else "验证失败")` |
| `sync_credential_pool.py` `_normalise_record()` reject path | n/a | Must write note, not `None` |
| `cleanup_feishu_status.py` `cleanup_feishu_status()` | `new_note = "验证通过"` | `new_note = error or status_specific_fallback` |
| `switch_next.py` `first_healthy()` failed candidates | n/a | `note=error or status_specific_fallback` (not bare `error`) |
| `switch_next.py` `main()` old credential | n/a | Derive note from old_status, not hardcoded "额度已用完" |
| `auto_bootstrap.py` `try_switch()` | Writes "验证通过" (correct) | **Missing**: failed candidates never update Feishu |

**Unified note policy** (must be consistent across all scripts):
- Health check PASS → `"验证通过"`
- S_R / 限流 → `error or "额度已用完"`
- S_I / 无效 → `error or "Key 无效"`
- S_U / 不可用 → `error or "验证失败"`

**Prevention rule**: Never preserve the existing 备注 value when writing a health-check result. The note must always reflect the *current* health check outcome, not historical state. Human-edited notes are not preserved — this is acceptable because the note's purpose is to show the latest automated health check result.

### Script-specific implementation

| Script | Function | How it sets status | Notes |
|---|---|---|---|
| `sync_credential_pool.py` | `_sync_unlocked()` | Per-record `status_add`/`status_remove`/`health_status` | `clear_current()` was removed in v7.3.3 — status is now maintained per-record |
| `cleanup_feishu_status.py` | `cleanup_feishu_status()` | `status_add`/`status_remove`/`health_status` | Uses `probe_status` (not `health_status`) to avoid fn name collision |
| `switch_next.py` | Post-switch update | `status_remove` on old, `status_add` on new | `usage_add`/`usage_remove` functions removed in v7.3.3 |
| `auto_bootstrap.py` | `try_switch()` → success | `status_remove` on old, `status_add` on new | Fixed v7.11.0: now also cleans up old credential's Agent marker via `status_remove` |

## References

- `references/2026-07-31-crash-postmortem.md` — 2026-07-31 凭证池崩溃事后分析（B1-B8 根因、修复状态、崩溃时间线、经验教训）
- `references/four-step-method-execution.md` — 四步法执行模式文档
- `references/main-model-switch.md` — 主模型切换流程
- `references/v7-audit-findings.md` — 2026-07-28 v7.0.0 全面审查发现的问题清单、修复详情和触发矩阵
- `references/v7.1-audit-findings.md` — 2026-07-29 v7.1.0 审查：subprocess编码修复、cron_sync.sh路径修正、auto_bootstrap飞书回写补全
- `references/v7.3-crash-analysis.md` — 2026-07-29 429 级联崩溃分析
- `references/v7.3.2-provider-writeback-fix.md` — 2026-07-30 v7.3.2 Provider 字段回写不一致修复
- `references/v7.3.3-status-field-agent-display.md` — 2026-07-30 v7.3.3 状态栏 Agent 标记显示修复
- `references/v7.4.0-integrity-protection.md` — 2026-07-30 v7.4.0 凭证池完整性保护
- `references/v7.6.0-identity-mismatch.md` — 2026-07-30 v7.6.0 identity 双重确认修复
- `references/v7.8.0-stale-note-fix.md` — 2026-07-30 v7.8.0 备注栏残留旧值修复
- `references/v7.9.0-detect-provider-leak.md` — 2026-07-30 v7.9.0 detect_provider 泄漏修复
- `references/v7.9.1-endpoint-base-url-fix.md` — 2026-07-30 v7.9.1 endpoint_base_url() 修复
- `references/v7.10.0-comprehensive-fix.md` — 2026-07-30 v7.10.0 综合修复（BUG-01~17）

## GitHub Synchronization

The remote repository is at `github.com/flamebird07/credential-pool-sync`. The installed skill is the canonical runtime — sync it to GitHub when version increments.

### Sync procedure

```bash
# 1. Clone fresh (avoids local repo corruption)
git clone https://github.com/flamebird07/credential-pool-sync.git /tmp/credential-pool-clone
cd /tmp/credential-pool-clone

# 2. Compare remote vs installed skill versions
# Check SKILL.md version and all script sizes
diff <(git ls-tree -r --name-only HEAD | sort) \
     <(ls %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync/**/*)

# 3. Overwrite with installed skill files
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\SKILL.md .
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\scripts\\*.py scripts/
cp %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\scripts\\*.sh scripts/
# Copy any new reference files
for f in %LOCALAPPDATA%\\hermes\\skills\\devops\\credential-pool-sync\\references\\*.md; do
  cp "$f" references/
done

# 4. Commit and push
git add -A
git commit -m "vX.Y.Z: <description>"
git push origin master
git tag vX.Y.Z
git push origin vX.Y.Z
```

### Pitfall: Local git repo corruption

**Symptoms**: `git status` reports `fatal: unable to read <hash>`, `.git/config` is missing, or `git remote -v` returns empty despite `remotes/origin/master` existing.

**Root cause**: The `.git/config` file can be deleted independently from the git objects directory. If the index references missing blob objects, all git operations fail.

**Fix**: Never try to repair a corrupted local repo. Clone fresh:
```bash
rm -rf credential-pool-sync
git clone https://github.com/flamebird07/credential-pool-sync.git
```
Then overwrite with the installed skill files as described above.

### Pitfall: Remote tag says v7.11.0 but files are outdated

The remote `v7.11.0` tag may have been set on a commit that only updates the tag name, not the actual script files. Always verify file content (not just the tag) by comparing the installed skill's script sizes and version strings with the remote's `HEAD`.

### Terminal failure recovery for git operations

When the Hermes `terminal` tool is completely unresponsive (all commands time out), use `execute_code` with `subprocess.run` and direct paths to `git.exe`:

```python
import subprocess
git = r"C:\Program Files\Git\bin\git.exe"
result = subprocess.run([git, "status"], capture_output=True, text=True, timeout=10, cwd=repo_path)
print(result.stdout)
```

This bypasses the broken shell session entirely. The `execute_code` tool uses a Python sandbox with its own process management, independent of the terminal's shell state.

## Files

```text
credential-pool-sync/
├── SKILL.md
├── references/
│   ├── 2026-07-31-crash-postmortem.md
│   ├── four-step-method-execution.md
│   ├── main-model-switch.md
│   ├── v7-audit-findings.md
│   ├── v7.1-audit-findings.md
│   ├── v7.3-crash-analysis.md
│   ├── v7.3.2-provider-writeback-fix.md
│   ├── v7.3.3-status-field-agent-display.md
│   ├── v7.4.0-integrity-protection.md
│   ├── v7.6.0-identity-mismatch.md
│   ├── v7.8.0-stale-note-fix.md
│   ├── v7.9.0-detect-provider-leak.md
│   ├── v7.9.1-endpoint-base-url-fix.md
│   └── v7.10.0-comprehensive-fix.md
└── scripts/
    ├── auto_bootstrap.py
    ├── cleanup_feishu_status.py
    ├── cron_sync.sh
    ├── four-step-template.py
    ├── switch_next.py
    └── sync_credential_pool.py
```

## Version history

### v7.21.0 (2026-08-17)

- **Fixed MiniMax health check false failure (root cause: cold start timeout)**:
  - **F-1**: Health check timeout configurable: default 15s (was hardcoded 8s), with 25s override for `MINIMAX`/`XIAOMI` providers via `_PROVIDER_TIMEOUT_OVERRIDE` + `_health_timeout()`. Also fixed `request_with_retry` default to reference `HEALTH_CHECK_TIMEOUT`.
  - **F-2**: Timeout/connection failure now `break` out of the endpoint loop instead of `continue` — reaching the host means the endpoint is likely correct; trying the next one (e.g. `/messages` on MiniMax) only produces misleading 404s.
  - **F-3**: Introduced `root_cause` to preserve the first real failure. 404/405 (endpoint-missing) no longer overwrite the true root cause. Final return prioritizes `root_cause or last_error`.
  - **F-4**: `endpoint_candidates()` now accepts `provider` parameter and only generates `/messages` for Anthropic-family providers (`anthropic`/`longcat` or URL containing `/messages`/`/anthropic`). OpenAI-family providers skip the doomed `/messages` probe entirely.
  - **F-5**: New `_health_request()` helper adds 2-attempt exponential-backoff retry for connection-level failures (`URLError`/`OSError`). HTTP status codes pass through to `tk()` for branch judgement — avoids conflating endpoint-missing 404s with network jitter.
  - **F-6**: Returns the actual failed endpoint via `last_endpoint` instead of `candidates[-1]`. 404/405 do not update `last_endpoint` (those endpoints aren't the real failure).
  - **F-7**: Every failure branch now explicitly sets `last_status = S_U` instead of relying on initial value — eliminates implicit dependency.
- **Reordered constants**: Moved `HEALTH_CHECK_TIMEOUT`/`_PROVIDER_TIMEOUT_OVERRIDE` declarations before `request_with_retry()` to fix `NameError` at import time.
- **Verified**: MiniMax 5 consecutive `tk()` calls now all return `(True, S_A, None, ...)` after the fix (previously ~40% failure rate due to cold-start timeouts).
- **Bumped script versions** to `v7.21.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, `auto_bootstrap.py`, and `SKILL.md`.

### v7.20.0 (2026-08-16)

- **Fixed `mark_runtime_failure` multi-match bug**: When multiple Feishu records share the same `(model, base_url)` (e.g. two ARK records with `doubao-seed-2-1-turbo`), the function previously raised `ValueError` because `len(matches) != 1`. The runtime failure was never written to Feishu, so records stayed "✅ 正常" and kept being selected on next sync, causing infinite 429 loops. Now all matching records are marked as failed (they share the same model endpoint and would all fail the same way).
- **Bumped all script version strings** to `v7.20.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.19.0 (2026-08-15) Claude Code 凭证池按优先级自动切换
- **实现 active 档（收窄最高有效档）语义**：`refresh()` 健康检查后 `_group_by_tier` 按 0-9 优先级分档 → `_select_active_tier` 只把最高有效档（含 ≥1 健康未耗尽凭证的最低档）的凭证进 `_credentials`，低优先级档不再混入活动池。
- **档内优先切换、档内耗尽才推进**：`next_after`/`rotate` 先在 active 档内按优先级找下一个健康凭证，档内全耗尽才 `_advance_tier()` 推进下一档；`current()` 档内跳过 `_bad`。
- **`_bad` 跨 refresh 保留并自动恢复**：当前档位内仍探活失败的 key 保留标记，重新探活通过（进入 healthy）的 key 自动解除 `_bad`，恢复为可选用，避免恢复凭证永久滞留被排除。
- **重试上限跨档**：`do_POST` 重试循环上限改为全部健康凭证总数，跨档推进时不提前退出。
- 涉及脚本：`claude_credential_proxy.py`。

### v7.18.0 (2026-08-15)  Claude Code 凭证池恢复与按优先级切换完善
- **凭证池不再永久卡死在"限流/额度耗尽"**：`refresh()` 只剔除硬性失效（无效/停用），不再永久排除"额度耗尽/限流"；`_sync_dashboard` 探活通过即把"⛔ 限流/⚠️ 额度耗尽"恢复写回"✅ 正常"。
- **区分可恢复与永久状态**：`mark_exhausted` 改为 `mark_failure(credential, status, reason)`——429 标"⛔ 限流"，402/403 标"⚠️ 额度耗尽"，均记入 `_bad`。
- **按优先级切换只到健康凭证**：`next_after` 与 `rotate` 跳过 `_bad` 中的已耗尽 key，避免下一请求再打中失效 key；自动/手动切换均写回"🔄 使用中"。
- **新增 `_bad` 集合**：运行时累积失败 key，周期 `refresh` 探活前清空，实现恢复后重新可用。
- 涉及脚本：`claude_credential_proxy.py`。

### v7.17.0 (2026-08-14)
- **Fixed MiniMax/DeepSeek health check false failures (S_U)**:
  1. Removed `/anthropic` gate in `endpoint_candidates()`; now `/v1/chat/completions` is added for *any* base URL without a version segment.
  2. Changed `_strip_endpoint_suffix()` to only strip method suffixes (`/chat/completions`, `/messages`), preserving version prefixes like `/v1`, `/v3`.
  3. Fixed `tk()`: 400-499 errors (400/406/422/...) now `continue` to try other candidate endpoints instead of returning `S_U` immediately.
  4. Enhanced `try_url_variants()`: now also generates a `/v1`-prefixed variant when the stored base has no explicit version segment.
  5. Added `api.minimax.chat` → `MINIMAX` mapping in `detect_provider()`, covering both MiniMax official domains.
- **Bumped all script version strings** to `v7.17.0` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, `auto_bootstrap.py`, and `SKILL.md`.

### v7.15.0 (2026-08-13)
- Reconciled README and operational documentation with the actual priority-tier and provider behavior.

### v7.14.2 (2026-08-10)

- **Fixed `switch_next.py` false-failure when current credential is already optimal**: The switch script previously reported a failure when the current credential was already the best available. It now detects this case and exits cleanly instead of raising a spurious failure.
- **Bumped version strings** to `v7.14.2` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.14.1 (2026-08-10)

- **Fixed `register_cron_job()` script path sandbox violation**: `setup.py` previously registered the cron job with an absolute path pointing into the skills directory (`SCRIPT_DIR/sync_credential_pool.py`), which the cron sandbox rejected with "Blocked: script path resolves outside the scripts directory". Now copies the script to `~/AppData/Local/hermes/scripts/credential_pool_sync.py` via `shutil.copy2` and uses the relative filename. Also sets `workdir` to the scripts directory. Existing jobs are migrated on re-run.
- **Added `get_cron_scripts_dir()` helper**: Returns the canonical `~/AppData/Local/hermes/scripts` path for cron-compatible script placement.

### v7.14.0 (2026-08-07)

- **Fixed `custom:custom` credential pool key**: `_hermes_pool_key()` previously produced `custom:custom` for the `custom` provider (the `custom:` prefix was prepended to a provider that was already "custom"). Now it returns bare `custom` directly when `pk == HERMES_CUSTOM_PROVIDER`. Prior to this, Hermes could show a `custom:custom` key in `auth.json`.
- **Added version header to `auto_bootstrap.py`**: Docstring now carries the `v7.14.1` version, matching the other scripts.
- **Bumped version strings** to `v7.14.1` across `sync_credential_pool.py`, `switch_next.py`, `cleanup_feishu_status.py`, `setup.py`, and `SKILL.md`.

### v7.13.0 (2026-08-06)

- **Added priority tier-based reading**: Records in the Feishu table must have a priority integer 0-9 (smallest = highest priority). Synchronization now reads only one tier at a time, starting from tier 0. If all records in a tier are invalid, advances to the next tier (1, 2, ... 9). When a lower tier has valid credentials, higher tier credentials are never loaded locally.
- **Added `priority()` function**: Unified 0-9 normalization across all scripts. Out-of-range or non-integer values are clamped to tier 9 (lowest) with a stderr warning. Never drops records or promotes priority.
- **Added `group_by_priority()` function**: Groups normalized records by priority tier (0-9), returning `{tier: [records]}`.
- **Added `collect_active_tier()` function**: Shared tier-by-tier health check. Starting from tier 0, health-checks each tier's records and returns the first tier with ≥1 valid credential. Tiers above the active tier are never health-checked or read. Handles `skip_health_rotate` mode (all records valid, active tier = lowest non-empty tier).
- **Refactored `_sync_unlocked()`**: Replaced full-scan loop with `collect_active_tier()` call. auth.json credential_pool, fallback_providers, and main model switch candidates are now limited to the active tier only.
- **Updated `sync_fallback_providers()`**: Now receives only active-tier raw records instead of all records.
- **Updated `switch_next.py`**: Imports `priority`, `group_by_priority`, `collect_active_tier` from `sync_credential_pool`. `first_healthy()` narrows candidates to active tier before selection.
- **Updated `auto_bootstrap.py`**: Same imports. `try_switch()` narrows to active tier. `main()` triggers a full sync (without `--skip-health-rotate`) to advance tiers when active tier fails.

### v7.12.0 (2026-08-03)

- **Added Responses API support to health checks**: endpoint_candidates() now probes /responses before /chat/completions and /messages. tk() sends Responses API request body for /responses endpoints. Fixes glm-4-7-251222 and other ARK/OpenAI Responses API models being incorrectly classified as "不可用".
- **Updated _strip_endpoint_suffix()**: Added /v1/responses, /v3/responses, /responses suffix stripping.
- **Updated endpoint_base_url()**: Added /responses suffix recognition.
- **Fixed HTTP 404 handling in tk()**: Changed from immediate return to continue, so a 404 on one protocol does not prevent trying other protocols.

### v7.11.1 (2026-08-01)

- **Added GitHub Sync section**: Documented the full sync procedure, git corruption recovery, and terminal failure workaround. The installed skill is the canonical source — the GitHub repo is a mirror, not the authoritative copy.
- **Updated Files tree**: Added missing `v7.10.0-comprehensive-fix.md` and `2026-07-31-crash-postmortem.md` to the reference files listing.
- **Added pitfall: Remote tag vs file content mismatch**: The remote `v7.11.0` tag may reference outdated files — always verify actual content before assuming the remote is up to date.

### v7.11.0 (2026-07-31)

- **Fixed switch_next.py default sync**: Default `run_sync(full=False)` → `run_sync(full=True)`, ensuring `auth.json` is always updated before each switch. Previously, normal switches only read Feishu to memory without writing local `auth.json`.
- **Fixed switch_next.py --skip-sync help text**: Corrected misleading "直接读取 auth.json" to accurately describe the actual behavior (reads Feishu directly, no auth.json update).
- **Fixed auto_bootstrap.py old credential status cleanup**: Added `status_remove()` on the old credential after switching. Previously, `auto_bootstrap.py` only updated the new credential's status, leaving the old credential's Agent marker stale.
- **Updated `try_switch()` return value**: Now returns `(record, path, current_identity)` triple instead of `(record, path)` dual, enabling the caller to identify and clean up the previous credential.

### v7.10.0 (2026-07-30)

- **Fix BUG-01: exhausted status never recovers**: Add retry mechanism for exhausted credentials — before skipping, retry once and if successful, update health status and add to pool
- **Fix BUG-03: URL suffix missing**: Expand URL normalization to `/api/coding`→`/api/coding/v3` and `/api`→`/api/v3`
- **Fix BUG-04/05: case inconsistencies**: Provider normalization to lowercase, force to "custom" for Hermes config; all model names lowercased
- **Fix BUG-08/14: missing access_token**: Add `_backfill_token_fields()` helper to ensure every credential has both token fields
- **Fix BUG-12: model/endpoint mismatch**: Healthy main model selection — automatically switch to healthy model when current is unhealthy
- **Fix BUG-17: fallback skips same base_url**: Dedup fallback by base_url, prefer distinct endpoints to avoid Hermes official bug
- **Fix BUG-02: overwrites config**: `_sync_managed` flag — only update fallback_providers if not marked as managed
- **Critical loop fixes**: Fixed tk() tuple unpacking bug, ensure recovered credentials are added to pool

### v7.9.1 (2026-07-30)

- **Fixed Feishu status not showing "🔄 周公瑾使用中"**: `endpoint_base_url()` stripped the `/v3` version suffix from probed endpoints (e.g. `.../api/coding/v3/chat/completions` → `.../api/coding`), preventing URL healing. The identity comparison between Feishu record (without `/v3`) and config (with `/v3`) always failed, so no credential was ever marked as in-use.
- **Fix details**: `endpoint_base_url()` now only strips method suffixes (`/chat/completions`, `/messages`), preserving the version prefix. Also reordered `try_url_variants()` and `endpoint_candidates()` to prefer `/v3` over `/v1`.

### v7.9.0 (2026-07-30)

- **Fixed BUG-09: Pool key format**: `detect_provider()` leaked "ARK" into auth.json credential_pool key (producing `ark` instead of `custom:ark`). Added `_hermes_pool_key()` helper and `STANDARD_PROVIDERS` frozenset. Non-standard providers now get `custom:` prefix.
- **Fixed BUG-10: Provider name in config.yaml**: `detect_provider()` leaked "ARK" into fallback_providers entry provider and config.yaml model provider. Both now hardcode `HERMES_CUSTOM_PROVIDER = "custom"` for URL-backed records.
- **Fixed BUG-11: custom_providers cleared**: `cleanup_custom_providers()` removed entries whose Feishu base_urls lacked `/v3` suffix. Added `_strip_version_suffix()` for fuzzy matching of `/v1`/`/v3`-normalized URLs.
- **Fixed BUG-13: Exhausted credential re-added**: Rate-limited credentials with `额度已用完` or `quota exhausted` in error text are now skipped from the credential pool.
- **Bugs BUG-02/03/04/05 also resolved**: These were caused by the same `detect_provider()` leakage into pool key and provider fields.
- **Note**: BUG-12 (model/endpoint mismatch) is a Feishu data issue, not a code bug. The sync script correctly uses the model/endpoint from Feishu; the user must ensure compatible combinations in the table.
- **Added reference**: `references/v7.9.0-detect-provider-leak.md` with full bug inventory (BUG-09~13) and fix details.

### v7.8.0 (2026-07-30)

- **Fixed stale 备注 notes surviving health check cycles**: Health check success paths in 3 scripts (sync, cleanup, switch_next) now write "验证通过" instead of preserving old error notes. Failure paths now prefer the current error over stale notes, with status-specific fallbacks ("额度已用完" for S_R, "Key 无效" for S_I, "验证失败" for S_U).
- **Removed dead code**: `_usage_names`, `usage_remove`, `usage_add` functions deleted from sync_credential_pool.py — Agent markers have been in the status field since v7.3.3.
- **Fixed switch_next.py old credential note**: Old credential no longer unconditionally gets "额度已用完"; note is derived from its actual health status.
- **Added unified note policy**: Documented the 4-state note policy (PASS→验证通过, S_R→额度已用完, S_I→Key无效, S_U→验证失败) as the cross-script standard.
- **Known gap**: `auto_bootstrap.py` failed candidates still don't write back to Feishu — to be addressed in a future release.
- **Added reference**: `references/v7.8.0-stale-note-fix.md` with full bug inventory (10 items across 4 scripts) and fix details.

### v7.7.0 (2026-07-30)

- **Unified identity() across all scripts**: All scripts now use `(model, api_key, base_url)` triple-key format for credential identity. Previously `switch_next.py` used `(model, api_key)` dual-key while `sync_credential_pool.py` and `cleanup_feishu_status.py` used `(api_key, base_url, model)`.
- **Fixed switch_next.py endpoint discovery**: `tk()` now imported from `sync_credential_pool.py`, giving `switch_next.py` access to the full URL healing logic including /v3 endpoint support. Previously switch_next had its own stunted endpoint discovery that missed /v3 paths.
- **Fixed `normalise_base_url()` sharing**: `normalise_base_url()` is now a shared public function imported by both `switch_next.py` and `cleanup_feishu_status.py`, eliminating duplicate normalization logic.

### v7.6.0 (2026-07-30)

- **Fixed identity() dual-key mismatch**: `switch_next.py` `identity()` changed from `(api_key, base_url, model)` to `(model, api_key)` — the user's preferred "模型 + API key 双重确认唯一性" design principle. This fixes the bug where `switch_next.py` always claimed "切换成功" but never actually switched to a different credential.
- **Fixed `get_current_model_config()` model name extraction**: The function now maps `config.yaml`'s `default` key to `current["model"]` so that `identity()` can read the model name correctly. Previously, the model name was always `None` because `identity()` reads `record.get("model")` but the config uses `default`.
- **Added `ordered_candidates()` None-safety**: Explicitly handles `current_identity is None` to prevent false matches.
- **Added pitfall documentation**: Documented the `default` vs `model` key mismatch as a known pitfall, and the cross-script identity inconsistency (switch_next uses `(model, api_key)` but sync_credential_pool and cleanup_feishu_status still use `(api_key, base_url, model)`).
- **Added reference**: `references/v7.6.0-identity-mismatch.md` with full root cause analysis and verification steps.

### v7.4.0 (2026-07-30)

- **Added credential pool integrity protection**: New `MIN_POOL_RETENTION_RATIO = 0.5` constant guards against catastrophic pool shrinkage. When new candidate pool < 50% of existing AND > 50% of health checks returned `S_U` (unavailable), the existing pool is preserved with a warning instead of being replaced. Fixes the Trae-reported crash scenario where network-level 429 bursts caused all credentials to be deleted.
- **Extracted `normalise_base_url()` shared function**: ARK `api/plan` → `api/plan/v1` URL correction is now a reusable public function, eliminating the gap between `sync_credential_pool.py` and `switch_next.py` independent record normalizers. Both scripts now use the same normalization rule.
- **Fixed misleading comment in `sync_fallback_providers()`**: Comment said "跳过 429 的模型" but the code actually skips `S_I` (invalid), not `S_R` (rate limited). Updated to accurately describe the behavior.
- **Fixed edge case in pool protection**: When protection triggers and `fe` is empty, the final status message now says "保留现有 credential_pool" instead of misleading "已清空 credential_pool".

### v7.3.3 (2026-07-30)

- **Moved Agent usage marker from 备注 to 状态 field**: `_sync_unlocked()` now uses `status_add()`/`status_remove()` directly on the status field instead of writing "✅ 正常" for all valid credentials. Active credential shows "🔄 周公瑾使用中", others show "✅ 正常".
- **Removed `clear_current()` function**: The global clear-before-write pattern was replaced with per-record status maintenance. The function was dead code after the v7.3.3 refactor and is now deleted.
- **Fixed `health_status` variable name collision** in `cleanup_feishu_status.py`: The probe result variable was renamed from `health_status` to `probe_status` to avoid shadowing the imported `health_status()` function, which was causing a `NameError` when `S_U` was referenced.
- **Cleaned up `switch_next.py`**: Removed `_usage_names`/`usage_add`/`usage_remove` functions (no longer needed since Agent markers are in the status field, not 备注). Post-switch Feishu update now uses `status_remove()` on old credential and `status_add()` on new credential.
- **Added reference**: `references/v7.3.3-status-field-agent-display.md` with full fix details and cross-script consistency rules.

### v7.3.2 (2026-07-30)

- **Fixed 'custom' provider write-back inconsistency**: All 4 scripts now consistently handle the "custom" Provider value across read-normalize-write paths.
- **`sync_credential_pool.py`**: `url_updates` condition changed from `not original_provider` to `not original_provider or original_provider.lower() == "custom"` — ensures detected Provider is written back to Feishu even when the field says "custom" instead of empty.
- **`switch_next.py`**: `_normalise_record()` now checks `not provider or provider.lower() == "custom"` instead of just `not provider`.
- **`switch_next.py`**: `update_runtime_main_model()` now uses `record.get("provider") or detect_provider() or "custom"` — prefers the normalized record field over re-detection.
- **`auto_bootstrap.py`**: `write_runtime_config()` same triple-fallback pattern as switch_next.
- **`cleanup_feishu_status.py`**: Added `S_U` import and `cleanup_feishu_status()` now infers Provider from Base URL and writes it back to Feishu alongside status and note.
- **Added reference**: `references/v7.3.2-provider-writeback-fix.md` with full fix details and cross-script consistency rules.

### v7.3.1 (2026-07-29)

- **Fixed URL healing identity mismatch**: Added `healed_urls` dict to propagate healed URLs from `_sync_unlocked()` to `sync_fallback_providers()`. When URL healing occurs (e.g., `api/plan` → `api/plan/v1`), the healed URL is now used for health_results lookup in the fallback chain, preventing invalid/rate-limited credentials from being added.
- **Fixed model name normalization**: `identity()` in `cleanup_feishu_status.py` now normalizes model to `str(model or "").strip().lower()` for case-insensitive comparison, preventing config.yaml vs Feishu identity mismatches.
- **Added shared rotation lock**: `switch_next.py` and `auto_bootstrap.py` now share a `.rotation.lock` file with `sync_credential_pool.py`. The lock covers the full read-current → select → health-check → write-config sequence, preventing multi-process conflicts. The `run_sync()` subprocess is kept outside the lock to avoid reentrant locking.
- **`switch_next.py`**: `rotate_once()` function wraps the rotation lock. `main()` calls `run_sync()` before the lock, then `rotate_once()` inside it.
- **`auto_bootstrap.py`**: `try_switch()` function wraps the rotation lock. `main()` calls `run_sync()` before the lock, then `try_switch()` inside it.

### v7.3.0 (2026-07-29)

- **Provider Reverse-Inference**: `detect_provider()` now overrides empty or "custom" Provider values from Feishu with standard provider names (ARK, OPENAI, ANTHROPIC, DEEPSEEK, MOONSHOT, DASHSCOPE, Z.AI) based on Base URL hostname matching.
- **Removed `custom:` prefix**: Credential pool keys in `auth.json` no longer use the `custom:` prefix. Known providers use their standard name; unknown providers fall back to bare `custom`.
- **`repair_feishu_providers()` function**: New function in `cleanup_feishu_status.py` that reads all Feishu records, infers Provider from Base URL, and updates Feishu when current field is empty or "custom". Invoked via `--repair-provider` flag.
- **`switch_next.py`**: Now uses `detect_provider()` instead of hardcoded "custom". Added provider inference in `_normalise_record()` so empty-Provider records are not silently dropped.
- **`auto_bootstrap.py`**: Now uses `detect_provider()` instead of hardcoded "custom".
- **`sync_fallback_providers()`**: Fallback entry provider uses `rec.get("provider", detect_provider() or "custom")` instead of hardcoded "custom".
- **`_normalise_record()`**: Provider inference logic now prefers `detect_provider()` over "custom" fallback: `if inferred and (not provider or provider.lower() == "custom"): provider = inferred`

### v7.2.0 (2026-07-29)

- **Fix Feishu UI status management chaos**: Separated health status from usage status
- **Health status field**: Only shows "✅ 正常", "⛔ 限流", "❌ 无效", "🔄 检查中" 
- **Usage status field**: Agent ownership moved to 备注 field, no longer overwrites health status
- **Fixed core bug**: Health checks now correctly show failed status instead of "✅ 正常"
- **Auto-switch 429 fallback**: When main model is 429, automatically switches to first healthy non-vision model
- **Updated sync()**: Uses health_status() and usage_add/remove() functions for clean separation
- **Updated switch_next.py**: Now properly manages usage status in 备注 field
- **Updated cleanup_feishu_status.py**: Reconciles health and usage status separately
- **Added _cleanup_429_fallback_config()**: Removes 429 entries from config.yaml fallback_providers
- **Improved sync_fallback_providers()**: Filters 429 records and excludes current main model
- **Fixed subprocess encoding**: Added encoding='utf-8', errors='replace' to all subprocess calls

- Fixed `subprocess.run` missing `encoding='utf-8', errors='replace'` in all three scripts (sync, switch_next, auto_bootstrap). Windows default encoding caused `UnicodeDecodeError` on Chinese output.
- Fixed `sync_credential_pool.py` docstring version from v2.0 to v7.0.0.
- Rewrote `cron_sync.sh` to use the known skill directory path instead of a fragile cross-drive `find` search.
- `auto_bootstrap.py` now writes Feishu status (marks new credential as in-use) after a successful switch, matching `switch_next.py` behavior.
- Increased `auto_bootstrap.py` sync timeout from 20s to 60s to avoid premature timeout on slow networks.

### v7.0.0 (2026-07-28)

- Deferred all Feishu mutations until after the atomic `auth.json` commit.
- Added corrupt/missing `auth.json` recovery while preserving parseable top-level data.
- Fixed cleanup to mark only the exact active credential as in-use.
- Removed hardcoded Feishu application credentials and added Hermes config loading.
- Added one full-sync retry when candidate selection fails.
- Added best-effort full reconciliation after every successful switch.
- Made the installed skill self-contained and updated Gateway startup to use it.

### v6.0.0

- Added cross-provider rotation, URL normalization, model limits, Feishu status updates, cleanup tooling, and `--skip-sync`.

### v5.x and earlier

- Added atomic writes, Windows file locking, unified health checks, retries, fallback cleanup, and bootstrap integration.
