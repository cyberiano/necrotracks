"""Salida de audio: lee un render de 4 canales, aplica la matriz del perfil y escribe al device ALSA.

El stream queda abierto siempre y escribe silencio cuando no hay canción: así el reloj
de salida nunca se detiene y play/stop no reabren el device.
"""
import logging
import queue
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

log = logging.getLogger(__name__)

SAMPLERATE = 48000
RENDER_CHANNELS = 4  # FOH L, FOH R, Click, Guía
BLOCK = 1024
FADE = int(0.010 * SAMPLERATE)
PREFILL_BLOCKS = int(0.5 * SAMPLERATE / BLOCK)  # buffer mínimo antes de arrancar


def find_device(name):
    for i, d in enumerate(sd.query_devices()):
        if name.lower() in d["name"].lower() and d["max_output_channels"] > 0:
            return i
    raise RuntimeError(f"No encuentro un device de audio que contenga {name!r}")


def _reader(path, q, stop_evt, ready):
    """Lee el render en bloques y los encola. None al final (o si falla).
    `ready` se activa cuando hay PREFILL_BLOCKS encolados o se terminó el archivo."""
    try:
        with sf.SoundFile(path) as f:
            if f.samplerate != SAMPLERATE:
                raise ValueError(f"{path}: {f.samplerate} Hz, se espera {SAMPLERATE}")
            while not stop_evt.is_set():
                data = f.read(BLOCK, dtype="float32", always_2d=True)
                if len(data) == 0:
                    break
                if data.shape[1] < RENDER_CHANNELS:
                    data = np.pad(data, ((0, 0), (0, RENDER_CHANNELS - data.shape[1])))
                _put(q, data[:, :RENDER_CHANNELS], stop_evt)
                if q.qsize() >= PREFILL_BLOCKS:
                    ready.set()
    except Exception:
        log.exception("Error leyendo %s", path)
    _put(q, None, stop_evt)
    ready.set()


def _put(q, item, stop_evt):
    while not stop_evt.is_set():
        try:
            q.put(item, timeout=0.1)
            return
        except queue.Full:
            pass


class Player:
    def __init__(self, device_name, matrix, latency=0.1, buffer_seconds=20.0):
        self.matrix = np.asarray(matrix, dtype=np.float32)  # salidas × 4
        self.outputs = self.matrix.shape[0]
        self.buffer_blocks = int(buffer_seconds * SAMPLERATE / BLOCK)
        self.position = 0  # frames reproducidos de la canción actual
        self.playing = False
        self.underflows = 0  # xruns reportados por ALSA
        self.starved = 0  # bloques en que el reader no llegó a tiempo
        self.on_end = None  # callback al terminar una canción (corre en el hilo de audio: que sea rápido)
        self._cmds = queue.SimpleQueue()
        self.stream = sd.OutputStream(
            device=find_device(device_name), samplerate=SAMPLERATE, channels=self.outputs,
            dtype="float32", blocksize=BLOCK, latency=latency,
        )
        self.stream.start()
        self._thread = threading.Thread(target=self._audio_loop, daemon=True, name="audio")
        self._thread.start()

    def play(self, path):
        q = queue.Queue(maxsize=self.buffer_blocks)
        stop_evt, ready = threading.Event(), threading.Event()
        threading.Thread(target=_reader, args=(path, q, stop_evt, ready), daemon=True, name="reader").start()
        self._cmds.put(("play", q, stop_evt, ready))

    def stop(self):
        self._cmds.put(("stop",))

    def close(self):
        self._cmds.put(("quit",))
        self._thread.join(timeout=2)
        self.stream.stop()
        self.stream.close()

    def _audio_loop(self):
        silence = np.zeros((BLOCK, self.outputs), np.float32)
        ramp = np.linspace(0, 1, FADE, dtype=np.float32)[:, None]
        cur = None  # (cola, evento de stop, evento de prefill) de la canción en curso
        fade_in = False
        while True:
            stopping = False
            while True:
                try:
                    cmd = self._cmds.get_nowait()
                except queue.Empty:
                    break
                if cmd[0] == "play":
                    if cur:
                        cur[1].set()
                    cur, fade_in, stopping = cmd[1:], True, False
                    self.position, self.playing = 0, True
                elif cmd[0] == "stop" and cur:
                    stopping = True
                elif cmd[0] == "quit":
                    if cur:
                        cur[1].set()
                    return

            out = silence
            if cur and not cur[2].is_set() and not stopping:
                pass  # esperando el prefill: silencio sin consumir ni contar como starvation
            elif cur:
                try:
                    block = cur[0].get_nowait()
                except queue.Empty:
                    self.starved += 1
                    block = False
                if block is None:
                    cur[1].set()
                    cur, self.playing = None, False
                    if self.on_end:
                        self.on_end()
                elif block is not False:
                    n = len(block)
                    out = np.zeros((BLOCK, self.outputs), np.float32)
                    out[:n] = block @ self.matrix.T
                    if fade_in:
                        out[:FADE] *= ramp
                        fade_in = False
                    if stopping:
                        out[:FADE] *= ramp[::-1]
                        out[FADE:] = 0
                    np.clip(out, -1.0, 1.0, out=out)
                    self.position += n
                if stopping and cur:
                    cur[1].set()
                    cur, self.playing = None, False

            if self.stream.write(out):
                self.underflows += 1
