# Necrotracks — Plan de desarrollo

Documento vivo. Decisiones tomadas y orden de trabajo.
Complementa a `Reproductor multipista para vivo Necrotracks.md` (el *qué*); esto es el *cómo*.

---

## Estado actual y próximos pasos (cierre de sesión 2026-09-11, 18:10)

**Hardware: la Pi 3B+ + iRig Stomp I/O es viable con `dwc_otg.speed=1`** (34 + 63 min limpios). Sin ese
ajuste el iRig se silencia solo a los ~25 min. Detalle en "Diagnóstico del clipping" más abajo.

**En la Pi hoy** (`necrotracks@192.168.1.32` por la WiFi Akasha; el Ethernet está desenchufado):
- `main` desplegado (código `47157f1`): `necrotracks-engine` (dueño del iRig en `1-1.3`, prioridad RT, MIDI
  y video), `necrotracks-web` (puerto 80) y `necrotracks-hotspot` (hotspot si no hay WiFi conocida).
- `cmdline.txt`: `dwc_otg.speed=1`, `console=tty3 quiet splash`, sin cursor. `config.txt`: audio integrado
  prendido (jack de respaldo), `vc4-kms-v3d,noaudio,cma-384`, `disable_splash=1`. Plymouth con el tema
  `necrotracks`; `getty@tty1` deshabilitado; logind con `RemoveIPC=no`. mpv 0.40 y ffmpeg 7.1 por apt.
- Biblioteca: "Sands of time", "I will not spoil" y "There is a Place" (con video 720p). Set lists `prueba`,
  `soak` y `video-prueba`. `video-logo.png` (logo blanco de Necrópolis) está en los datos, no en el repo.
  En `data/reposo/` quedó la imagen que subió Cristian.
- `config.json`: `hdmi_mode` automático (sale en 1920x1080), `video_fit` en el default y `idle_fit` al 50 %
  subido (Cristian recalibró con la imagen del 30 aniversario de Necrópolis como pantalla de reposo).
- `/var/lib/necrotracks/video-prueba/`: videos de prueba y una copia del MP4 original. Se pueden borrar.
- Monitor de prueba: un Samsung detrás de un adaptador HDMI-VGA (EDID "TS35505", pide 1024x768).

**Hecho y probado en la Pi (2026-09-11)**, detalle en cada fase:
- Engine en servicio, import, set lists por `ctl`; el iRig desenchufado y reenchufado sonando vuelve solo.
- Soak de 63 min con el engine en servicio: audio hasta el final, sin errores USB.
- Web (Fase 3) con la identidad de Necrotracks, instalable en el iPhone. Import desde el celular.
- Footswitches del iRig y MIDI Learn (Fase 4, parte).
- Hotspot "Necrotracks" probado con el iPhone y la app instalada.
- Jack de la Pi como salida de respaldo (abre y reproduce; falta escucharlo).
- Video por HDMI: MP4 con audio (click/pista) en la biblioteca, mpv por hardware sincronizado al audio (≤0,1 s),
  encaje y patrón de ajuste, pantalla de reposo configurable (imagen probada), resolución automática que se
  corrige sola, arranque con el logo de Necrotracks y la tty1 en negro. Ver "Visuales por HDMI".
- Bugs arreglados: `RemoveIPC` borraba el socket y la bandera "sonando"; play + pausa fantasma (escuchadores
  acumulados en la web y una pausa que se perdía en el Player); CMA agotada (video congelado).

**Detalles del video, arreglados (2026-09-11, `8501ad0`)**. Ver "Fundidos" en "Visuales por HDMI".
- Fundidos de 0,5 s en cada cambio de pantalla, con el archivo cambiado bajo negro. Eso tapa **el reposo que
  se agrandaba** al arrancar un video. Cristian lo vio: Play y Stop limpios.
- **Stop sin demora**: el engine despierta al hilo de video y el aplanado del reposo tiene caché.
- **El "CLS" al terminar un video**: pasa por el mismo camino (al reposo con fundido), pero falta verlo con un
  video que termine solo. Si sigue: `fbcon=map:` para sacar la consola del HDMI.

