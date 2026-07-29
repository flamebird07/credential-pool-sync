---
name: credential-pool-sync
description: "Synchronize, health-check, rotate, and reconcile Hermes credentials stored in Feishu Bitable."
version: 7.2.0
author: Hermes Agent
platforms: [windows]
metadata:
  hermes:
    tags: [credential-pool, feishu, sync, devops]
    related_skills: [feishu-bitable, hermes-agent]
---

# Credential Pool Sync v7.2.0

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

## Full versus lightweight sync

A full sync reads Feishu, health-checks all records, builds the complete local credential pool, atomically commits `auth.json`, and only afterward updates Feishu statuses. This ordering prevents a failed health check or local write from corrupting Feishu's derived state.

A lightweight sync (`--skip-health-rotate`) reads Feishu and rebuilds `auth.json` without health checks, Feishu status writes, or fallback cleanup. It is intended for fast discovery, not authoritative reconciliation.

## Rotation behavior

`switch_next.py` identifies the current credential by the `(api_key, base_url, model)` triple, orders candidates by priority, and checks each candidate before switching. `config.yaml` is written atomically with a UUID temporary file, flush, `fsync`, `os.replace`, and a Windows `msvcrt.locking` lock.

If the first pass finds no healthy candidate, the script runs one full sync and retries selection exactly once. This applies even with `--skip-sync`; that flag skips only the initial sync.

After a successful configuration switch and Feishu status attempt, the script runs a full sync. A failure in this post-switch refresh is logged as a warning and never rolls back the successful switch.

## Pitfall: "Wrong model displayed" ≠ model switch needed

When a user reports that Hermes shows the wrong model (e.g. a 429'd model name in the session banner), **always read `config.yaml` first before attempting any credential switch or health check**. The fallback chain may have already rotated the active model in `config.yaml`. The display discrepancy is often a session-cache or Hermes display-layer issue, not a config problem. Running `switch_next.py` or health checks prematurely wastes time and may rotate to a worse credential. Steps:

1. Read `config.yaml` → check `model.default` and `model.api_key`.
2. If the model matches what the user expects, the issue is display-layer, not credential-pool.
3. Only if `config.yaml` still points to the 429'd model should you proceed with `switch_next.py` or a full sync.

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

`cleanup_feishu_status.py` reads the active model from Hermes `config.yaml` and compares the complete `(api_key, base_url, model)` identity. A healthy active credential receives this agent's in-use marker. Other healthy credentials are marked `✅ 正常`, or retain another agent's in-use marker. Unhealthy records lose only this agent's marker and receive the health result. It never marks every healthy credential as in-use.

## References

- `references/v7-audit-findings.md` — 2026-07-28 v7.0.0 全面审查发现的问题清单、修复详情和触发矩阵
- `references/v7.1-audit-findings.md` — 2026-07-29 v7.1.0 审查：subprocess编码修复、cron_sync.sh路径修正、auto_bootstrap飞书回写补全
- `references/four-step-method-execution.md` — 四步法执行模式：Step1审查→Step2方案→Step3实施→Step4验证，包含实际执行示例和用户纠正记录

## Files

```text
credential-pool-sync/
├── SKILL.md
├── references/
│   ├── v7-audit-findings.md
│   ├── v7.1-audit-findings.md
│   └── four-step-method-execution.md
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
