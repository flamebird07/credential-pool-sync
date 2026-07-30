---
name: credential-pool-sync
description: "Synchronize, health-check, rotate, and reconcile Hermes credentials stored in Feishu Bitable. v7.6.0 fixes identity() to use (model, api_key) dual-key instead of (api_key, base_url, model), fixing the 'switch never changes' bug."
version: 7.6.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [credential-pool, feishu, sync, devops]
    related_skills: [feishu-bitable, hermes-agent]
---

# Credential Pool Sync v7.6.0

This skill synchronizes API credentials from Feishu into Hermes `auth.json`, rotates the active model in `config.yaml`, and reconciles Feishu health/in-use status. The installed skill directory is canonical at:

`%LOCALAPPDATA%\hermes\skills\devops\credential-pool-sync`

The external `credential-pool-sync` repository is the development source only. Runtime scripts live in this skill's own `scripts/` directory, so the installed skill is standalone.

## Four-Step Method Execution

This skill was optimized using the user's required four-step method:

1. **Step 1: Codex CLI Review** - Identified Feishu UI status management issues
2. **Step 2: Codex CLI Solution** - Created detailed 11-point fix plan (--ephemeral)
3. **Step 3: Codex CLI Implementation** - Executed fixes (-s danger-full-access)
4. **Step 4: MiMo Code Review** - Verified implementation and identified issues

The method requires strict separation: reviewers cannot be implementers, and each step must be completed before the next. See `references/four-step-method-execution.md` for detailed execution patterns.

## Trigger matrix

| User intent / event | Command | Sync mode | Result |
|---|---|---|---|
| Synchronize or refresh all credentials | `python scripts/sync_credential_pool.py` | Full | Reads Feishu, health-checks every key, atomically writes `auth.json`, then reconciles Feishu |
| Refresh local pool quickly | `python scripts/sync_credential_pool.py --skip-health-rotate` | Lightweight | Reads Feishu and writes `auth.json`; skips health checks, Feishu writes, and fallback cleanup |
| Switch to the next credential | `python scripts/switch_next.py` | Lightweight before selection; full after switch | Rotates to the next healthy key and performs a best-effort full refresh |
| Switch using the existing local pool | `python scripts/switch_next.py --skip-sync` | No initial sync; full only on zero candidates and after success | Preserves backward-compatible local-first behavior |
| Gateway startup | `python scripts/auto_bootstrap.py` | Lightweight discovery plus per-candidate checks | Selects the first healthy non-current credential |
| Repair Feishu status | `python scripts/cleanup_feishu_status.py` | Full health scan | Matches active `(api_key, base_url, model)` from `config.yaml`; only that credential is marked in-use |
| Repair Feishu Provider field | `python scripts/cleanup_feishu_status.py --repair-provider` | Read-only (no health check) | Infers standard Provider (ARK, OPENAI, etc.) from Base URL; updates Feishu when current is empty or "custom" |

## Full versus lightweight sync

A full sync reads Feishu, health-checks all records, builds the complete local credential pool, atomically commits `auth.json`, and only afterward updates Feishu statuses. This ordering prevents a failed health check or local write from corrupting Feishu's derived state.

A lightweight sync (`--skip-health-rotate`) reads Feishu and rebuilds `auth.json` without health checks, Feishu status writes, or fallback cleanup. It is intended for fast discovery, not authoritative reconciliation.

## Rotation behavior

`switch_next.py` identifies the current credential by the `(model, api_key)` dual-key pair (model + API key, NOT base_url). This is the user's preferred uniqueness rule — see "Pitfall: config.yaml 'default' key vs 'model' key identity mismatch" below for why this matters. Candidates are ordered by Feishu return order (not priority), and each candidate is health-checked before switching. `config.yaml` is written atomically with a UUID temporary file, flush, `fsync`, `os.replace`, and a Windows `msvcrt.locking` lock.

If the first pass finds no healthy candidate, the script runs one full sync and retries selection exactly once. This applies even with `--skip-sync`; that flag skips only the initial sync.