**Pruebas físicas pendientes** (con Cristian, de a una):
- Un video que termine solo (sin Stop): que no aparezca el "CLS".
- Un video como pantalla de reposo.
- Reiniciar y ver que mpv se corrige solo al modo HDMI (en el reinicio anterior arrancó en 1024x768).
- **Video "lavado"**: comparar el negro del logo o del patrón con el del video. Si todo se ve gris, es el rango
  de la salida HDMI ("Broadcast RGB" limitado); si solo el video, el rango YUV de la capa de video. `modetest`
  (paquete `libdrm-tests`) muestra las propiedades.
- Escuchar el jack de respaldo.
- Prueba larga (30 min) con video y el iRig sonando, mirando el vúmetro. Si traba → Raspberry Pi 4.
- El hotspot levantándose solo sin WiFi conocida (en la sala de ensayo).

**Próximos pasos, en orden:**
1. ~~Arreglar los detalles del video~~ (hecho).
2. Las pruebas físicas pendientes.
3. **Usarla en un ensayo** (el primer uso real), manejándola desde la web con el hotspot.
4. **Fase 4, lo que falta**: LEDs de los footswitches como indicador de estado, OLED SH1106 + encoder,
   botones GPIO (Cristian todavía no tiene el hardware).
5. Fase 5 (chequeo pre-show en la UI, pánico, apagado seguro) y Fase 6 (MIDI de automatización).

**Pendientes y deudas:**
- MIDI OUT hacia el DIN del iRig: sin probar (faltan cables).
- LEDs de los footswitches: CC 20–23 = 0 apaga; falta encontrar qué valores dan verde y rojo.
- Pisada larga del footswitch 4 (tap tempo): sin medir.
- Probar la StudioLive 16R como perfil multipista.
- `necrotracks.local` todavía resuelve también la IP vieja del Ethernet: los scripts usan
  `NECROTRACKS_HOST` con la IP del WiFi hasta que se acomode.
- El import con el stream abierto se probó parado, un solo archivo. Sigue bloqueado mientras suena.
- `bin/deploy.sh` reinicia todos los servicios (el engine también): nunca mientras suena. Para cambios solo de
  la web alcanza `git pull` + `systemctl restart necrotracks-web`.

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

**Estado (2026-09-10): hecho en local, 29 tests, falta probar en la Pi.**
- Import: carpeta, ZIP o archivo suelto (WAV/FLAC/AIFF/MP3). Conversión a 48 kHz con `soxr`. Render de
  4 canales. Reemplazo atómico (si falla, la versión anterior queda intacta). Avisos de fase al sumar a
  mono y de stems con duraciones distintas.
- Un archivo estéreo suelto **exige** decir el formato (`click-pista` o `foh`): adivinar mal manda el click al PA.
- **Import bloqueado mientras suena** (bandera en RAM, `/dev/shm`) y escritura con **tope de 3 MB/s** con
  fsync por bloque. Riesgo residual: el stream queda abierto aun parado; si un import trabara al iRig,
  habría que reenchufarlo antes de tocar. A validar en la Pi.
- Set lists: bloques como etiqueta con comportamiento por defecto editable canción por canción; `stop`,
  `arm_next` (default), `auto_next`, `wait` (cancelable con Stop, adelantable con Play) y `repeat`;
  mover y chequeo pre-show.
- Máquina de estados del show (`engine/show.py`): play, pausa, stop, siguiente, anterior, ir a. Un solo
  hilo; el fin de canción llega del hilo de audio como evento.
- Pausa y reanudar en el Player (con fades).
- **Decidido con Cristian**: Siguiente/Anterior/Ir a **solo con la reproducción parada** (o durante una
  espera, que se cancela). Sonando o en pausa no hacen nada: solo Stop corta una canción.

### Fase 3 — Web UI
Sin build step. Config (biblioteca, setlists, perfiles, import) + Show Mode + estado por WebSocket.
Degrada bien: si se corta el WS, muestra "desconectado" y no bloquea nada.

