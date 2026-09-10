# Instala/actualiza los units de systemd del repo. Corre como root. Lo usan setup.sh y deploy.sh.
set -euo pipefail
NT_USER="${NT_USER:?}"
APP=/opt/necrotracks
shopt -s nullglob
units=()
for u in "$APP"/systemd/*.service; do
  name="$(basename "$u")"
  sed "s|@USER@|$NT_USER|g" "$u" > "/etc/systemd/system/$name"
  units+=("$name")
done
systemctl daemon-reload
if [ ${#units[@]} -gt 0 ]; then
  systemctl enable -q "${units[@]}"
  echo "   units: ${units[*]}"
else
  echo "   todavía no hay units en el repo"
fi
