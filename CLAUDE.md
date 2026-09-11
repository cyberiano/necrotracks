# Necrotracks

Reproductor de pistas para vivo de la banda: Raspberry Pi 3B+ + iRig Stomp I/O. Proyecto personal:
práctico, sin sobreingeniería. Todo en español rioplatense (UI, docs, commits).

- **Leer primero `docs/PLAN.md`**: decisiones, fases y todo lo que se aprendió probando el hardware.
  `docs/Reproductor multipista para vivo Necrotracks.md` es la propuesta original (el *qué*).
- Código: `engine/` (audio, store, library, setlists, show, profiles, controls, video, daemon, cli),
  `web/` (FastAPI + HTML/JS sin build step), `bin/` (bootstrap, deploy, `remote/` con setup, hotspot y
  splash), `systemd/`, `tools/` (icons.sh regenera íconos y splash; video_proto.py mide mpv), `tests/`.

## Pi
- `ssh necrotracks@necrotracks.local` (WiFi 192.168.1.32; Ethernet 192.168.1.228 si está enchufado).
  `sudo` sin password. Código en `/opt/necrotracks` (git), datos en `/var/lib/necrotracks` (nunca en git).
- Deploy: commit + push a `main`, después `bin/deploy.sh [--fast]`. Provisioning: `bin/bootstrap.sh` (idempotente).
  `NECROTRACKS_HOST=usuario@host` pisa el destino.
- Tests en la Mac, sin hardware: `.venv/bin/python -m pytest` (venv con `requirements-dev.txt`).

## Reglas que salen de probar el hardware
- **El iRig se traba** (saca basura o silencio, solo vuelve desenchufándolo) cuando la Pi 3B+ escribe fuerte
  en la SD. Ni ALSA ni el kernel lo detectan. Nada de I/O pesada mientras suena: el import está bloqueado
  mientras suena y escribe con tope de velocidad (`store.Throttle`).
- En la Pi 3B+ el USB va en full speed: **`dwc_otg.speed=1`** en `cmdline.txt` (lo aplica el bootstrap).
  Sin eso el iRig se silencia solo a los ~25 min. Con eso, 34 min limpios.
- iRig en el par de puertos USB **lejos del Ethernet** (`1-1.2` / `1-1.3`).
- El stream de audio se abre una vez y no se reconfigura nunca durante un show.
- `config.txt`: un `dtparam` de la base va **antes** del primer `dtoverlay` (ver `base_param` en el bootstrap).
- Nunca `pkill -f` con un patrón que pueda coincidir con la línea de comando del propio SSH: matar por PID.
- Pruebas de hardware con Cristian: de a un paso, estímulo sostenido y una sola pregunta.
- **`bin/deploy.sh` y el bootstrap reinician el engine (y reabren el iRig): nunca mientras suena.** Chequear
  `python -m engine.cli ctl state` antes. Para cambios solo de la web: `git pull` + reiniciar `necrotracks-web`.
- Video en la Pi: mpv con `--drm-draw-plane=overlay --drm-drmprime-video-plane=primary` (si no, el video queda
  tapado), `cma-384` (si no, el decodificador se queda sin buffers) y sin `--drm-draw-surface-size`.
- El journal está en RAM: se borra al reiniciar. Leerlo antes de reiniciar la Pi.
- Para medir el video sin que suene en la casa de Cristian: salida al jack (`/api/output` con `pi-jack`,
  sin nada enchufado) y después volver al iRig.
- En cadenas de comandos, que un test que falla corte todo: `pytest … || exit 1`, nunca `pytest | tail`.
- `sed -i` de la Mac no es el de la Pi: los scripts de `bin/remote/` se prueban en la Pi, sobre copias en `/tmp`.
