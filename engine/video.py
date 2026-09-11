"""Video por HDMI: un mpv que queda abierto mostrando la pantalla de reposo (el logo, o la imagen o el
video que se haya subido) y, mientras suena una canción con video, lo reproduce sincronizado con el audio.

mpv corre aparte, con menos prioridad que el engine, y se maneja por su socket JSON. Si se cae,
se vuelve a levantar; el audio no depende de él. Probado en la 3B+ (ver docs/PLAN.md): con
--vo=gpu --gpu-context=drm --hwdec=v4l2m2m decodifica por hardware y muestra por una capa de la
pantalla, sin copias por la CPU (1080p30 a ~16 % de CPU).

Qué se muestra, en orden: el video de la canción que suena; el patrón de ajuste (si se pidió, parado);
si no, la pantalla de reposo. El reposo tiene su propio encaje (escala, posición), aparte del de los videos.

El reloj es el del audio. Cada segundo se compara la posición del video con la del engine (menos
la latencia de la salida): si se alejó más de SEEK, salta; si se alejó más de TOLERANCE, ajusta
un poco la velocidad hasta alcanzarla.
"""
import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Capas de la pantalla: en la Pi el video decodificado va en la primaria y lo que dibuja mpv (imágenes,
# fondo) en la de arriba; con el orden por defecto de mpv el video queda tapado (pantalla negra con audio).
# --background=color: por defecto mpv dibuja un damero detrás de lo transparente. Negro para las imágenes;
# con un video, la capa de arriba se vuelve transparente (VIDEO_BG) para que se vea el de abajo.
MPV_ARGS = ["--vo=gpu", "--gpu-context=drm", "--hwdec=v4l2m2m", "--no-audio", "--idle=yes", "--force-window=yes",
            "--drm-draw-plane=overlay", "--drm-drmprime-video-plane=primary",
            "--background=color", "--background-color=#000000",
            "--keep-open=yes", "--image-display-duration=inf", "--osd-level=0", "--no-osc",
            "--no-input-default-bindings", "--really-quiet"]
VIDEO_BG = "background-color=#00000000"  # con un video, la capa de mpv transparente (solo para ese archivo)
IDLE_DIR = "reposo"  # en los datos: la imagen o el video de reposo que se subió (uno solo)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
FIT_MODES = {"fit": "Ajustar", "fill": "Llenar", "stretch": "Estirar"}
FIT_DEFAULT = {"mode": "fit", "scale": 100, "x": 0.0, "y": 0.0}
IDLE_FIT_DEFAULT = {"mode": "fit", "scale": 54, "x": 0.0, "y": 0.0}  # el logo a ~54 % del ancho, centrado
# Patrón de ajuste 16:9: grilla, zona segura del 5 % en rojo, borde blanco (tiene que verse entero) y cruz.
PATTERN = ("color=c=0x111111:s=1920x1080,drawgrid=w=192:h=108:t=2:c=0x3a3a3a,"
           "drawbox=x=96:y=54:w=1728:h=972:c=0xc1121f:t=6,drawbox=x=0:y=0:w=1920:h=1080:c=white:t=12,"
           "drawbox=x=954:y=390:w=12:h=300:c=white:t=fill,drawbox=x=810:y=534:w=300:h=12:c=white:t=fill")
SEEK = 0.3  # segundos de desfase para saltar
# Un cuadro dura 33 ms: con menos margen que eso, la corrección persigue ruido de medición.
TOLERANCE = 0.08  # desfase para empezar a corregir con la velocidad…
SETTLED = 0.03  # …y para volver a velocidad normal
GAIN = 0.5
MAX_SPEED_ADJUST = 0.03
LOAD_LEAD = 0.33  # mpv tarda ~330 ms en cargar y arrancar un video (medido en la 3B+): arranca adelante
SYNC_EVERY = 1.0
STEP = 0.25
RETRY = 10  # segundos entre intentos de levantar mpv (p. ej. sin pantalla conectada)
PLYMOUTH_WAIT = 60  # segundos, como mucho, esperando que termine la pantalla de arranque
MODE_CHECK = 5  # segundos entre chequeos de que mpv esté en el modo HDMI que corresponde (solo parado)


def hdmi_modes(base="/sys/class/drm"):
    """Modos que ofrece la pantalla conectada por HDMI (sin repetir); el primero es el que prefiere."""
    for card in sorted(Path(base).glob("card*-HDMI-A-*")):
        try:
            if (card / "status").read_text().strip() != "connected":
                continue
            return list(dict.fromkeys((card / "modes").read_text().split()))
        except OSError:
            continue
    return []


def _size(mode):
    m = re.fullmatch(r"(\d+)x(\d+)(i?)", mode)
    return (int(m.group(1)), int(m.group(2)), bool(m.group(3))) if m else None


def pick_mode(modes, wanted="auto"):
    """El modo HDMI a usar. Automático: el que prefiere la pantalla, salvo que sea 4:3 (o 5:4) y haya uno
    16:9. Los visuales son 16:9 y, por ejemplo, un adaptador HDMI-VGA puede pedir 1024x768 aunque el
    monitor sea ancho (y entonces lo estira). Nunca más de 1080p ni entrelazado: la Pi 3 no da para más."""
    usable = [m for m in modes if (s := _size(m)) and s[0] <= 1920 and s[1] <= 1080 and not s[2]]
    if not usable:
        return None  # que decida mpv
    if wanted and wanted != "auto" and wanted in usable:
        return wanted
    area = lambda m: _size(m)[0] * _size(m)[1]  # noqa: E731
    preferred = modes[0] if modes[0] in usable else None
    if preferred and _size(preferred)[0] / _size(preferred)[1] >= 1.5:
        return preferred
    wide = [m for m in usable if abs(_size(m)[0] / _size(m)[1] - 16 / 9) < 0.03]
    if wide:
        return max(wide, key=area)
    return preferred or max(usable, key=area)


def check_fit(fit, default=FIT_DEFAULT):
    """Valida y normaliza un encaje: modo, escala (%) y posición (% del tamaño de la imagen)."""
    f = {**default, **(fit or {})}
    if f["mode"] not in FIT_MODES:
        raise ValueError(f"Encaje desconocido: {f['mode']}")
    scale, x, y = float(f["scale"]), float(f["x"]), float(f["y"])
    if not 50 <= scale <= 120:
        raise ValueError("La escala va de 50 a 120 %")
    if not (-50 <= x <= 50 and -50 <= y <= 50):
        raise ValueError("La posición va de −50 a 50 %")
    return {"mode": f["mode"], "scale": round(scale), "x": round(x, 1), "y": round(y, 1)}


def fit_props(fit):
    """Propiedades de mpv para un encaje. Se pasan al cargar cada archivo (mpv las deshace al cambiar)."""
    f = check_fit(fit)
    return {"keepaspect": f["mode"] != "stretch", "panscan": 1.0 if f["mode"] == "fill" else 0.0,
            "video-zoom": round(math.log2(f["scale"] / 100), 4),
            "video-pan-x": f["x"] / 100, "video-pan-y": f["y"] / 100}


def fit_opts(fit):
    return ",".join(f"{k}={('yes' if v else 'no') if isinstance(v, bool) else format(v, 'g')}"
                    for k, v in fit_props(fit).items())


def idle_file(data_dir, default_logo):
    """La pantalla de reposo: la imagen o el video subido a data/reposo, o el logo; None si no hay nada."""
    d = Path(data_dir) / IDLE_DIR
    if d.is_dir():
        for p in sorted(d.iterdir()):
            if not p.name.startswith(".") and p.suffix.lower() in IMAGE_EXT | VIDEO_EXT:
                return p
    return Path(default_logo) if Path(default_logo).exists() else None


def make_pattern(out):
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", PATTERN, "-frames:v", "1", str(out)],
                       check=True, capture_output=True, timeout=30)
        return Path(out)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("video: no se pudo generar el patrón de ajuste (%s)", e)
        return None


