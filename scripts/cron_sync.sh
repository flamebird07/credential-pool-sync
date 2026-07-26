#!/usr/bin/env bash
# 凭证池同步包装脚本 - 供 cron 使用
if [ -f "$HOME/.hermes/.env" ]; then
  set -a; source "$HOME/.hermes/.env"; set +a
fi
cd /c/Users/Administrator/credential-pool-sync
python scripts/sync_credential_pool.py
