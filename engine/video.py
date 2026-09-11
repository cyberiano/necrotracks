"""Video por HDMI: un mpv que queda abierto mostrando el logo y, mientras suena una canción con
video, lo reproduce sincronizado con el audio.

mpv corre aparte, con menos prioridad que el engine, y se maneja por su socket JSON. Si se cae,
se vuelve a levantar; el audio no depende de él. Probado en la 3B+ (ver docs/PLAN.md): con
--vo=gpu --gpu-context=drm --hwdec=v4l2m2m decodifica por hardware y muestra por una capa de la
pantalla, sin copias por la CPU (1080p30 a ~16 % de CPU).

El reloj es el del audio. Cada segundo se compara la posición del video con la del engine (menos
la latencia de la salida): si se alejó más de SEEK, salta; si se alejó más de TOLERANCE, ajusta
un poco la velocidad hasta alcanzarla.
"""
import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

MPV_ARGS = ["--vo=gpu", "--gpu-context=drm", "--hwdec=v4l2m2m", "--no-audio", "--idle=yes", "--force-window=yes",
            "--keep-open=yes", "--image-display-duration=inf", "--osd-level=0", "--no-osc",
            "--no-input-default-bindings", "--really-quiet"]
LOGO_ZOOM = -0.9  # log2 del tamaño: el logo a ~54 % del ancho, centrado sobre negro
SEEK = 0.3  # segundos de desfase para saltar
TOLERANCE = 0.04  # segundos de desfase para empezar a corregir con la velocidad
MAX_SPEED_ADJUST = 0.05
SYNC_EVERY = 1.0
STEP = 0.25
RETRY = 10  # segundos entre intentos de levantar mpv (p. ej. sin pantalla conectada)


class Mpv:
    """El proceso mpv y su socket JSON."""

    def __init__(self, sock):
        self.sock = Path(sock)
        self.proc = None

    def start(self):
        self.sock.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen(["mpv", *MPV_ARGS, f"--input-ipc-server={self.sock}"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                     text=True, preexec_fn=lambda: os.nice(10))

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
    def __init__(self, get_state, video_for, logo, delay=0.0, mpv=None, clock=time.monotonic):
        self.get_state = get_state  # → estado del engine (el mismo que ve la web)
        self.video_for = video_for  # slug → ruta del video, o None
        self.logo = Path(logo)
        self.delay = delay  # latencia de la salida de audio: lo que suena va atrasado respecto de position
        self.mpv = mpv
        self.clock = clock
        self.loaded = False  # ruta del video cargado; None = logo; False = nada todavía
        self.paused = None
        self.speed = 1.0
        self._last_sync = 0.0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="video").start()

    def _run(self):
        warned = False
        while True:
            if not self.mpv.alive():
                self.mpv.start()
                time.sleep(2)  # que abra la pantalla y el socket
                if not self.mpv.alive():
                    if not warned:
                        log.warning("video: mpv no arranca (¿hay pantalla por HDMI?): %s", self.mpv.error_output())
                        warned = True
                    time.sleep(RETRY)
                    continue
                log.info("video: mpv listo en el HDMI")
                warned, self.loaded, self.paused = False, False, None
            try:
                self.step(self.get_state())
            except (OSError, RuntimeError, ValueError) as e:
                log.warning("video: %s", e)
                time.sleep(1)
            time.sleep(STEP)

    def step(self, s):
        """Lleva mpv a lo que corresponde según el estado del engine."""
        sounding = s["state"] in ("playing", "paused")
        want = self.video_for(s["song_slug"]) if sounding and s.get("song_slug") else None
        target = round(max(0.0, s["position"] - self.delay), 3)
        paused = s["state"] == "paused"
        if want != self.loaded:
            if want:
                self.mpv.command("loadfile", str(want), "replace", -1,
                                 f"start={target:.3f},video-zoom=0,pause={'yes' if paused else 'no'}")
                self.paused = paused
            elif self.logo.exists():
                self.mpv.command("loadfile", str(self.logo), "replace", -1, f"video-zoom={LOGO_ZOOM},pause=no")
            else:
                self.mpv.command("stop")  # sin logo: negro
            self.loaded = want
            self._set_speed(1.0)
            self._last_sync = self.clock()
            return
        if not want:
            return
        if paused != self.paused:
            self.mpv.command("set_property", "pause", paused)
            self.paused = paused
        if not paused and self.clock() - self._last_sync >= SYNC_EVERY:
            self._last_sync = self.clock()
            self._sync(target)

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
            self._set_speed(1.0 - max(-MAX_SPEED_ADJUST, min(MAX_SPEED_ADJUST, diff)))
        else:
            self._set_speed(1.0)

    def _set_speed(self, speed):
        speed = round(speed, 3)
        if speed != self.speed:
            self.mpv.command("set_property", "speed", speed)
            self.speed = speed
