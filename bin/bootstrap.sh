#!/usr/bin/env bash
# Provisiona la Pi desde cero. Idempotente: se puede correr de nuevo sin romper nada.
# Uso: bin/bootstrap.sh [usuario@host]
#      HOTSPOT_PSK=... bin/bootstrap.sh    la primera vez, para crear el hotspot (la clave no va al repo)
set -euo pipefail
HOST="${1:-${NECROTRACKS_HOST:-necrotracks@necrotracks.local}}"
DIR="$(cd "$(dirname "$0")" && pwd)"

ssh "$HOST" 'sudo -n true' 2>/dev/null || { echo "sudo sin password no está habilitado en $HOST"; exit 1; }
ssh "$HOST" "sudo NT_USER=\$(whoami) HOTSPOT_PSK=$(printf %q "${HOTSPOT_PSK:-}") bash -s" < "$DIR/remote/setup.sh"
