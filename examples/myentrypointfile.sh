#!/usr/bin/env bash
set -euo pipefail

echo "[ara-entrypoint] setup start"

# Bare-minimum example:
# - this file runs before the automation itself
# - keep it idempotent (safe to run multiple times)

# Optional additions you can enable later:
# 1) Create/use a venv
# if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
# . .venv/bin/activate
#
# 2) Install dependencies
# pip install --upgrade pip
# pip install resend==2.4.0
#
# 3) Export extra env vars
# export MY_RUNTIME_FLAG="1"

echo "[ara-entrypoint] setup done"
