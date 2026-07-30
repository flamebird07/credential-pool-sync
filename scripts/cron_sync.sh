#!/usr/bin/env bash
# Credential pool sync — designed to be called by cron.
# Uses the installed skill directory path directly, no fragile find.
if [ -f "$HOME/.hermes/.env" ]; then set -a; source "$HOME/.hermes/.env"; set +a; fi
SKILL_DIR="$HOME/AppData/Local/hermes/skills/devops/credential-pool-sync"
PID_FILE="$SKILL_DIR/.cron_sync.pid"
if [ -e "$PID_FILE" ]; then
    old_pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "credential sync already running (pid $old_pid)"
        exit 0
    fi
fi
printf '%s\n' "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
cd "$SKILL_DIR" && python scripts/sync_credential_pool.py
