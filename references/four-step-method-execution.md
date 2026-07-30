# Four-Step Method Execution Reference

This document captures the four-step method execution pattern used in the credential-pool-sync v7.2.0 optimization task.

## Four-Step Method Overview

The user requires strict adherence to a four-step method for code review and optimization:

1. **Step 1: Codex CLI Review** - Understand the problem, read code, confirm issues exist
2. **Step 2: Codex CLI Solution** - Create a detailed fix plan (read-only, --ephemeral)
3. **Step 3: Codex CLI Implementation** - Execute the fix (-s danger-full-access)
4. **Step 4: MiMo Code Review** - Verify the implementation and identify issues

## Key Principles

### Separation of Concerns
- **Review cannot implement**: The reviewer (Step 1) cannot be the implementer (Step 3)
- **Clear handoffs**: Each step must be completed before moving to the next
- **No shortcuts**: Skipping steps is not allowed

### CLI Tool Requirements
- **Step 2**: Must use `--ephemeral --skip-git-repo-check` (read-only, no workspace changes)
- **Step 3**: Must declare CLI tool before execution: "Step 3 implementation CLI: Codex CLI -s danger-full-access"
- **Step 4**: Must use MiMo Code for final verification

### Loop Pattern
If Step 4 identifies issues, return to Step 2 with the new findings:
- Step 4 → Step 2 (revised plan) → Step 3 (revised implementation) → Step 4 (final verification)

## Example Execution Pattern

### v7.2.0 Feishu UI Optimization Task

**Step 1: Review**
```bash
# Problem: Feishu table UI status management chaos
# Findings: Status field mixed health and usage info, 429 not auto-switching
# Output: 4 mismatch paths, detailed analysis
```

**Step 2: Solution**
```bash
# Plan: Separate health status from usage status
# 11-point detailed fix plan
# --ephemeral flag ensures no workspace changes
```

**Step 3: Implementation**
```bash
# CLI declaration: "Step 3 implementation CLI: Codex CLI -s danger-full-access"
# Code changes: Add health_status(), usage_add(), usage_remove() functions
# Fix 429 auto-switch logic
```

**Step 4: Review**
```bash
# Issues found: 1) New model status not updated, 2) unused parameter
# Return to Step 2 for revised plan
```

**Loop 2:**
- Step 2: Revised plan addressing MiMo findings
- Step 3: Implementation fixes
- Step 4: Final verification

## Post-Completion Self-Audit

After completing all four steps, perform a self-audit:

| Check | Result | Evidence |
|-------|--------|----------|
| Step 2 was read-only | ✅/❌ | Verify `--ephemeral` used, no workspace changes |
| Step 3 CLI declared | ✅/❌ | Check CLI declaration in output |
| All 4 steps executed | ✅/❌ | Verify complete cycle |
| Verification completed | ✅/❌ | Actual test execution, not just code review |

## User Corrections Applied

### Memory Management
- **Issue**: Incorrect Obsidian location in memory
- **Correction**: Obsidian is on 10.0.0.50, not local
- **Action**: Updated memory with correct path

### Documentation Process
- **Issue**: Need to document in Obsidian, not just local files
- **Correction**: SCP documents to remote Obsidian
- **Action**: Created comprehensive documentation and synced to E:\obsidian\obs\凭证池技能\

### Task Management
- **Issue**: Task list showed incomplete status
- **Correction**: Updated todo list to reflect completed work
- **Action**: Marked all four steps as completed

## Pitfalls to Avoid

1. **Skipping verification**: Never summarize as "done" without actual testing
2. **CLI tool not declared**: Always declare Step 3 CLI tool before execution
3. **Memory errors**: Double-check workspace configurations
4. **Document location**: Remember Obsidian is remote, not local
5. **Incomplete cycles**: Ensure all four steps are completed before declaring success

## Success Metrics

- Complete four-step cycle without shortcuts
- Actual testing (not just code review)
- Proper documentation in Obsidian
- Memory accuracy maintained
- Task status correctly updated