**Estado (2026-09-11): hecha y probada en la Pi.** `web/` (FastAPI, `necrotracks-web.service`, puerto 80,
`Nice=10` e I/O `idle`). Vanilla JS, sin libs: `web/static/{index.html,app.js,style.css}`.
- **SSE en vez de WebSocket**: el estado va en una sola dirección, `EventSource` reconecta solo y no
  hace falta instalar soporte de WS en uvicorn. Comandos por `POST /api/cmd` al socket del engine.
  Ping cada 10 s: 25 s sin nada = cartel "Sin conexión". Engine caído = cartel "¿iRig enchufado?".
- **Show**: set list, bloque, canción N/total, tiempo y restante, qué hace al terminar, próxima,
  cuenta regresiva de la espera, transporte grande y lista (tocar una = ir a, solo parado). Teclado.
- **Set lists**: crear, renombrar (el slug no cambia), agregar, reordenar, bloque por fila o por rango,
  comportamiento, chequeo pre-show, cargar/recargar en el engine. Avisa cambios sin guardar.
- **Biblioteca**: import de archivos, ZIP o carpeta. El navegador sube de a un archivo (cuerpo crudo,
  sin multipart) a `/var/lib/necrotracks/incoming` con el tope de 3 MB/s, después importa. No en
  `/tmp`: es RAM (450 MB). Borrar canciones solo si no están en ninguna set list.
- Nada se sube, importa ni borra mientras suena, y **si arranca la reproducción a mitad de un import,
  se corta** (`store.Throttle(guard=True)`); la versión anterior de la canción queda intacta.
- **Identidad (2026-09-11)**: emblema de Necrópolis redibujado en vector y wordmark ΠΣCRΘTRΛCKS
  (`docs/assets/logo/necrotracks.svg`; símbolos inline en `index.html`). Negro, hueso y rojo sangre;
  DIN Condensed (viene con iOS/macOS, sin descargas: en el venue no hay internet). Íconos SVG propios.
  Menú fijo igual en todas las pantallas; en el celular, pestañas abajo y tablas en bloques.
- **Instalable en el iPhone** (Compartir → Agregar a inicio): `manifest.webmanifest`, `icon-180.png` y
  modo standalone. Los PNG salen de `web/static/icons/icon.svg` con `tools/icons.sh` (solo en la Mac).
- **Ajustes → Sistema** (2026-09-11): temperatura, lugar libre, aviso si la Pi recortó por tensión o calor
  (`vcgencmd get_throttled`) y botones para **apagar y reiniciar** la Pi (`sudo systemctl poweroff|reboot`,
  nunca mientras suena). Apagar bien importa: cortar la corriente de golpe puede arruinar la SD.
- **La set list editada se recarga sola** (2026-09-11): el engine trabaja con su copia en memoria, así que
  guardar en el editor no cambiaba el show. Se notaba feo: el show seguía con la lista vieja, con Siguiente y
  Anterior apagados y sin las canciones nuevas (le pasó a Cristian). Ahora, al guardar, si es la set list
  cargada y está parado, la web la recarga sola.
- **Casillero propio y caché** (2026-09-11): la regla general de los campos les saca la apariencia nativa
  (`appearance: none`, para el estilo de la app) y un `<input type=checkbox>` quedaba **invisible tildado o
  sin tildar** —lo mismo que ya había pasado con el deslizante—. Va uno propio: cuadrado rojo con tilde blanca.
  Y `index.html` pide `style.css` y `app.js` con **`?v=N`**: si no, el navegador (sobre todo el iPhone con la
  app instalada) se queda con la versión vieja. **Subir el número al tocar cualquiera de los dos.**
- **Sin zoom y con botón de recargar** (2026-09-11, pedido de Cristian): el zoom se bloquea (`maximum-scale=1`,
  `user-scalable=no`, `touch-action: manipulation` y los gestos cortados a mano para Safari), porque un pellizco
  sin querer en el escenario deja la pantalla corrida. Y **instalada en el inicio del iPhone no hay forma de
  recargar** (no hay barra ni tirar hacia abajo): va un botón en la barra, más un refresco automático al volver
  a la app (por ejemplo, después de subir una canción desde la Mac).
