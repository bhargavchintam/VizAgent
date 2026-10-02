#!/usr/bin/env bash
# One-time setup on the workshop VM, from the repo root:  bash scripts/vm_setup.sh
# - copies the organizers' Cursor skills + rules so `agent` in this repo knows the stack
# - creates a virtualenv with all dependencies
set -euo pipefail

STARTER="${STARTER:-$HOME/vast-builders-challenge}"

if [[ -d "$STARTER/.cursor" ]]; then
  (cd "$STARTER" && git pull --ff-only || true)
  rm -rf .cursor && cp -R "$STARTER/.cursor" .cursor
  echo "Copied Cursor skills from $STARTER"
else
  echo "warning: $STARTER/.cursor not found; Cursor skills not copied"
fi

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-dev.txt
echo "Done. Activate with: source .venv/bin/activate"
echo "Run locally (dev only): python app/main.py   then open http://localhost:8080/"
