# Al arrancar la Pi: si en ~45 s el WiFi no se conectó a una red conocida, levanta el hotspot "Necrotracks".
# En casa gana la WiFi de siempre (SSH, deploy); en el ensayo o el show no hay red conocida y queda el
# hotspot. Lo corre necrotracks-hotspot.service como root.
set -u
CON=necrotracks-hotspot
if ! nmcli -t -f NAME con show | grep -qx "$CON"; then
  echo "No hay hotspot configurado (bin/remote/hotspot.sh)"
  exit 0
fi
for _ in $(seq 45); do
  state=$(nmcli -g GENERAL.STATE dev show wlan0 2>/dev/null)
  case "$state" in
    100*) echo "WiFi conectada ($(nmcli -g GENERAL.CONNECTION dev show wlan0)): sin hotspot"; exit 0 ;;
  esac
  sleep 1
done
echo "Sin WiFi conocida: levanto el hotspot Necrotracks"
nmcli con up "$CON"
