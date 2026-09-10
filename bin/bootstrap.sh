#!/usr/bin/env bash
# Provisiona la Pi desde cero. Idempotente: se puede correr de nuevo sin romper nada.
# Uso: bin/bootstrap.sh [usuario@host]
set -euo pipefail
HOST="${1:-${NECROTRACKS_HOST:-necrotracks@192.168.1.228}}"
DIR="$(cd "$(dirname "$0")" && pwd)"

ssh "$HOST" 'sudo -n true' 2>/dev/null || { echo "sudo sin password no está habilitado en $HOST"; exit 1; }
ssh "$HOST" "sudo NT_USER=\$(whoami) bash -s" < "$DIR/remote/setup.sh"