- **Set list imprimible** (2026-09-11, pedido de Cristian): botón Imprimir en el editor → `#imprimir/SLUG`, con dos
  hojas: **"para el piso"** (número y nombre lo más grande que entre —el tamaño baja según cuántas canciones
  haya—, con los bloques marcados) y **"técnica"** (duración, bloque, qué hace al terminar y total). **El PDF lo
  hace el navegador** (Imprimir → Guardar como PDF): nada de generarlo en la Pi, que sería una dependencia pesada
  para algo que el navegador ya hace. La hoja se dibuja en pantalla igual que sale en papel (blanca y negra) y
  `@media print` solo saca lo que la rodea. Abajo, la lista **en texto para mandar por mensaje**.
  Ojo: **la web va por HTTP**, así que no hay `navigator.share` ni portapapeles moderno (piden HTTPS): el texto
  va en un cuadro seleccionable y el botón Copiar cae a `execCommand`.
- Falta: elegir el perfil de hardware (hoy solo se ve cuál está activo en `/api/info`).

### Fase 4 — Controles físicos
MIDI in + **MIDI Learn** (footswitches del iRig → Play/Stop/Next/Prev).
El learn debe ignorar CC continuos para que el pedal de expresión no se mapee solo.
3 botones GPIO: **apagado seguro** (2 s), **hotspot on/off**, **pánico**.
Encoder rotativo + OLED: setlist activa, canción actual, próxima, tiempo, estado, IP/SSID.

**Estado (2026-09-11): footswitches hechos y probados en la Pi.** `engine/controls.py`, dentro del engine
(hilo de MIDI → `show.send`): anda aunque se caiga la web.
- Mapa por defecto del iRig, canal 1, en los dos modos: FS1 = Anterior (PC 0 / CC 20), FS2 = Siguiente
  (PC 1 / CC 21), FS3 = Stop (PC 2 / CC 22), FS4 = Play/Pausa (PC 3 / CC 23). Se guarda en `config.json`.
- MIDI Learn en la web (pestaña Controles): la pisada que se aprende no dispara la acción; un control =
  una acción, una acción puede tener varios controles. Muestra la última pisada recibida.
- Antirrebote de 200 ms por control. Un CC que manda valores distintos de 0/127 es continuo (pedal de
  expresión) y nunca cuenta como pisada.
- El puerto se busca por nombre (`iRig Stomp IO MIDI 1`); si no está, reintenta cada 5 s.
- Falta: LEDs de los footswitches (valores de verde/rojo sin encontrar), OLED + encoder y botones GPIO.

### Fase 5 — Red y robustez
Hotspot es la **única red en ensayo y show** → el toggle es crítico, no cómodo.
NetworkManager AP + dnsmasq con `address=/#/192.168.4.1` (así cualquier dominio cae en la Pi;
mDNS `necrotracks.local` como conveniencia, no como única vía).

**Corolario importante:** si el hotspot falla en el venue, no hay web. Por eso OLED + encoder
tienen que poder correr un show completo solos: elegir setlist, ver actual/próxima, play/stop.

Además: chequeo pre-show, pánico con all-notes-off, autostart, watchdog de systemd, backup de SD.

**Configurador de WiFi (2026-09-11, pedido de Cristian)**: Ajustes → Red WiFi, por `nmcli` (`sudo -n`, que el
usuario tiene sin password). Muestra la red conectada y la IP, lista las redes que ve (sin repetir la misma
antena, las ocultas no se listan), conecta con contraseña y olvida las guardadas. **El hotspot
`necrotracks-hotspot` no se puede borrar desde la web**: es lo único que queda si en el escenario no hay red
conocida. Nada de esto mientras suena. Ojo al probar: cambiar de red **corta la web y el SSH**; si la
contraseña está mal, la Pi queda sin red hasta reiniciarla (ahí vuelve el hotspot).
**Lo que costó encontrar: sin `sudo`, `nmcli` no escanea** — contesta lo que tiene en la caché, que era solo la
red conectada (medido en la Pi: sin sudo, 1 red; con `sudo` y `--rescan yes`, 10). La lista va con `sudo` y
`--rescan auto`, y el botón "Buscar redes" fuerza `yes`, que tarda **~8 s**: por eso avisa mientras busca.

