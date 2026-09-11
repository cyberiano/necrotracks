"""Engine de Necrotracks como servicio: dueño del audio, del show y de los controles MIDI.

Abre el device una sola vez y lo mantiene abierto (ver docs/PLAN.md). Si la placa del perfil elegido
no está y hay un respaldo (el jack de la Pi), sale por el respaldo; cuando vuelve la placa, con la
reproducción parada, se reinicia para usarla. Expone un socket unix en RAM (store.RUN/engine.sock)
con JSON por línea, que usan la web, la pedalera, el OLED y el CLI:

    → {"cmd": "play"}            play, pause, play_pause, stop, next, prev
    → {"cmd": "goto", "index": 3}
    → {"cmd": "load", "setlist": "show-necropolis"}   solo con la reproducción parada
    → {"cmd": "state"}           ← {"ok": true, "state": {...}}   el estado incluye "output"
    → {"cmd": "subscribe"}       ← {"state": {...}} en cada cambio y cada 0,5 s mientras suena
    → {"cmd": "controls"}        ← {"ok": true, "controls": {...}}  mapa MIDI y última pisada
    → {"cmd": "learn", "action": "next"}   espera la próxima pisada (hasta LEARN_TIMEOUT s)
    → {"cmd": "unlearn", "action": "next"}
    → {"cmd": "output"}          ← salida activa, perfil elegido, respaldo y perfiles disponibles
    → {"cmd": "set_output", "profile": "...", "fallback": "pi-jack"|null}   parado; reinicia el engine

Cada pedido responde {"ok": true} o {"ok": false, "error": "..."}. El engine no depende de
ningún cliente: si la web se cae, el show sigue, y los footswitches también.
"""
import asyncio
import json
import logging
import os
import shutil
import sys

from . import controls, library, profiles, setlists, store
from .show import Show

log = logging.getLogger("necrotracks.engine")

DEFAULT_CONFIG = {"profile": profiles.DEFAULT, "fallback": profiles.FALLBACK, "setlist": None,
                  "midi": {"port": controls.DEFAULT_PORT, "map": controls.DEFAULT_MAP}}
EMPTY_STATE = {"setlist": None, "slug": None, "state": "stopped", "index": 0, "count": 0, "song": None,
               "song_slug": None, "block": None,
               "behavior": None, "next": None, "position": 0.0, "duration": 0.0, "wait_remaining": None}
SHOW_COMMANDS = {"play", "pause", "play_pause", "stop", "next", "prev"}
LEARN_TIMEOUT = 15
MIDI_RETRY = 5  # segundos entre intentos de abrir el puerto MIDI
PRIMARY_RETRY = 3  # segundos entre chequeos de si volvió la placa (sonando por el respaldo)


class EngineError(Exception):
    pass


def config_path():
    return store.DATA / "config.json"


def cursor_path():
    # En RAM: se escribe en cada cambio y sobrevive a un reinicio del servicio, no de la Pi.
    return store.RUN / "cursor.json"


def load_config():
    return {**DEFAULT_CONFIG, **(store.read_json(config_path(), {}) or {})}


def _output(used, wanted):
    return {"profile": used, "label": profiles.get(used)["label"], "wanted": wanted, "fallback": used != wanted}


def open_output(config, make_player=None):
    """Abre la salida del perfil elegido; si su placa no está y hay respaldo, la del respaldo.
    Devuelve (player, output). Sin ninguna: RuntimeError (el engine sale y systemd reintenta)."""
    if make_player is None:
        from .audio import Player

        def make_player(profile):
            return Player(profile["device"], profile["matrix"])

    wanted = config["profile"]
    try:
        return make_player(profiles.get(wanted)), _output(wanted, wanted)
    except RuntimeError as e:
        backup = config.get("fallback")
        if not backup or backup == wanted:
            raise
        log.warning("%s: salgo por el respaldo (%s)", e, backup)
        return make_player(profiles.get(backup)), _output(backup, wanted)


