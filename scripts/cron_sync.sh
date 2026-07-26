#!/usr/bin/env bash
if [ -f "$HOME/.hermes/.env" ]; then set -a; source "$HOME/.hermes/.env"; set +a; fi
DIR=$(dirname "$(find /c /d /e /f "$HOME" -maxdepth 5 -name sync_credential_pool.py -path "*/credential-pool-sync/*" 2>/dev/null | head -1)")
cd "$DIR/.." && python scripts/sync_credential_pool.py
