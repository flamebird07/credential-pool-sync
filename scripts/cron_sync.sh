#!/usr/bin/env bash
# Credential pool sync — designed to be called by cron.
# Uses the installed skill directory path directly, no fragile find.
if [ -f "$HOME/.hermes/.env" ]; then set -a; source "$HOME/.hermes/.env"; set +a; fi
SKILL_DIR="$HOME/AppData/Local/hermes/skills/devops/credential-pool-sync"
cd "$SKILL_DIR" && python scripts/sync_credential_pool.py
