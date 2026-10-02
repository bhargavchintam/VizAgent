#!/usr/bin/env bash
# One-time setup on the workshop VM, from the repo root:  bash scripts/vm_setup.sh
# - copies the organizers' Cursor skills + rules, and adds our vision-zero-sweep skill,
#   so `agent` (Cursor CLI) in this repo knows the stack and can run a sweep
# - creates .venv with the app's dependencies (the VM image has no venv module or pip)
# - installs kubectl into ~/.local/bin if it is missing (the deploy needs it)
set -euo pipefail

STARTER="${STARTER:-$HOME/vast-builders-challenge}"

if [[ -d "$STARTER/.cursor" ]]; then
  (cd "$STARTER" && git pull --ff-only || true)
  rm -rf .cursor && cp -R "$STARTER/.cursor" .cursor
  echo "Copied Cursor skills from $STARTER"
else
  echo "warning: $STARTER/.cursor not found; organizer skills not copied"
fi
mkdir -p .cursor/skills && cp -R skills/vision-zero-sweep .cursor/skills/
echo "Added the vision-zero-sweep skill for Cursor"

if [[ ! -x .venv/bin/python ]]; then
  if ! python3 -m venv .venv 2>/dev/null; then
    rm -rf .venv
    python3 -m venv --without-pip .venv
  fi
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  python3 -c "import urllib.request as u; u.urlretrieve('https://bootstrap.pypa.io/get-pip.py', '/tmp/get-pip.py')"
  .venv/bin/python /tmp/get-pip.py -q
fi
.venv/bin/python -m pip install -q -r requirements.txt
echo "Python packages installed in .venv"

if ! command -v kubectl >/dev/null 2>&1; then
  KUBECTL_VERSION=v1.31.0
  mkdir -p "$HOME/.local/bin"
  (cd /tmp && curl -sSLO "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl" \
    && curl -sSLO "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl.sha256" \
    && echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check --quiet \
    && install -m 0755 kubectl "$HOME/.local/bin/kubectl")
  echo "Installed kubectl $KUBECTL_VERSION (checksum verified) in ~/.local/bin"
fi

echo "Done. Next: .venv/bin/python scripts/vm_run.py"