class Engine:
    def __init__(self, player, config=None, output=None):
        self.player = player
        self.config = config or load_config()
        self.output = output or _output(self.config["profile"], self.config["profile"])
        self.show = None
        self.loop = None
        self.subscribers = set()
        self._writers = set()
        self.controls = controls.Controls(self.config["midi"]["map"], self._control_action)
        self.midi_port = None  # lo fija main(); en los tests no se abre MIDI
        self.restart = None  # lo fija main(): salir para que systemd reinicie con otra salida

    def state(self):
        return {**(self.show.snapshot() if self.show else EMPTY_STATE), "output": self.output}

    def _require_stopped(self, what):
        if self.show and self.show.state != "stopped":
            raise EngineError(f"Frená la reproducción antes de {what}")

    def load(self, name):
        self._require_stopped("cambiar de set list")
        setlist = setlists.get(name)
        if setlist is None:
            raise EngineError(f"No existe la set list '{name}'")
        problems = setlists.check(setlist)
        if problems:
            raise EngineError("La set list tiene problemas: " + "; ".join(problems))
        if self.show:
            self.show.send("quit")
        songs = {item["song"]: library.get_song(item["song"]) for item in setlist["items"]}
        cursor = store.read_json(cursor_path(), {}) or {}
        index = cursor.get("index", 0) if cursor.get("setlist") == setlist["slug"] else 0
        self.show = Show(self.player, setlist, songs, library.render_path, on_change=self._on_change, index=index)
        self.show.start()
        if self.config.get("setlist") != setlist["slug"]:
            self.config["setlist"] = setlist["slug"]
            store.write_json(config_path(), self.config)  # parado: escribir unos bytes no molesta
        self._on_change(self.show.snapshot())

    def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "state":
            return {"ok": True, "state": self.state()}
        if cmd == "load":
            self.load(req.get("setlist") or "")
            return {"ok": True}
        if cmd in SHOW_COMMANDS or cmd == "goto":
            if not self.show:
                raise EngineError("No hay set list cargada")
            self.show.send(cmd, req.get("index") if cmd == "goto" else None)
            return {"ok": True}
        if cmd == "controls":
            return {"ok": True, "controls": self.controls.snapshot()}
        if cmd == "unlearn":
            self._require_stopped("cambiar los controles")
            self.controls.forget(req.get("action"))
            self._save_midi()
            return {"ok": True}
        if cmd == "output":
            return {"ok": True, "output": self.output, "profile": self.config["profile"],
                    "fallback": self.config.get("fallback"),
                    "profiles": {k: p["label"] for k, p in profiles.PROFILES.items()}}
        if cmd == "set_output":
            return self.set_output(req.get("profile") or self.config["profile"], req.get("fallback") or None)
        raise EngineError(f"Comando desconocido: {cmd!r}")

    def set_output(self, profile, fallback):
        """Guarda la salida elegida y reinicia el engine para abrirla (el stream no se reconfigura en caliente)."""
        self._require_stopped("cambiar la salida de audio")
        profiles.get(profile)  # ValueError si no existe
        if fallback:
            profiles.get(fallback)
        changed = (profile, fallback) != (self.config["profile"], self.config.get("fallback"))
        self.config.update(profile=profile, fallback=fallback)
        store.write_json(config_path(), self.config)
        restart = changed and self.restart is not None
        if restart:
            log.info("salida: %s (respaldo: %s): reinicio el engine", profile, fallback or "ninguno")
            self.loop.call_later(0.5, self.restart)  # después de responder
        return {"ok": True, "restart": restart}

    async def learn(self, action):
        """Asigna a `action` el próximo control que se pise."""
        if action not in controls.ACTIONS:
            raise EngineError(f"Acción desconocida: {action!r}")
        self._require_stopped("cambiar los controles")
        if not self.controls.connected and self.midi_port:
            raise EngineError("No hay pedalera MIDI conectada")
        future = self.loop.create_future()
        self.controls.learn(action, lambda key: self.loop.call_soon_threadsafe(
            lambda: future.done() or future.set_result(key)))
        try:
            key = await asyncio.wait_for(future, LEARN_TIMEOUT)
        except asyncio.TimeoutError:
            self.controls.cancel_learn()
            raise EngineError("No llegó ninguna pisada: probá de nuevo") from None
        self._save_midi()
        log.info("MIDI learn: %s → %s", controls.label(key), action)
        return {"ok": True, "control": key, "label": controls.label(key)}

    def _save_midi(self):
        self.config["midi"] = {**self.config["midi"], "map": self.controls.mapping()}
        store.write_json(config_path(), self.config)

    # Llega desde el hilo de MIDI: el show encola el comando.
    def _control_action(self, action):
        log.info("pedal: %s", action)
        if self.show:
            self.show.send(action)

    # Los cambios llegan desde el hilo del show: se pasan al loop de asyncio.
    def _on_change(self, snapshot):
        if self.show:
            store.write_json(cursor_path(), {"setlist": self.show.setlist["slug"], "index": snapshot["index"]})
        if self.loop:
            self.loop.call_soon_threadsafe(self._broadcast, {**snapshot, "output": self.output})

    def _broadcast(self, snapshot):
        for q in list(self.subscribers):
            if not q.full():
                q.put_nowait(snapshot)

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.5)
            if self.show and self.show.state in ("playing", "waiting"):
                self._broadcast(self.state())

    async def _midi_watch(self):
        """Abre el puerto MIDI y, si todavía no está (otra pedalera, enchufada después), reintenta."""
        warned = False
        while not self.controls.connected:
            try:
                if self.controls.open(self.midi_port):
                    log.info("MIDI: escuchando %s", self.controls.port_name)
                    return
            except Exception as e:  # sin MIDI el show sigue: se maneja por la web
                log.warning("MIDI: no se pudo abrir %r: %s", self.midi_port, e)
            if not warned:
                log.warning("MIDI: no hay un puerto que contenga %r; reintento cada %d s", self.midi_port, MIDI_RETRY)
                warned = True
            await asyncio.sleep(MIDI_RETRY)

    async def _primary_watch(self):
        """Sonando por el respaldo: cuando vuelve la placa del perfil elegido y está parado, reinicia para usarla."""
        wanted = profiles.get(self.output["wanted"])
        while True:
            await asyncio.sleep(PRIMARY_RETRY)
            if profiles.card_present(wanted) and (not self.show or self.show.state == "stopped"):
                log.info("volvió %s: reinicio el engine para usarlo", wanted["device"])
                self.restart()
                return

    async def _client(self, reader, writer):
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                    if req.get("cmd") == "subscribe":
                        await self._stream(writer)
                        return
                    resp = await self.learn(req.get("action")) if req.get("cmd") == "learn" else self.handle(req)
                except (EngineError, ValueError, TypeError, AttributeError) as e:
                    resp = {"ok": False, "error": str(e)}
                writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _stream(self, writer):
        q = asyncio.Queue(maxsize=50)
        self.subscribers.add(q)
        try:
            q.put_nowait(self.state())
            while True:
                snapshot = await q.get()
                writer.write((json.dumps({"state": snapshot}, ensure_ascii=False) + "\n").encode())
                await writer.drain()
        finally:
            self.subscribers.discard(q)

    async def serve(self, path, ready=None):
        self.loop = asyncio.get_running_loop()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self._client, path=str(path))
        tasks = [asyncio.create_task(self._ticker())]
        if self.midi_port:
            tasks.append(asyncio.create_task(self._midi_watch()))
        if self.output["fallback"] and self.restart:
            tasks.append(asyncio.create_task(self._primary_watch()))
        log.info("escuchando en %s", path)
        if ready:
            ready.set()
        try:
            # No serve_forever(): al cancelarse espera (wait_closed) a que se vayan todos los
            # clientes antes de dejar correr este finally, y un suscriptor no se va nunca.
            await asyncio.Future()
        finally:
            for task in tasks:
                task.cancel()
            for writer in list(self._writers):
                writer.close()
            server.close()


