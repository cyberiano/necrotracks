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
out1 (Pista) = FOH_L*0.5 + FOH_R*0.5   # 0.5: la suma nunca puede clipear
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
**Formato simple de la banda (el que usan hoy):** un solo WAV de 2 canales con **L = click,
R = pista mono**. El import lo reconoce y arma el render con pista en ch1/ch2 y click en ch3.
El perfil del iRig de la banda es `irig-click-pista`: **salida L = click, salida R = pista**
(el orden inverso al que yo había supuesto).
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

### MIDI del iRig — modo normal (2026-09-10)

Todo por **canal 1**, puerto `iRig Stomp IO MIDI 1`. El puerto `Control` no manda nada.

| Control | Mensaje | Comportamiento |
|---|---|---|
| Footswitch A | Program Change 0 | 1 mensaje por pisada, nada al soltar |
| Footswitch B | Program Change 1 | ídem |
| Footswitch C | Program Change 2 | ídem |
| Footswitch D | Program Change 3 | ídem |
| Pedal de expresión | CC 11 | continuo, 6..127, ~65 msg/s en movimiento |
| Toe switch (bajo el pedal) | CC 26 | alterna 127 / 0 en cada pisada |

Implicancias para el MIDI Learn:
- En modo normal no hay pisada larga (no hay mensaje al soltar).
- CC de tipo toggle (CC 26): **cualquier valor** cuenta como pisada.
- CC continuos (CC 11) se ignoran al aprender acciones de botón.
- La actividad MIDI no afectó el audio (soak en curso, `xruns=0`).

### MIDI del iRig — modo stomp (2026-09-10)

Mismo canal 1 y puerto `MIDI 1`. Cada footswitch es un interruptor: alterna 127 / 0.

| Control | Mensaje |
|---|---|
| Footswitch A | CC 20 (127 / 0) |
| Footswitch B | CC 21 (127 / 0) |
| Footswitch C | CC 22 (127 / 0) |
| Footswitch D | CC 23 (127 / 0) |
| ¿Cambio de modo? | CC 39 = 127 (llegó justo antes de las pisadas; sin confirmar) |

⚠️ **Rebote**: una pisada del D mandó `0 → 127 → 0` en 140 ms. **El MIDI Learn necesita
antirrebote** (ignorar repeticiones del mismo control dentro de ~200 ms) en los dos modos:
si no, una pisada de "Next" puede saltearse una canción en vivo.

Recomendación: usar el **modo normal** (Program Change). Un mensaje distinto por footswitch
y sin estado de prendido/apagado que confunda con los LEDs. El Learn soporta los dos igual.
- **Deuda: MIDI OUT sin probar.** Faltan cables. Hasta probarlo, se asume que lo que la Pi manda
  a `iRig Stomp IO MIDI 1` sale por el DIN OUT. Si no sale, plan B: cable USB-MIDI aparte.
- Estabilidad de 30 min con carga (Fase 1) — **interrumpida**, ver hallazgos abajo.

### Hallazgos de la prueba de estabilidad (2026-09-10)

- Sin carga: 5 min limpios, `xruns=0 starved=0`, load 0.1, 35 °C.
- **Escritura de 1 GB a la SD (fsync)**: ALSA sin xruns, pero el reader **no llegó a tiempo**:
  `starved=547` (~12 s de huecos en ~50 s). La escritura saturó la SD y frenó las lecturas
  por más de los 3 s del ring buffer. Al terminar la escritura se recuperó.
  → Propuesta: **precargar la canción entera en RAM, ya ruteada** (2 ch float32 ≈ 115 MB
  cada 5 min). Durante la reproducción no se toca la SD.