def flatten(image, out):
    """La imagen aplanada sobre negro: así no depende de cómo mpv pinta lo transparente (y una captura
    del HDMI lo puede confirmar). Si ffmpeg falla, queda la original."""
    try:
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0:s=x", str(image)], check=True, capture_output=True, text=True, timeout=30)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(image), "-filter_complex",
                        f"color=c=black:s={size.stdout.strip()}[bg];[bg][0:v]overlay=shortest=1,format=rgb24",
                        "-frames:v", "1", str(out)], check=True, capture_output=True, timeout=30)
        return Path(out)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("video: no se pudo aplanar %s (%s): va la original", image, e)
        return Path(image)


def plymouth_running():
    return subprocess.run(["pgrep", "-x", "plymouthd"], capture_output=True).returncode == 0


class Mpv:
    """El proceso mpv y su socket JSON."""

    def __init__(self, sock, mode=None):
        self.sock = Path(sock)
        self.proc = None
        self.mode = mode  # () → "WxH" o None (el que prefiere la pantalla); se consulta en cada arranque
        self.current_mode = None

    def start(self):
        self.sock.parent.mkdir(parents=True, exist_ok=True)
        mode = self.mode() if self.mode else None
        extra = []
        if mode:
            # Sin --drm-draw-surface-size: con la capa de mpv más chica que la pantalla, el video (que va en
            # otra capa) queda ubicado con las medidas de la chica y tapado (probado en la Pi: no se ve).
            extra.append(f"--drm-mode={mode}")
        self.current_mode = mode
        self.proc = subprocess.Popen(["mpv", *MPV_ARGS, *extra, f"--input-ipc-server={self.sock}"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, preexec_fn=lambda: os.nice(10))

    def restart(self):
        """Cierra mpv: el hilo de video lo vuelve a levantar (con el modo que corresponda ahora)."""
        if self.alive():
            self.proc.terminate()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def error_output(self):
        return (self.proc.stderr.read() if self.proc and self.proc.poll() is not None else "").strip()[-300:]

    def command(self, *args):
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(1)
            s.connect(str(self.sock))
            s.sendall((json.dumps({"command": list(args)}) + "\n").encode())
            f = s.makefile(encoding="utf-8")
            while True:
                msg = json.loads(f.readline())
                if "event" in msg:
                    continue  # mpv manda eventos a todos los clientes: no son la respuesta
                if msg.get("error") != "success":
                    raise RuntimeError(f"mpv {args[0]}: {msg.get('error')}")
                return msg.get("data")


class Video:
    def __init__(self, get_state, video_for, idle, delay=0.0, mpv=None, clock=time.monotonic, cache_dir=None,
                 fit=None, idle_fit=None):
        self.get_state = get_state  # → estado del engine (el mismo que ve la web)
        self.video_for = video_for  # slug → ruta del video, o None
        self.idle = idle  # () → ruta de la pantalla de reposo (imagen o video), o None: negro
        self.cache_dir = cache_dir  # dónde dejar imágenes aplanadas y el patrón (RAM); None = sin eso
        self.fit = check_fit(fit)
        self.idle_fit = check_fit(idle_fit, IDLE_FIT_DEFAULT)
        self.pattern = None  # ruta del patrón de ajuste (lo genera _run)
        self.pattern_on = False  # mostrarlo en vez del reposo (solo parado; se apaga al sonar)
        self.delay = delay  # latencia de la salida de audio: lo que suena va atrasado respecto de position
        self.mpv = mpv
        self.clock = clock
        # Lo que hay en pantalla: ("video", ruta) | ("pattern", ruta) | ("idle", ruta, mtime) | None (negro);
        # False = nada todavía.
        self.loaded = False
        self.paused = None
        self.speed = 1.0
        self._last_sync = 0.0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="video").start()

    def _run(self):
        # Si mpv toma la pantalla mientras está la de arranque (Plymouth), al cerrarse ésta deja todo en
        # negro y una imagen quieta no se vuelve a dibujar: se espera a que termine.
        for _ in range(PLYMOUTH_WAIT * 2):
            if not plymouth_running():
                break
            time.sleep(0.5)
        if self.cache_dir:
            self.pattern = make_pattern(Path(self.cache_dir) / "patron-16x9.png")
        warned, last_check = False, time.monotonic()
        while True:
            if not self.mpv.alive():
                self.mpv.start()
                if not self._wait_ready():
                    if not warned:
                        log.warning("video: mpv no arranca (¿hay pantalla por HDMI?): %s", self.mpv.error_output())
                        warned = True
                    time.sleep(RETRY)
                    continue
                log.info("video: mpv listo en el HDMI (%s)", getattr(self.mpv, "current_mode", None) or "modo preferido")
                warned, self.loaded, self.paused = False, False, None
            try:
                s = self.get_state()
                self.step(s)
                if s["state"] == "stopped" and time.monotonic() - last_check >= MODE_CHECK:
                    last_check = time.monotonic()
                    self._check_mode()
            except (OSError, RuntimeError, ValueError) as e:
                log.warning("video: %s", e)
                time.sleep(1)
            time.sleep(STEP)

    def _check_mode(self):
        """Si mpv no está en el modo HDMI que corresponde, lo reinicia en ese modo. Pasa al arrancar (la
        pantalla todavía no había mandado todos sus modos: medido, arrancó en 1024x768 con 1920x1080 elegido)
        y si enchufan un proyector con la Pi prendida. Solo se llama parado: no corta un video."""
        mode = getattr(self.mpv, "mode", None)
        wanted = mode() if mode else None
        if wanted and wanted != self.mpv.current_mode:
            log.info("video: la pantalla va en %s y mpv está en %s: lo reinicio", wanted, self.mpv.current_mode)
            self.mpv.restart()

    def _wait_ready(self, seconds=5):
        """Espera a que mpv abra la pantalla y conteste por el socket."""
        for _ in range(int(seconds / 0.25)):
            time.sleep(0.25)
            if not self.mpv.alive():
                return False
            try:
                self.mpv.command("get_property", "idle-active")
                return True
            except (OSError, RuntimeError, ValueError):
                pass
        return self.mpv.alive()

    def _want(self, s):
        """Qué tiene que estar en pantalla según el estado del engine."""
        if s["state"] in ("playing", "paused"):
            self.pattern_on = False  # el patrón es para ajustar con todo parado
            path = self.video_for(s["song_slug"]) if s.get("song_slug") else None
            if path:
                return ("video", str(path))
        elif self.pattern_on and self.pattern:
            return ("pattern", str(self.pattern))
        idle = self.idle() if self.idle else None
        try:
            return ("idle", str(idle), Path(idle).stat().st_mtime) if idle else None
        except OSError:
            return None

    def step(self, s):
        """Lleva mpv a lo que corresponde según el estado del engine."""
        want = self._want(s)
        kind = want[0] if want else None
        target = round(max(0.0, s["position"] - self.delay), 3)
        paused = s["state"] == "paused"
        if want != self.loaded:
            if kind == "video":
                start = target if paused else target + LOAD_LEAD
                self.mpv.command("loadfile", want[1], "replace", -1,
                                 f"start={start:.3f},{fit_opts(self.fit)},{VIDEO_BG},pause={'yes' if paused else 'no'}")
                self.paused = paused
            elif kind == "pattern":
                self.mpv.command("loadfile", want[1], "replace", -1, f"{fit_opts(self.fit)},{VIDEO_BG},pause=no")
            elif kind == "idle" and Path(want[1]).suffix.lower() in VIDEO_EXT:
                self.mpv.command("loadfile", want[1], "replace", -1,
                                 f"{fit_opts(self.idle_fit)},{VIDEO_BG},loop-file=inf,pause=no")
            elif kind == "idle":
                self.mpv.command("loadfile", str(self._flat(Path(want[1]))), "replace", -1,
                                 f"{fit_opts(self.idle_fit)},pause=no")
            else:
                self.mpv.command("stop")  # sin reposo: negro
            self.loaded = want
            self._set_speed(1.0)
            self._last_sync = self.clock()
            return
        if kind != "video":
            return
        if paused != self.paused:
            self.mpv.command("set_property", "pause", paused)
            self.paused = paused
        if not paused and self.clock() - self._last_sync >= SYNC_EVERY:
            self._last_sync = self.clock()
            self._sync(target)

    def _flat(self, image):
        return flatten(image, Path(self.cache_dir) / "reposo-plano.png") if self.cache_dir else image

    def _apply(self, fit):
        for prop, value in fit_props(fit).items():
            self.mpv.command("set_property", prop, value)

    def set_fit(self, fit):
        """Nuevo encaje de los videos: queda para los próximos y, si hay un video o el patrón en pantalla,
        se aplica en vivo."""
        self.fit = check_fit(fit)
        if self.loaded and self.loaded[0] in ("video", "pattern"):
            self._apply(self.fit)

    def set_idle_fit(self, fit):
        """Nuevo encaje de la pantalla de reposo: si está en pantalla, se aplica en vivo."""
        self.idle_fit = check_fit(fit, IDLE_FIT_DEFAULT)
        if self.loaded and self.loaded[0] == "idle":
            self._apply(self.idle_fit)

    def _get(self, prop):
        try:
            return self.mpv.command("get_property", prop)
        except RuntimeError:
            return None  # p. ej. todavía cargando

    def _sync(self, target):
        if self._get("eof-reached"):
            return  # el video terminó antes que el audio: queda el último cuadro
        pos = self._get("time-pos")
        if pos is None:
            return
        diff = pos - target
        if abs(diff) > SEEK:
            self.mpv.command("seek", target, "absolute+exact")
            self._set_speed(1.0)
        elif abs(diff) > TOLERANCE:
            self._set_speed(1.0 - max(-MAX_SPEED_ADJUST, min(MAX_SPEED_ADJUST, diff * GAIN)))
        elif abs(diff) < SETTLED:
            self._set_speed(1.0)
        # entre SETTLED y TOLERANCE: sigue como está (sin ida y vuelta)

    def _set_speed(self, speed):
        speed = round(speed, 3)
        if speed != self.speed:
            self.mpv.command("set_property", "speed", speed)
            self.speed = speed