### Visuales por HDMI (pedido 2026-09-11)
Canciones como MP4 (audio L = click, R = pista) con el video por HDMI a un proyector. **Formato: H.264
hasta 1080p30, hasta ~8 Mbps** (720p si quieren archivos más livianos; nada de 60 fps ni H.265). El import
extrae el audio con ffmpeg (mismo render de siempre) y guarda el video sin recodificar. En el show, mpv sin
audio, sincronizado a la posición del engine; si se cae, el audio sigue. **Sin video sonando: el logo blanco
de Necrópolis sobre negro** (`docs/assets/logo/logo-white.png`).

**Prototipo en la Pi (2026-09-11)**, `tools/video_proto.py`, sin el engine sonando. mpv 0.40 y ffmpeg 7.1
por apt (`--no-install-recommends`; todavía no están en el bootstrap):
- **`--vo=gpu --gpu-context=drm --hwdec=v4l2m2m`**: decodifica por hardware y muestra los cuadros por una
  capa de la pantalla (`drmprime-overlay`), sin copias por la CPU. **720p: 14 % de CPU, 1080p: 16 %**
  (de 400 %), 0 cuadros perdidos, 36–39 °C. Es la única que sirve.
- `--hwdec=v4l2m2m-copy --vo=drm`: 270 % de CPU y pierde ~6 cuadros/s. Software: 334 % y ~10 cuadros/s.
- Logo por HDMI con `mpv --vo=drm --image-display-duration=…`: anda.
- Extraer el audio de un MP4 de 2 min: 2,7 s.
- Monitor de prueba conectado: máximo 1024×768 (el overlay escala por hardware).
**Integrado (2026-09-11)**: `engine/video.py` (un mpv siempre abierto, nice +10 respecto del engine, por
su socket JSON; si se cae, se levanta de nuevo) e import de MP4/MOV en `library.py`. Probado con un video
real de la banda ("There is a Place", H.264 720p30, 7,4 Mbps, AAC 44,1 kHz, 4:30, 255 MB):
- Import por CLI: 169 s (casi todo es copiar el video a la SD con el tope de 3 MB/s).
- Sonando (por el jack, en silencio): video por hardware, mpv ~17 % de CPU, load 0,9. Stop → logo.
- El logo sale de `/var/lib/necrotracks/video-logo.png` (datos, no repo), achicado (`video-zoom=-0.9`).
  mpv 0.40 dibuja un **damero** detrás de lo transparente por defecto: va `--background=color` y, además,
  el engine lo aplana sobre negro con ffmpeg al arrancar (`/dev/shm/necrotracks/video-logo-plano.png`).
  Captura del HDMI: esquina negra opaca (0,0,0,255). Ojo: `screenshot-to-file … window` estira la imagen
  a la pantalla e ignora zoom y proporción; la geometría real está en la propiedad `osd-dimensions`
  (con el monitor de 1024×768: márgenes de ~237 px, el logo a 548×295 centrado, proporción respetada).
- Sincronía: con tolerancia de 40 ms la velocidad iba y venía (1,05/0,95): un cuadro son 33 ms. Queda
  en 80 ms para corregir y 30 ms para soltar, con ganancia 0,5 y ±3 % de velocidad. Estable: el video va
  ~110 ms detrás de la posición del engine, que es la latencia de la salida (lo que tiene que compensar).
  mpv tarda ~330 ms en arrancar un video: carga con ese adelanto (`LOAD_LEAD`).