- **El vúmetro de salida del iRig marcó clip** (rojo al máximo) durante la prueba. La señal
  es de −40/−30 dBFS, así que no debería. Sin parlantes conectados. Causa sin confirmar. Hipótesis:
  1. Corrupción del stream S24_3LE bajo carga (un desalineo de 24-bit empaquetado = ruido a full scale)
  2. El driver interpreta mal el control de volumen del iRig (dmesg: *"Unlikely big volume range
     (=65534), cval->res is probably wrong"*), y el "0 dB" que fijamos es en realidad ganancia alta
  3. El vúmetro no está midiendo nuestro stream
- Formato negociado: S24_3LE, 48 kHz, 2 ch, period 2048, buffer 6144 (128 ms).

### Diagnóstico del clipping: **el que se cuelga es el iRig** (2026-09-10)

- Linux y el driver sanos: el puntero de ALSA avanza a ~48 000 frames/s, `aplay` termina en
  tiempo exacto, mixer en 0 dB sin mute. El vúmetro sí mide la salida USB (escalera de −40 a −3 dB:
  1 → 2 → 3 luces tras reenchufar).
- **El iRig se colgó 3 veces**, siempre con escritura fuerte a la SD. Una vez tocando el engine
  (que reportó 0 xruns) y dos con `aplay` (6–7 underruns). Síntoma en el kernel:
  `usb_set_interface failed (-32)` / `cannot set freq 48000 to ep 0x1`. Colgado, saca basura a
  full scale (rojo) o nada. **Solo vuelve desenchufándolo.**
- Un corte forzado de 1 s **sin carga**: 1 underrun, sin error USB, no se colgó.
- Hipótesis: con la Pi bajo carga fuerte (IRQ/CPU), una reconfiguración del stream o transferencias
  isócronas tardías hacen fallar al firmware del iRig.
- `aplay` (el reproductor estándar) también corta con la SD saturada: no es un problema de Python.

Reglas que salen de acá:
1. Durante la reproducción no hay I/O pesada (import bloqueado mientras suena).
2. El stream de audio se abre una vez y no se reconfigura nunca durante un show.
3. Buffer grande, hilo de audio con prioridad de tiempo real.
4. Detectar el cuelgue (errores del device / dmesg) y mostrar "Reconectar iRig" en OLED y web:
   la Pi no puede revivirlo sola.
5. Engine con buffer de 20 s bajo la misma carga (cable USB nuevo): los números dan limpio
   (`xruns=0 starved=0`, sin errores USB, load hasta 2.05), **pero el vúmetro fue a rojo**
   (todas las luces con un tono de −12 dB) → el iRig sacó basura igual.
   **Ni los contadores del engine ni dmesg detectan el cuelgue.** El único indicador confiable
   hoy es el vúmetro. El cable nuevo no lo resolvió.
6. **Descartes** (cada prueba con el iRig recién reenchufado y un tono fijo de −12 dB):
   - Alimentación: con **fuente externa** el iRig se cuelga igual.
   - Cable USB: se cuelga igual con cable nuevo.
   - Ethernet (comparte el hub USB interno con el iRig): **desconectado**, se cuelga igual.
   - Python: con `aplay` pasa lo mismo.
   - `throttled=0x0` desde el arranque: la Pi nunca detectó baja tensión.
   Patrón: con escritura fuerte a la SD el vúmetro sube a rojo y baja varias veces (basura), y
   al rato queda apagado. **No se recupera al cortar la carga**: solo desenchufando.
   Topología: iRig USB 1.1 (12M) detrás del hub interno USB 2.0, junto al lan78xx.
   Controlador `dwc_otg`, `fiq_fsm_mask=15`, `speed=-1`.
7. **Carga solo de CPU** (4 núcleos al 98 %, sin tocar la SD, 51–57 °C, sin throttling):
   `aplay` con **0 underruns** en ~5 min, pero el vúmetro terminó al máximo con rojo y el tono
   por parlante "un poco áspero". **No se recuperó al cortar la carga.** → No es solo la SD:
   **cualquier carga fuerte puede trabar al iRig, incluso sin cortes de audio.**
   (Duda menor: el parlante se conectó durante la prueba.)
   ⚠️ Trabado saca basura cerca de full scale: en un PA es peligroso. Hay que resolverlo, no evitarlo.
8. **Puerto USB**: el iRig pasado de `1-1.1.3` (hub compartido con Ethernet) a `1-1.2`
   (directo en el primer hub, el par de puertos lejos del Ethernet):
   - Carga de CPU 3,5 min (47–58 °C): **aguanta**. 0 underruns, 2 luces verdes, tono suave.
   - Escritura fuerte a la SD: **se traba igual** (ruido y después apagado; 13 underruns).
   → **Usar siempre el par de puertos lejos del Ethernet.** La SD sigue siendo el problema.
9. **SD sin DMA**: `sd_force_pio=on` llega al DT (`brcm,force-pio`) pero el driver lo ignora
   (`DMA enabled`). `sd_pio_limit=65535` sí se aplica (`DMA enabled (>65535)` = nunca DMA).
   Con escritura fuerte a ~16 MB/s en PIO (load 3.2, 9 underruns): **se traba exactamente igual.**
   → **DMA descartado.** Ajustes revertidos (costaban CPU sin beneficio).
   Nota: `dtparam` después de un `dtoverlay` se aplica a ese overlay → el bootstrap ahora usa
   `base_param()` (antes, `i2c_arm_baudrate` nunca se aplicaba).
10. **Próximo (a propuesta de Cristian)**: soak test realista con **WAV reales**: el engine toca
    un set entero seguido, stream siempre abierto, sin carga artificial. Si aguanta, se avanza con
    reglas (cero I/O pesada durante el show). Si no: `dwc_otg.speed=1` (lado USB: todo en full
    speed, sin transaction translator).
    Si no alcanza, dos caminos:
    - **Raspberry Pi 4**: controlador USB xHCI, sin los problemas de `dwc_otg`. El código corre igual.
    - **Seguir con la 3B+ con reglas**: cero I/O pesada durante el show + soak test realista de
      60 min (engine con canciones reales, web abierta). Riesgo: si igual se traba en vivo, saca
      basura al PA y nada lo detecta.
