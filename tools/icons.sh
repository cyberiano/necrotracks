#!/usr/bin/env bash
# Genera los PNG del ícono (pantalla de inicio del iPhone y manifest) desde web/static/icons/icon.svg.
# Solo en la Mac: usa qlmanage (render de SVG) y sips (redimensionar). Los PNG van al repo.
set -euo pipefail
cd "$(dirname "$0")/../web/static/icons"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
qlmanage -t -s 1024 -o "$tmp" icon.svg > /dev/null
for size in 180 192 512; do
  sips -z "$size" "$size" "$tmp/icon.svg.png" --out "icon-$size.png" > /dev/null
done
ls -la icon-*.png
