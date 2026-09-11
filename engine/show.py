"""Máquina de estados del show: recorre una set list sobre el Player.

Todas las transiciones corren en un único hilo. El Player avisa el fin de canción desde su
hilo de audio y acá solo se encola el evento: el audio nunca espera a la lógica del show.
Web, MIDI, GPIO y teclado mandan comandos con send(), desde el hilo que sea.

Estados:
    stopped  parado, con el cursor en una canción, listo para Play
    playing  sonando
    paused   en pausa a mitad de canción
    waiting  cuenta regresiva antes de la siguiente (comportamiento "wait")
"""
import queue
import threading
import time

from . import store


class Show:
    def __init__(self, player, setlist, songs, path_for, samplerate=48000, clock=time.monotonic, on_change=None):
        self.player = player
        self.setlist = setlist
        self.items = setlist["items"]
        self.songs = songs  # slug → song.json
        self.path_for = path_for  # slug → ruta del render
        self.samplerate = samplerate
        self.clock = clock
        self.on_change = on_change  # recibe un snapshot en cada cambio de estado o de canción
        self.state = "stopped"
        self.index = 0
        self.wait_until = None
        self._q = queue.SimpleQueue()
        self._lock = threading.RLock()
        player.on_end = lambda: self._q.put(("ended", None))

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="show").start()

    def send(self, cmd, arg=None):
        """Encola un comando: play, pause, play_pause, stop, next, prev, goto (arg = índice), quit."""
        self._q.put((cmd, arg))

    def snapshot(self):
        with self._lock:
            item = self.items[self.index] if self.items else None
            song = self.songs.get(item["song"]) if item else None
            nxt = self.items[self.index + 1] if self.index + 1 < len(self.items) else None
            nxt_song = self.songs.get(nxt["song"]) if nxt else None
            sounding = self.state in ("playing", "paused")
            return {
                "setlist": self.setlist["name"],
                "state": self.state,
                "index": self.index,
                "count": len(self.items),
                "song": song["name"] if song else None,
                "block": item["block"] if item else None,
                "behavior": item["behavior"] if item else None,
                "next": nxt_song["name"] if nxt_song else None,
                "position": self.player.position / self.samplerate if sounding else 0.0,
                "duration": song["duration"] if song else 0.0,
                "wait_remaining": max(0.0, self.wait_until - self.clock()) if self.state == "waiting" else None,
            }

    def _run(self):
        while True:
            try:
                cmd, arg = self._q.get(timeout=0.1)
            except queue.Empty:
                cmd = None
            if cmd == "quit":
                return
            with self._lock:
                if cmd:
                    self.process(cmd, arg)
                self.tick()

    # Lógica sincrónica: la usa el hilo del show y se puede testear sin hilos.
    def process(self, cmd, arg=None):
        handlers = {
            "play": self._play,
            "pause": self._pause,
            "play_pause": self._play_pause,
            "stop": self._stop,
            "next": lambda: self._jump(self.index + 1),
            "prev": lambda: self._jump(self.index - 1),
            "goto": lambda: self._jump(arg),
            "ended": self._ended,
        }
        before = (self.state, self.index)
        handlers[cmd]()
        if (self.state, self.index) != before:
            self._changed()

    def tick(self):
        if self.state == "waiting" and self.clock() >= self.wait_until:
            self._start(self.index)
            self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change(self.snapshot())

    def _set(self, state):
        self.state = state
        if state != "waiting":
            self.wait_until = None
        store.set_playing(state != "stopped")

    def _start(self, index):
        self.index = index
        self.player.play(str(self.path_for(self.items[index]["song"])))
        self._set("playing")

    def _play(self):
        if not self.items:
            return
        if self.state in ("stopped", "waiting"):  # en waiting: saltea la espera
            self._start(self.index)
        elif self.state == "paused":
            self.player.resume()
            self._set("playing")

    def _pause(self):
        if self.state == "playing":
            self.player.pause()
            self._set("paused")

    def _play_pause(self):
        if self.state == "playing":
            self._pause()
        else:
            self._play()

    def _stop(self):
        if self.state in ("playing", "paused"):
            self.player.stop()
        self._set("stopped")  # en waiting: cancela la espera, el cursor queda en la siguiente

    def _jump(self, index):
        """Sonando: salta y sigue sonando. Parado o esperando: solo mueve el cursor."""
        if index is None or not 0 <= index < len(self.items):
            return
        if self.state in ("playing", "paused"):
            self._start(index)
        else:
            self.index = index
            self._set("stopped")

    def _ended(self):
        if self.state != "playing":
            return  # evento viejo
        item = self.items[self.index]
        has_next = self.index + 1 < len(self.items)
        behavior = item["behavior"]
        if behavior == "repeat":
            self._start(self.index)
        elif behavior == "auto_next" and has_next:
            self._start(self.index + 1)
        elif behavior == "wait" and has_next:
            self.index += 1
            self._set("waiting")
            self.wait_until = self.clock() + item["wait"]
        elif behavior == "arm_next" and has_next:
            self.index += 1
            self._set("stopped")
        else:
            self._set("stopped")
