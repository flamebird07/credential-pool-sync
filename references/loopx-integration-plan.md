# LoopX Integration Plan for credential-pool-sync

## Overview

Integrate LoopX as a real-time guardian for credential pool health, providing:
- 5-minute health checks (vs 2-hour cron)
- Auto-switch on failure with gate verification
- Alert on degradation (<80% health)
- Evidence logging for audit trail

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    LoopX Control Plane                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Goals     │  │   Gates     │  │  Evidence   │        │
│  │ (health)    │  │ (quality)   │  │  (audit)    │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Quota Manager                           │   │
│  │  - Max checks per day: 288 (every 5 min)            │   │
│  │  - Max switches per day: 10                          │   │
│  │  - Cooldown between switches: 5 minutes              │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              credential-pool-sync Scripts                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │sync_credential│  │switch_next  │  │cleanup_feishu│        │
│  │   pool.py    │  │   .py       │  │  status.py   │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Implementation Plan

### Phase 1: LoopX Setup ✅

1. **Initialize LoopX project** ✅
   - Goal ID: `credential-pool-sync-goal`
   - Registry: `.loopx/registry.json`
   - State: `.codex/goals/credential-pool-sync-goal/ACTIVE_GOAL_STATE.md`

2. **Configure Goal** ✅
   - Quota: 0.5 (720 checks per 24 hours)
   - Self-repair: enabled
   - Orchestration: default mode

3. **Create Monitoring Todos** ✅
   - monitor-001: 每5分钟检查凭证池健康状态
   - monitor-002: 凭证切换前gate验证
   - monitor-003: 凭证池健康度告警

### Phase 2: Cron Job Setup ✅

1. **Created "凭证池实时守护" cron job** ✅
   - Frequency: every 5 minutes
   - Next run: 2026-08-07 21:04
   - Delivery: feishu

### Phase 3: Script Integration (Pending)

1. **Create loopx_integration.py helper module**
   - log_evidence(): Record evidence to LoopX
   - check_quota(): Check quota before operations
   - record_switch(): Record credential switch events
   - record_health_check(): Record health check results
   - record_sync(): Record sync operations

2. **Modify sync_credential_pool.py**
   - Add LoopX evidence logging after sync

3. **Modify switch_next.py**
   - Add LoopX gates and evidence logging

4. **Modify cleanup_feishu_status.py**
   - Add LoopX evidence for cleanup operations

### Phase 4: Gate Configuration (Pending)

1. **Create credential_switch_gate.py**
   - Validate switch safety
   - Check model compatibility
   - Verify required fields

## Key Improvements

| Current | LoopX | Improvement |
|---------|-------|-------------|
| 2-hour checks | 5-minute checks | 24x faster detection |
| No gate verification | Gate before switch | Prevent bad switches |
| No real-time alerts | Alert on <80% health | Prevent downtime |
| No evidence trail | LoopX evidence logging | Full audit trail |

## Benefits

1. **Faster Detection**: Issues found in 5 minutes vs 2 hours
2. **Automatic Recovery**: Auto-switch on failure
3. **Quality Assurance**: Gate verification before switches
4. **Real-time Alerts**: Immediate notification on degradation
5. **Audit Trail**: Complete history of all operations

## Next Steps

1. Create loopx_integration.py helper module
2. Modify scripts to use LoopX
3. Test the integration
4. Monitor for 24 hours
5. Document results
