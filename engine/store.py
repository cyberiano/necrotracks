"""Rutas de datos, JSON atómico y escritura a velocidad limitada.

Todo lo que escribe en la SD pasa por acá. La SD escribiendo fuerte traba al iRig
(ver docs/PLAN.md), así que las escrituras grandes van con tope de velocidad y fsync
por bloque: nada de ráfagas de writeback.
"""
import json
import os
import re
import time
import unicodedata
from pathlib import Path

DATA = Path(os.environ.get("NECROTRACKS_DATA", "/var/lib/necrotracks"))
RUN = Path(os.environ.get("NECROTRACKS_RUN", "/dev/shm/necrotracks"))  # en RAM
WRITE_RATE = 3 * 1024 * 1024  # bytes/s para escrituras grandes (import)


def library_dir():
    return DATA / "library"


def setlists_dir():
    return DATA / "setlists"


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    if not s:
        raise ValueError(f"Nombre inválido: {name!r}")
    return s


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Throttle:
    """Limita el ritmo de escritura: llamar a wrote(n, archivo) después de escribir n bytes."""

    def __init__(self, rate=None):
        self.rate = rate or WRITE_RATE
        self.start = time.monotonic()
        self.total = 0

    def wrote(self, nbytes, fileobj=None):
        if fileobj is not None:
            fileobj.flush()
            os.fsync(fileobj.fileno())
        self.total += nbytes
        ahead = self.total / self.rate - (time.monotonic() - self.start)
        if ahead > 0:
            time.sleep(ahead)


def copy_stream(fi, fo, rate=None, chunk=512 * 1024):
    throttle = Throttle(rate)
    while block := fi.read(chunk):
        fo.write(block)
        throttle.wrote(len(block), fo)


# "Sonando": vive en RAM para no escribir en la SD durante el show. Lo consulta el import.
def set_playing(on):
    flag = RUN / "playing"
    if on:
        RUN.mkdir(parents=True, exist_ok=True)
        flag.write_text(str(os.getpid()))
    else:
        flag.unlink(missing_ok=True)


def is_playing():
    try:
        pid = int((RUN / "playing").read_text())
    except (FileNotFoundError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # quedó de un proceso que ya no existe
    except PermissionError:
        pass
    return True
