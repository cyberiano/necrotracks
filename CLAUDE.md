# Necrotracks

Reproductor de pistas para vivo de la banda: Raspberry Pi 3B+ + iRig Stomp I/O. Proyecto personal:
práctico, sin sobreingeniería. Todo en español rioplatense (UI, docs, commits).

- **Leer primero `docs/PLAN.md`**: decisiones, fases y todo lo que se aprendió probando el hardware.
  `docs/Reproductor multipista para vivo Necrotracks.md` es la propuesta original (el *qué*).
- Código: `engine/` (audio, store, library, setlists, show, profiles, cli), `bin/` (bootstrap y deploy),
  `tools/`, `tests/`.

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
- iRig en el par de puertos USB **lejos del Ethernet** (`1-1.2`).
- El stream de audio se abre una vez y no se reconfigura nunca durante un show.
- `config.txt`: un `dtparam` de la base va **antes** del primer `dtoverlay` (ver `base_param` en el bootstrap).
- Nunca `pkill -f` con un patrón que pueda coincidir con la línea de comando del propio SSH: matar por PID.
- Pruebas de hardware con Cristian: de a un paso, estímulo sostenido y una sola pregunta.
