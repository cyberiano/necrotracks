# Corre en la Pi como root (lo invoca bin/bootstrap.sh). NT_USER = usuario dueño de la app.
set -euo pipefail
NT_USER="${NT_USER:?}"
REPO_URL="https://github.com/cyberiano/necrotracks.git"
APP=/opt/necrotracks
DATA=/var/lib/necrotracks
CFG=/boot/firmware/config.txt
REBOOT=0
log() { echo "==> $*"; }

log "Paquetes"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  git python3-venv python3-rtmidi python3-lgpio libportaudio2 alsa-utils i2c-tools

# Un dtparam de la base tiene que ir ANTES del primer dtoverlay: si va después,
# el firmware lo aplica a ese overlay y lo ignora. Mueve la línea si está mal ubicada.
base_param() {
  local first_ov line
  first_ov=$(grep -nE '^dtoverlay=' "$CFG" | head -1 | cut -d: -f1)
  line=$(grep -nxF "$1" "$CFG" | head -1 | cut -d: -f1)
  if [ -n "$line" ] && [ -n "$first_ov" ] && [ "$line" -lt "$first_ov" ]; then return 0; fi
  sed -i "\\|^$1\$|d" "$CFG"
  first_ov=$(grep -nE '^dtoverlay=' "$CFG" | head -1 | cut -d: -f1)
  if [ -n "$first_ov" ]; then sed -i "${first_ov}i $1" "$CFG"; else echo "$1" >> "$CFG"; fi
  REBOOT=1
}

# Parámetro del kernel en cmdline.txt. Es una sola línea: si queda mal, la Pi no arranca.
# Copia, agrega, verifica; si algo no cierra, restaura la copia.
cmdline_param() {
  local c="${CMDLINE:-/boot/firmware/cmdline.txt}" b
  grep -qw -- "$1" "$c" && return 0
  b="$c.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$c" "$b"
  sed -i "1 s/\$/ $1/" "$c"
  if [ "$(wc -l < "$c")" -gt 1 ] || ! grep -q "root=" "$c" || ! grep -qw -- "$1" "$c"; then
    cp "$b" "$c"
    echo "   cmdline.txt quedó mal: se restauró la copia"
    return 1
  fi
  REBOOT=1
}

log "I2C (OLED) y SPI"
[ -e /dev/i2c-1 ] || REBOOT=1
raspi-config nonint do_i2c 0
raspi-config nonint do_spi 0
base_param 'dtparam=i2c_arm_baudrate=400000'

log "USB en full speed en la Pi 3 (el iRig se traba con dwc_otg en high speed, ver docs/PLAN.md)"
if grep -q "Raspberry Pi 3" /proc/device-tree/model 2>/dev/null; then
  cmdline_param "dwc_otg.speed=1"
fi

log "Sin audio onboard, sin audio HDMI, sin Bluetooth"
cfg_replace() { if grep -qE "$1" "$CFG"; then sed -i -E "s|$1|$2|" "$CFG"; REBOOT=1; fi; }
cfg_replace '^dtparam=audio=on$' 'dtparam=audio=off'
cfg_replace '^dtoverlay=vc4-kms-v3d$' 'dtoverlay=vc4-kms-v3d,noaudio'
grep -qxF 'dtoverlay=disable-bt' "$CFG" || { echo 'dtoverlay=disable-bt' >> "$CFG"; REBOOT=1; }
systemctl disable --now hciuart bluetooth 2>/dev/null || true

log "WiFi sin ahorro de energía"
cat > /etc/NetworkManager/conf.d/necrotracks-wifi.conf << 'CONF'
[connection]
wifi.powersave = 2
CONF

log "Logs del sistema en RAM (durante el show no se escribe en la SD)"
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/necrotracks.conf << 'CONF'
[Journal]
Storage=volatile
RuntimeMaxUse=32M
CONF
systemctl restart systemd-journald

log "Código en $APP, datos en $DATA"
install -d -o "$NT_USER" -g "$NT_USER" "$APP" "$DATA" "$DATA/library" "$DATA/setlists" "$DATA/profiles"
if [ -d "$APP/.git" ]; then
  sudo -u "$NT_USER" git -C "$APP" pull -q --ff-only
else
  sudo -u "$NT_USER" git clone -q "$REPO_URL" "$APP"
fi

log "Entorno Python"
# --system-site-packages: python3-rtmidi y lgpio vienen de apt (no hay wheel aarch64 para py3.13)
[ -d "$APP/.venv" ] || sudo -u "$NT_USER" python3 -m venv --system-site-packages "$APP/.venv"
sudo -u "$NT_USER" "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u "$NT_USER" "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

log "iRig: volumen digital fijo en 0 dB"
if amixer -c IO sget 'USB Streaming' > /dev/null 2>&1; then
  amixer -q -c IO sset 'USB Streaming' 0dB unmute
  alsactl store
else
  echo "   iRig no conectado, se omite"
fi

log "Servicios"
NT_USER="$NT_USER" bash "$APP/bin/remote/units.sh"

if [ "$REBOOT" = 1 ]; then
  echo "==> Listo. Hace falta reiniciar para aplicar cambios de /boot: sudo reboot"
else
  echo "==> Listo."
fi
