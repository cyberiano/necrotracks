#!/usr/bin/env bash
# Despliega origin/main en la Pi: pull, dependencias, units y restart.
# Uso: bin/deploy.sh [--fast] [usuario@host]    --fast = solo pull + restart
set -euo pipefail
FAST=0
[ "${1:-}" = "--fast" ] && { FAST=1; shift; }
HOST="${1:-${NECROTRACKS_HOST:-necrotracks@192.168.1.228}}"
cd "$(dirname "$0")/.."

git fetch -q origin
[ -z "$(git status --porcelain --untracked-files=no)" ] || echo "⚠ Hay cambios sin commitear: no se despliegan."
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "HEAD local ($(git rev-parse --short HEAD)) ≠ origin/main ($(git rev-parse --short origin/main)). Hacé push primero."
  exit 1
fi

ssh "$HOST" "FAST=$FAST bash -s" << 'REMOTE'
set -euo pipefail
cd /opt/necrotracks
git pull -q --ff-only
[ "$FAST" = 1 ] || .venv/bin/pip install -q -r requirements.txt
sudo NT_USER="$(whoami)" bash bin/remote/units.sh
units=$(cd systemd 2>/dev/null && ls *.service 2>/dev/null || true)
if [ -n "$units" ]; then
  sudo systemctl restart $units
  for u in $units; do echo "   $u: $(systemctl is-active "$u")"; done
fi
echo "==> Desplegado $(git rev-parse --short HEAD)"
REMOTE