- **Pantalla negra con audio** (primera prueba con Cristian mirando): con las capas por defecto de mpv el
  video queda tapado. En la Pi va `--drm-draw-plane=overlay --drm-drmprime-video-plane=primary` (el video
  en la primaria, lo que dibuja mpv arriba), y con un video cargado el fondo de arriba es transparente
  (`background-color=#00000000`, solo para ese archivo). Así se ve (confirmado por Cristian, ~11–20 % CPU).
- **Video congelado en el primer cuadro**: con la CMA de 256 MB quedaban ~60 MB para mpv y, después de un
  video, 1–5 MB libres: el decodificador no conseguía buffers. El bootstrap sube a **`cma-384`**: con el logo
  quedan ~260 MB libres. Mientras suena, la CMA libre baja (la usa la caché de archivos) y vuelve sola
  (medido: bajó a 21 MB y volvió a 139 MB). Audio y video sincronizados: ≤0,1 s en 214 s de canción.
- **Play + pausa fantasma** (dos bugs, arreglados): la web sumaba escuchadores a `#view` en cada vista, y
  después de ir y volver un toque mandaba `play_pause` dos veces; y el Player perdía una pausa que llegaba
  mientras se llenaba el buffer (el show quedaba "en pausa" con el audio sonando). El transporte de la web
  además ignora un segundo toque del mismo botón dentro de 300 ms.
- **Proporción estirada**: el monitor de Cristian está detrás de un adaptador (EDID "TS35505", 30×23 cm)
  que pide 1024x768; la Pi le hacía caso y el monitor, ancho, lo estiraba. Resolución automática: la que
  pide la pantalla, salvo 4:3/5:4 con un 16:9 disponible (tope 1080p, sin entrelazado); se elige a mano en
  Ajustes → Pantalla HDMI y cambiarla reinicia solo mpv (no el engine ni el iRig). **No usar
  `--drm-draw-surface-size`**: con la capa de mpv más chica que la pantalla, el video queda tapado.
  Pendiente: que Cristian confirme el video en 1920x1080 con la proporción bien.
- **Ancho y alto por separado** (2026-09-11, pedido de Cristian): la escala única pasó a ser dos, ancho y alto
  (50–120 %), con `video-scale-x` / `video-scale-y` de mpv, para compensar pantallas de escenario que deforman
  (la misma pantalla estira a lo ancho o a lo alto). En la web se mueven juntas salvo que se destilde "Mover
  ancho y alto juntos". El `scale` de antes se sigue leyendo (se copia a los dos ejes). **Ojo: mpv ignora
  escala y posición con `keepaspect=no`**, o sea en modo Estirar.
- **Encaje ajustable** (Ajustes → Pantalla HDMI): Ajustar / Llenar / Estirar, escala 50–120 % y posición
  ±50 % (con ±20 % no alcanzaba para ubicar libremente), en vivo sobre mpv y guardado en `config.json`
  (`video_fit`). Patrón de ajuste 16:9 (borde blanco, zona segura del 5 % en rojo) para compensar el
  overscan: Cristian lo calibró en su monitor (vertical −2,5 %).
- **Pantalla de reposo** (Ajustes → Pantalla en reposo): lo que se ve cuando no suena un video (parado o una
  canción sin video). Por defecto el logo de Necrópolis (`video-logo.png`, al 54 %); se reemplaza subiendo una
  imagen (PNG/JPG/WebP, se aplana sobre negro) o un video (MP4, en loop y sin audio) a `data/reposo/`, con su
  propio encaje (`idle_fit`). Probado con imagen; falta probar un video de reposo.
- **Modo HDMI que se corrige solo**: después de un reinicio mpv tomó la pantalla antes de que el adaptador diera
  todos sus modos y quedó en 1024x768 con 1920x1080 elegido. Parado, cada 5 s compara el modo que corresponde con
  el de mpv y, si no coinciden, reinicia solo mpv (también sirve si enchufan un proyector con la Pi prendida).
- **Arranque**: Plymouth con el logo de Necrotracks (`bin/remote/splash/`), sin arcoíris (`disable_splash=1`);
  la tty1 queda negra (kernel en `console=tty3`, `quiet splash`, sin cursor ni login; login en Ctrl+Alt+F2).
  Primer reinicio: después del logo quedó todo negro porque mpv tomó la pantalla antes de que Plymouth terminara;
  ahora mpv espera a que se cierre `plymouthd` (probado: Plymouth terminó 18:01:08, mpv tomó la pantalla 18:01:18).