After a successful configuration switch and Feishu status attempt, the script runs a full sync. A failure in this post-switch refresh is logged as a warning and never rolls back the successful switch.

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
| `switch_next.py` | `update_runtime_main_model()` | `"provider": detect_provider(...) or "custom"` | `"provider": record.get("provider") or detect_provider(...) or "custom"` |
| `auto_bootstrap.py` | `write_runtime_config()` | `"provider": detect_provider(...) or "custom"` | `"provider": record.get("provider") or detect_provider(...) or "custom"` |
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

**Design principle**: credential identity is `(model, api_key)` — model + API key dual-key, NOT `(api_key, base_url, model)`. This was explicitly chosen by the user because:
- Multiple Feishu records can share the same API key (different models under one account)
- `base_url` is unstable (normalise_base_url() adds/removes /v3 suffixes)
- A credential is uniquely identified by "which model on which API key"

## Pitfall: Identity inconsistency across scripts

`switch_next.py` now uses `(model, api_key)` for identity, but the other scripts still use the old `(api_key, base_url, model)` format:

| Script | Identity format | Status |
|--------|----------------|--------|
| `switch_next.py` | `(model, api_key)` | ✅ Fixed v7.6.0 |
| `cleanup_feishu_status.py` | `(api_key, base_url, model)` | ❌ Still old format |
| `sync_credential_pool.py` (_sync_unlocked) | `(api_key, base_url, model)` | ❌ Still old format |
| `sync_credential_pool.py` (sync_fallback_providers) | `(api_key, base_url, model)` | ❌ Still old format |

**Symptom**: `switch_next.py` and `cleanup_feishu_status.py` disagree on which credential is "current" when:
- Multiple records share the same API key (different models)
- `base_url` has been normalized differently

**Fix**: Update all scripts to use the same `(model, api_key)` identity format. The `cleanup_feishu_status.py` `active_identity()` already reads `model.get("default", "")` correctly — it just passes it through the old `identity()` function that returns `(api_key, base_url, model)`. The fix is to change `identity()` and `active_identity()` to return `(model, api_key)`.

TODO: When modifying `cleanup_feishu_status.py` and `sync_credential_pool.py`, follow the same pattern as `switch_next.py`:
- `identity()` returns `(model, api_key)` — model first, api_key second
- `active_identity()` maps `default` → `model` before calling identity()

If `auth.json` is missing, invalid JSON, unreadable, incorrectly encoded, or has a non-object root, full/lightweight synchronization treats it as an empty pool and rebuilds it from Feishu. When the existing document is parseable, unrelated top-level keys such as `version`, `providers`, and other metadata are preserved. A completely unusable file is replaced atomically with a minimal valid structure.

## Feishu credentials

No Feishu application credentials are embedded in source. Configure both environment variables:

```powershell
$env:FEISHU_APP_ID = "..."
$env:FEISHU_APP_SECRET = "..."
```

Alternatively configure `secrets.feishu.app_id` and `secrets.feishu.app_secret` in `%LOCALAPPDATA%\hermes\config.yaml`. Top-level `feishu` and `channels.feishu` mappings are also recognized for compatibility. Missing credentials produce a clear error without logging secret values.

## Cleanup semantics

`cleanup_feishu_status.py` reads the active model from Hermes `config.yaml` and compares the complete `(api_key, base_url, model)` identity. A healthy active credential receives this agent's in-use marker in the status field (via `status_add()`). Other healthy credentials are marked `✅ 正常`, or retain another agent's in-use marker via `status_remove()`. Unhealthy records lose only this agent's marker and receive the health result. It never marks every healthy credential as in-use.

## Provider Reverse-Inference

`detect_provider(base_url)` infers the standard Hermes provider name from the Base URL hostname:

| URL Hostname | Inferred Provider |
|---|---|
| `ark.cn-beijing.volces.com` | ARK |
| `open.bigmodel.cn` | Z.AI |
| `api.openai.com` | OPENAI |
| `api.anthropic.com` | ANTHROPIC |
| `api.deepseek.com` | DEEPSEEK |
| `api.moonshot.cn` | MOONSHOT |
| `dashscope.aliyuncs.com` | DASHSCOPE |

