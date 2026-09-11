# Hotspot WiFi de Necrotracks. Corre en la Pi como root (lo invoca setup.sh).
# Red "Necrotracks", 5 GHz canal 36 (sin DFS), la Pi en 192.168.4.1. No se levanta sola: la prende
# necrotracks-hotspot.service si al arrancar no hay una WiFi conocida (ver hotspot-fallback.sh).
# La contraseña NO va al repo (es público): se pasa como HOTSPOT_PSK la primera vez y queda guardada
# en NetworkManager (/etc/NetworkManager/system-connections, solo root). Sin HOTSPOT_PSK, conserva la que hay.
set -euo pipefail
CON=necrotracks-hotspot
if ! nmcli -t -f NAME con show | grep -qx "$CON"; then
  if [ -z "${HOTSPOT_PSK:-}" ]; then
    echo "   sin HOTSPOT_PSK: no se crea el hotspot (bin/bootstrap.sh la pasa si está en el entorno)"
    exit 0
  fi
  nmcli con add type wifi ifname wlan0 con-name "$CON" ssid Necrotracks autoconnect no > /dev/null
fi
# brcmfmac (el WiFi de la Pi) en modo AP: WPA2 con CCMP explícito y sin PMF, si no el iPhone no conecta.
args=(802-11-wireless.mode ap 802-11-wireless.band a 802-11-wireless.channel 36
      ipv4.method shared ipv4.addresses 192.168.4.1/24 ipv6.method disabled
      wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp
      wifi-sec.pmf disable connection.autoconnect no)
[ -n "${HOTSPOT_PSK:-}" ] && args+=(wifi-sec.psk "$HOTSPOT_PSK")
nmcli con modify "$CON" "${args[@]}"

# Con el hotspot, necrotracks.local resuelve también por DNS (además de mDNS). No se redirige todo
# dominio a la Pi: el iPhone lo toma como portal cautivo y abre una ventanita cada vez que se conecta.
install -d /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/necrotracks.conf << 'CONF'
address=/necrotracks.local/192.168.4.1
CONF
echo "   hotspot: $CON (Necrotracks, 5 GHz canal 36, 192.168.4.1)"