- **Fundidos** (0,5 s, pedido de Cristian): cada cambio de pantalla (reposo, video, patrón) pasa por negro.
  Un rectángulo negro con `osd-overlay` (ASS con alfa, 10 pasos) en la capa de mpv, que está encima del video,
  sin tocar la decodificación (nada de `--vf=fade`, que copia los cuadros por la CPU). El archivo se cambia con
  la pantalla en negro y se vuelve cuando mpv ya lo cargó. El video arranca contando lo que sonó durante el fundido.
  Lo que aprendimos probando: **mpv borra los overlays de una conexión IPC cuando se cierra**, así que el negro va
  por una conexión fija (si se corta a mitad de un fundido, el negro no queda pegado). Se ve con `--osd-level=0`.
  **`screenshot-to-file … window` no incluye el OSD** (ni un `show-text`): el fundido solo se puede ver en la pantalla.
- Stop: el engine despierta al hilo de video en cada cambio de estado (antes lo miraba cada 0,25 s) y el
  reposo se aplana una vez por archivo (ruta y mtime), no en cada Stop: ffmpeg tarda ~1 s en la 3B+.
- Pendiente: el video "lavado" (sospecha: rango de color limitado/completo en algún punto de la cadena Pi →
  adaptador HDMI-VGA → monitor; falta comparar el negro del logo con el del video).
- Ojo con reiniciar el engine: al abrir el iRig con la CMA casi agotada, el USB lo perdió
  (`usb_set_interface failed (-19)`) y reapareció solo. Con `cma-384` no se repitió.
**Falta la prueba que decide**: 30 min con el engine sonando por el iRig y el video, con Cristian mirando
el vúmetro (y el proyector: que el logo se vea bien y el video vaya con el click). Si traba, Pi 4.

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

Confirmado: en modo stomp cada footswitch es un **interruptor** (cada pisada prende/apaga su LED
rojo y alterna 127 / 0). No manda nada al soltar.

⚠️ **Rebote confirmado**: una pisada del D mandó `0 → 127 → 0` en 140 ms. **El MIDI Learn
necesita antirrebote** (ignorar repeticiones del mismo control dentro de ~200 ms) en los dos
modos: si no, una pisada de "Next" puede saltearse una canción en vivo.

**Footswitches numerados 1–4 en el chasis** (A–D en las notas de arriba = 1–4). Cada uno tiene
una función al mantenerlo (serigrafía del panel):

| Footswitch | Mantener | Medido |
|---|---|---|
| 1 | BANK DOWN | CC 91 = 127, sin cambiar el LED |
| 2 | BANK UP | CC 90 = 127, sin cambiar el LED |
| 3 | TUNER | no manda MIDI |
| 4 | TAP TEMPO | sin probar |

1+2 = LOOPER, 3+4 = STOMP (cambio de modo; LED "STOMP MODE" en el panel). La pisada larga
**no cambia el LED** y manda su propio mensaje: sirve como segunda acción por footswitch (1 y 2).
Vúmetro del panel = **DEVICE OUTPUT** (5 LEDs).

**LEDs controlables desde la Pi** (confirmado): CC 20 = 0 enviado al iRig apagó el LED del 1.
CC 20 = 32 **no** lo prendió en verde (la escala 1–64 verde / 65–127 rojo de la referencia no
aplica tal cual). Falta encontrar cómo prender verde/rojo.

⚠️ **Silencio sin explicar (22:45–22:47)**: con el set sonando y la Pi sana (engine vivo,
`xruns=0`, ALSA consumiendo a 48 kHz, sin errores USB), el iRig dejó de sacar audio (DEVICE
OUTPUT apagado). Mantener y tocar el 3 no lo recuperó (el tuner no silencia la salida, según
Cristian). Sospechosos: cuelgue espontáneo, el CC 20 = 32 enviado al iRig o la pisada larga
del 3. → Se repite el set **sin interacción** (sin MIDI hacia el iRig, sin pisadas largas).