The inference is used in three places:
1. **`_normalise_record()`** — when Feishu's Provider field is empty or "custom", the inferred provider overrides it
2. **`sync_fallback_providers()`** — fallback entries use `detect_provider()` instead of hardcoded "custom"
3. **Credential pool keys** — no longer prefixed with `custom:`; known providers use their standard name

To repair existing Feishu records with empty or stale Provider fields:
```bash
python scripts/cleanup_feishu_status.py --repair-provider
```

This scans all records, infers the correct Provider from the Base URL, and only updates when the current value is empty or "custom".

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

### Script-specific implementation

| Script | Function | How it sets status | Notes |
|---|---|---|---|
| `sync_credential_pool.py` | `_sync_unlocked()` | Per-record `status_add`/`status_remove`/`health_status` | `clear_current()` was removed in v7.3.3 — status is now maintained per-record |
| `cleanup_feishu_status.py` | `cleanup_feishu_status()` | `status_add`/`status_remove`/`health_status` | Uses `probe_status` (not `health_status`) to avoid fn name collision |
| `switch_next.py` | Post-switch update | `status_remove` on old, `status_add` on new | `usage_add`/`usage_remove` functions removed in v7.3.3 |
| `auto_bootstrap.py` | `try_switch()` → success | `status_add` on new credential | Already correct; no change needed |

## References

- `references/v7-audit-findings.md` — 2026-07-28 v7.0.0 全面审查发现的问题清单、修复详情和触发矩阵
- `references/v7.1-audit-findings.md` — 2026-07-29 v7.1.0 审查：subprocess编码修复、cron_sync.sh路径修正、auto_bootstrap飞书回写补全
- `references/v7.3-crash-analysis.md` — 2026-07-29 429 级联崩溃分析：从 Trae 报告的 5 个 Bug 的根因、时间线和修复措施
- `references/v7-audit-findings.md` — 2026-07-28 v7.0.0 全面审查发现的问题清单、修复详情和触发矩阵
- `references/v7.1-audit-findings.md` — 2026-07-29 v7.1.0 审查：subprocess编码修复、cron_sync.sh路径修正、auto_bootstrap飞书回写补全
- `references/v7.3.2-provider-writeback-fix.md` — 2026-07-30 v7.3.2 Provider 字段回写不一致修复：4 个脚本中 "custom" 处理条件不一致的 6 处修复
- `references/v7.3.3-status-field-agent-display.md` — 2026-07-30 v7.3.3 状态栏 Agent 标记显示修复：Agent 使用信息从备注栏迁移到状态栏，删除 clear_current()，修复 health_status 变量名冲突
- `references/v7.4.0-integrity-protection.md` — 2026-07-30 v7.4.0 凭证池完整性保护、URL 标准化统一、注释修正；四步法审计记录
- `references/v7.6.0-identity-mismatch.md` — 2026-07-30 v7.6.0 identity 双重确认修复：switch_next.py identity 从 (api_key, base_url, model) 改为 (model, api_key)，解决切换不生效的问题

## Files

```text
credential-pool-sync/
├── SKILL.md
├── references/
│   ├── v7.3-crash-analysis.md
│   ├── v7-audit-findings.md
│   ├── v7.1-audit-findings.md
│   ├── v7.3.2-provider-writeback-fix.md
│   ├── v7.3.3-status-field-agent-display.md
│   ├── v7.4.0-integrity-protection.md
│   └── v7.6.0-identity-mismatch.md
└── scripts/
    ├── sync_credential_pool.py
    ├── switch_next.py
    ├── auto_bootstrap.py
    ├── cleanup_feishu_status.py
    ├── cron_sync.sh
    └── four-step-template.py
```

Hermes `start_gateway.py` launches `auto_bootstrap.py` from this installed skill directory, not from `%USERPROFILE%\credential-pool-sync`.

## Version history

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