def socket_path():
    return store.RUN / "engine.sock"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    try:
        player, output = open_output(config)
    except RuntimeError as e:  # sin ninguna salida, sale y systemd reintenta: una línea, sin traceback
        log.error("%s", e)
        sys.exit(1)
    player.on_device_lost = lambda: os._exit(3)  # systemd lo reinicia; al volver, recupera la set list y el cursor
    log.info("audio abierto: %s%s", output["label"], " (RESPALDO)" if output["fallback"] else "")
    engine = Engine(player, config, output)
    engine.midi_port = config["midi"]["port"]
    engine.restart = lambda: os._exit(0)  # Restart=always: systemd lo levanta con la salida nueva
    if config.get("video", True) and shutil.which("mpv"):
        from .video import Mpv, Video

        # El logo va en los datos (no en el repo): sin él, la pantalla queda en negro entre videos.
        delay = player.stream.latency + config.get("video_offset", 0.0)
        Video(engine.state, library.video_path, store.DATA / "video-logo.png", delay=delay,
              mpv=Mpv(store.RUN / "mpv.sock")).start()
    if config["setlist"]:
        try:
            engine.load(config["setlist"])
        except EngineError as e:
            log.warning("no se cargó la set list guardada: %s", e)
    asyncio.run(engine.serve(socket_path()))


if __name__ == "__main__":
    main()