Soak 1: **23 min con WAV real** (click en L, pista en R), 6 canciones seguidas con el stream
abierto, **0 xruns, 0 starved**, 33 °C, load < 0.2, con captura MIDI y pedaleo activos.

**Requisito nuevo**: el engine tiene que **detectar la desconexión del iRig y reconectarse solo**
cuando vuelve (hoy no lo hace).

**Referencia externa** (tabla que encontró Cristian, fuente sin verificar): coincide en PC 0–3,
CC 11 y CC 26, pero dice CC 22–25 para el modo stomp (medido: **20–23**), CC 39 = 0 (medido: 127)
y "127 pisar / 0 soltar" (medido: interruptor). Datos útiles a probar:
- CC 39 = aviso de cambio de modo (mantener 3 y 4 más de 1 s).
- **LEDs de los footswitches controlables desde la Pi** mandando CC al iRig (1–64 verde,
  65–127 rojo). Serviría como indicador de estado en la pedalera. Va por USB: se puede probar
  sin los cables DIN. Los números de CC probablemente sean los medidos (20–23), no los de la tabla.
- Arranque manteniendo el pulsador 1 = modo standalone (MIDI directo al DIN, sin host).

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
11. **Soak con WAV real en la Pi**: set 1, 29 min (7 canciones) sin cortes. Set 2 **sin ninguna
    interacción** (sin pisadas, sin MIDI hacia el iRig): engine sano todo el tiempo (0 xruns, ALSA
    consumiendo a 48 kHz, sin errores USB), pero a los **~25 min el iRig se silenció solo**, sin carga.
    → **Pi 3B+ + iRig no es viable para vivo tal como está.**
12. Al desenchufar el iRig, el engine **se queda colgado en silencio** (la posición se congela, sin
    error). La reconexión automática tiene que detectar también ese estado.
13. **Mismo set con el iRig en la Mac** (mismo engine, mismo archivo, 8 vueltas, 34 min): **llegó
    entero con audio**, 0 xruns. → **El iRig está sano: la culpable es la Pi 3B+** (su controlador
    USB `dwc_otg` con un dispositivo de audio USB 1.1 detrás del hub interno).
    Nota: el engine corre igual en macOS (CoreAudio) sin cambios.
15. **Prueba con `dwc_otg.speed=1`** (en `cmdline.txt`, copia en `cmdline.txt.bak-20260910-235538`):
    todo el USB en full speed (12M), sin transaction translator. iRig en `1-1.3`.
    **Resultado: 34 min enteros con audio**, 0 xruns, sin errores USB, 32 °C; y después volvió a
    sonar sin reenchufarlo (trabado habría quedado mudo). → **La Pi 3B+ es viable con
    `dwc_otg.speed=1`**, que ahora aplica el bootstrap (solo en Pi 3). Ethernet queda a 12 Mbit/s:
    irrelevante, el show va por WiFi.
    ⚠️ Es una sola corrida buena contra una que falló a los 25 min: repetir un soak largo (60+ min)
    y usarla en ensayos antes del primer show. Si vuelve a trabarse → Raspberry Pi 4.
14. Guía externa de optimización revisada: los parámetros FIQ que propone ya son los de fábrica,
    lo de PipeWire/WirePlumber no aplica (ALSA directo), la prioridad RT no ataca esto (0 xruns
    siempre) y `alsactl init` no destraba el firmware del iRig. PipeWire o JACK tampoco: usan el
    mismo driver y el mismo controlador USB.
    Si no alcanza, dos caminos:
    - **Raspberry Pi 4**: controlador USB xHCI, sin los problemas de `dwc_otg`. El código corre igual.
    - **Seguir con la 3B+ con reglas**: cero I/O pesada durante el show + soak test realista de
      60 min (engine con canciones reales, web abierta). Riesgo: si igual se traba en vivo, saca
      basura al PA y nada lo detecta.
