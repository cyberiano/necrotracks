# Necrotracks — Plan de desarrollo

Documento vivo. Decisiones tomadas y orden de trabajo.
Complementa a `Reproductor multipista para vivo Necrotracks.md` (el *qué*); esto es el *cómo*.

---

## Decisiones tomadas

| Tema | Decisión | Motivo |
|---|---|---|
| OS | Raspberry Pi OS **Lite 64-bit** | Wheels arm64 disponibles; en armhf hay que compilar desde fuente |
| Audio | **ALSA directo**, device en exclusiva | No es una app de baja latencia. Sin JACK/PipeWire no hay servidor que se caiga |
| Lenguaje | **Python** en todo | Trabajo por bloque es trivial; sin build step en la Pi |
| Sincronía | **Render multicanal pre-generado** por canción | La sincronía pasa a ser propiedad del archivo, no del software |
| Procesos | **Dos**: `engine` + `web` | Única división que compra estabilidad real |
| Persistencia | **JSON en disco** | 12 canciones. SQLite no aporta nada y resta legibilidad |
| Frontend | **Sin build step** — HTML + JS vanilla + libs vendorizadas | Node en una 3B+ es dolor. Editar = guardar + F5 |
| Sample rate | **48 kHz fijo**, todo se convierte al importar | Evita resampling y drift |
| Pantalla | **OLED SH1106 128×64 I2C** + encoder rotativo | Liviano, y permite operar sin celular |
| LEDs | Comunes o 1 RGB. **Si WS2812B → método SPI, nunca PWM** | PWM entra en conflicto con el subsistema de audio |

### Fuera del MVP
- Sync con Google Drive (import por web ZIP + pendrive alcanza)
- Marcadores, BPM map, loops de sección, entrar a mitad de canción
- Editor de automatizaciones MIDI en la UI (los `.mid` se exportan del DAW)

---

## Ruteo de audio

Layout canónico del render, **siempre 4 canales** (los que falten van en silencio):

```
ch1 = FOH L    ch2 = FOH R    ch3 = Click    ch4 = Guía
```

El perfil de hardware es una **matriz de ganancias** `salidas × 4`. Cambiar de hardware
es cambiar la matriz — nunca re-exportar canciones.

**Perfil MVP — iRig Stomp I/O (2 salidas):**

```
out1 (Pista) = FOH_L*0.7 + FOH_R*0.7
out2 (Click) = Click*1.0
```

⚠️ La suma L+R a mono puede cancelar por fase si la mezcla tiene contenido fuera de fase.
El import debe avisar si sumar pierde más de ~3 dB respecto del pico estéreo,
y el perfil debe permitir elegir `sum` / `solo L` / `solo R`.

**Perfil futuro — interfaz 4 salidas:** identidad (cada canal a su salida). Ya queda escrito.

---

## Estructura en disco

```
/opt/necrotracks/              # código — git
  bin/{bootstrap,deploy,backup-sd}.sh
  engine/  web/  systemd/

/var/lib/necrotracks/          # datos — NUNCA en git
  library/<slug>/
    song.json                  # nombre, duración, canales presentes, hash de stems
    stems/                     # originales, no se tocan en el show
    render.wav                 # 4ch / 48k / 24-bit — generado
    automation.mid             # opcional
  setlists/<slug>.json
  profiles/<slug>.json
  config.json                  # perfil activo, mapeos MIDI learn, red
```

Escritura atómica (write temp + rename). Backup = `cp -r`.

Espacio estimado: ~2 GB de renders + stems para 60 min de repertorio.
Throughput requerido: 576 KB/s. Una SD clase 10 hace 10 MB/s. Sobra.

---

## Engine — hilos

- **Reader** — lee bloques de `render.wav` a un ring buffer de ~3 s
- **Audio** — `sd.OutputStream` con escritura bloqueante, `blocksize=1024`, aplica la matriz (`numpy`) y escribe
- **MIDI sched** — lee el playhead en samples y emite eventos vencidos (precisión ±20 ms, sobra para luces/patches)
- **Main (asyncio)** — socket unix, GPIO, encoder, refresh del OLED a ~5 Hz

Estados: `IDLE` → `ARMED` → `PLAYING` ⇄ `PAUSED` → `WAITING` → …

Comportamiento por canción en la setlist: `stop` | `arm_next` | `auto_next` | `wait(n)` | `repeat`.
El **bloque es solo UI**: agrupa y aplica un default al crearse; la verdad vive en cada canción.

Fades de 5–10 ms en todo start/stop para no hacer "plop" en el PA.

---

## Fases

### Fase 0 — Provisionar y validar hardware
Bootstrap por SSH + tests del iRig. **Puede cambiar decisiones**, por eso va primero.

Tests:
1. ~~Canales de salida reales~~ → **2 canales**, el auricular es espejo. Opción B confirmada.
2. ~~Sample rates nativos~~ → 48k/24-bit nativo, abre OK.
3. Footswitches y pedal de expresión como MIDI (requiere que Cristian los pise mientras se captura)
4. **MIDI OUT**: ¿la Pi puede mandar MIDI al DIN OUT del iRig? Si no, hace falta un cable USB-MIDI aparte
5. Estabilidad: 30 min reproduciendo con subida de archivos en paralelo, contando xruns

