# Necrotracks

Reproductor de pistas para vivo de la banda: Raspberry Pi 3B+ + iRig Stomp I/O. Toca pistas y click
por salidas separadas, con set lists, bloques y comportamiento por canción, y se maneja desde la
pedalera, la web o la propia Pi.

- **Qué es y cómo va**: [docs/PLAN.md](docs/PLAN.md). Empieza con el estado actual y los próximos pasos.
- **La propuesta original**: [docs/Reproductor multipista para vivo Necrotracks.md](docs/Reproductor%20multipista%20para%20vivo%20Necrotracks.md).
- **Para trabajar en el repo** (incluido Claude Code): [CLAUDE.md](CLAUDE.md).

```bash
bin/bootstrap.sh          # provisiona la Pi desde cero (idempotente)
bin/deploy.sh [--fast]    # despliega origin/main en la Pi
.venv/bin/python -m pytest
```