### Fase 1 — Engine mínimo tocando
Un `render.wav` de 4ch saliendo por el perfil iRig. Play/stop desde CLI. **Hito que valida todo el stack.**

### Fase 2 — Biblioteca, import y setlists
Carpeta o ZIP por canción → `render.wav`. Convención: nombre de carpeta = nombre de canción,
archivos `foh_L.wav`, `foh_R.wav`, `click.wav`, `guia.wav` (los que existan).
También acepta un `foh.wav` estéreo. Documentado en la UI.
Setlists y bloques en JSON. Todo operable por CLI antes de que exista la web.

### Fase 3 — Web UI
Sin build step. Config (biblioteca, setlists, perfiles, import) + Show Mode + estado por WebSocket.
Degrada bien: si se corta el WS, muestra "desconectado" y no bloquea nada.

### Fase 4 — Controles físicos
MIDI in + **MIDI Learn** (footswitches del iRig → Play/Stop/Next/Prev).
El learn debe ignorar CC continuos para que el pedal de expresión no se mapee solo.
3 botones GPIO: **apagado seguro** (2 s), **hotspot on/off**, **pánico**.
Encoder rotativo + OLED: setlist activa, canción actual, próxima, tiempo, estado, IP/SSID.

### Fase 5 — Red y robustez
Hotspot es la **única red en ensayo y show** → el toggle es crítico, no cómodo.
NetworkManager AP + dnsmasq con `address=/#/192.168.4.1` (así cualquier dominio cae en la Pi;
mDNS `necrotracks.local` como conveniencia, no como única vía).

**Corolario importante:** si el hotspot falla en el venue, no hay web. Por eso OLED + encoder
tienen que poder correr un show completo solos: elegir setlist, ver actual/próxima, play/stop.

Además: chequeo pre-show, pánico con all-notes-off, autostart, watchdog de systemd, backup de SD.

### Fase 6 — Automatización MIDI
Reproducción de `.mid` sincronizada al playhead. Se exporta del DAW en el mismo timeline.

---

## Chequeo pre-show

Un botón que verifica y lista en verde/rojo:
- Todos los `render.wav` de la setlist existen y abren
- Sample rate y cantidad de canales correctos
- El perfil activo tiene ≤ salidas que el device presenta
- El device de audio esperado está conectado
- El puerto MIDI out existe (si la setlist usa MIDI)
- Los `.mid` parsean
- Espacio libre en disco

Vale más que la mitad de las features de la lista.

---

## Deploy

- `bin/bootstrap.sh` — una vez. apt deps, venv, units de systemd, i2c/spi, hotspot,
  tuning (governor performance, wifi powersave off, audio onboard y bluetooth deshabilitados)
- `bin/deploy.sh` — desde la Mac: `git pull` + `pip install` + `systemctl restart`. Flag `--fast` = solo restart
- `bin/backup-sd.sh` — imagen de la SD desde la Mac

Los WAV **nunca** van al repo.

---

## Riesgos vigentes

| Riesgo | Mitigación |
|---|---|
| USB y Ethernet comparten bus USB 2.0 en la 3B+ | Nada más que audio en el bus durante el show. Prohibir sync/import mientras se reproduce |
| Corrupción de SD por corte de energía | Botón de apagado + imagen de respaldo. Evaluar overlayfs más adelante |
| WiFi 2.4 GHz onboard en venue lleno | Los controles físicos son el camino primario; la web es conveniencia |
| El iRig puede no rutear MIDI USB → DIN OUT | Se resuelve en Fase 0. Plan B: cable USB-MIDI |
| Solo 2 salidas en el MVP (confirmado) | Perfil mono+click. La matriz ya deja listo el perfil de 4 salidas |

---

## Resultados Fase 0 (2026-09-10)

- **OS**: Raspberry Pi OS Trixie 64-bit, kernel 6.18 PREEMPT. 905 MiB RAM, SD de 29 GB.
- **iRig Stomp IO**: USB full-speed. Playback **2 canales** (auricular = espejo del main).
  S16_LE / S24_3LE, 32–96 kHz. 48k/24-bit abre sin problemas.
- **Mixer ALSA** `USB Streaming`: el bootstrap lo fija en 0 dB y lo persiste.
- **MIDI**: dos puertos, `iRig Stomp IO MIDI 1` y `iRig Stomp IO Control`.
- **Python 3.13**: wheels aarch64 OK para numpy 2.5, soundfile 0.14, sounddevice, mido,
  fastapi, uvicorn, luma.oled, gpiozero. `python-rtmidi` no tiene wheel → apt `python3-rtmidi`
  con venv `--system-site-packages`. `lgpio` viene del sistema.
- **Repo público** → la Pi clona por HTTPS, sin deploy key.

Pendiente:
- Qué manda cada footswitch y el pedal de expresión, y en qué puerto (necesita a Cristian pisando)
- Si el MIDI que manda la Pi sale por el DIN OUT del iRig
- Estabilidad de 30 min con carga (Fase 1)
