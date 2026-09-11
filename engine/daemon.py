"""Engine de Necrotracks como servicio: dueño del audio y del show.

Abre el device una sola vez y lo mantiene abierto (ver docs/PLAN.md). Expone un socket unix
en RAM (store.RUN/engine.sock) con JSON por línea, que usan la web, la pedalera, el OLED y el CLI:

    → {"cmd": "play"}            play, pause, play_pause, stop, next, prev
    → {"cmd": "goto", "index": 3}
    → {"cmd": "load", "setlist": "show-necropolis"}   solo con la reproducción parada
    → {"cmd": "state"}           ← {"ok": true, "state": {...}}
    → {"cmd": "subscribe"}       ← {"state": {...}} en cada cambio y cada 0,5 s mientras suena

Cada pedido responde {"ok": true} o {"ok": false, "error": "..."}. El engine no depende de
ningún cliente: si la web se cae, el show sigue.
"""
import asyncio
import json
import logging

from . import library, profiles, setlists, store
from .show import Show

log = logging.getLogger("necrotracks.engine")

DEFAULT_CONFIG = {"profile": profiles.DEFAULT, "setlist": None}
EMPTY_STATE = {"setlist": None, "state": "stopped", "index": 0, "count": 0, "song": None, "block": None,
               "behavior": None, "next": None, "position": 0.0, "duration": 0.0, "wait_remaining": None}
SHOW_COMMANDS = {"play", "pause", "play_pause", "stop", "next", "prev"}


class EngineError(Exception):
    pass


def config_path():
    return store.DATA / "config.json"


def load_config():
    return {**DEFAULT_CONFIG, **(store.read_json(config_path(), {}) or {})}


class Engine:
    def __init__(self, player, config=None):
        self.player = player
        self.config = config or load_config()
        self.show = None
        self.loop = None
        self.subscribers = set()
        self._writers = set()

    def state(self):
        return self.show.snapshot() if self.show else dict(EMPTY_STATE)

    def load(self, name):
        if self.show and self.show.state != "stopped":
            raise EngineError("Frená la reproducción antes de cambiar de set list")
        setlist = setlists.get(name)
        if setlist is None:
            raise EngineError(f"No existe la set list '{name}'")
        problems = setlists.check(setlist)
        if problems:
            raise EngineError("La set list tiene problemas: " + "; ".join(problems))
        if self.show:
            self.show.send("quit")
        songs = {item["song"]: library.get_song(item["song"]) for item in setlist["items"]}
        self.show = Show(self.player, setlist, songs, library.render_path, on_change=self._on_change)
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
        raise EngineError(f"Comando desconocido: {cmd!r}")

    # Los cambios llegan desde el hilo del show: se pasan al loop de asyncio.
    def _on_change(self, snapshot):
        if self.loop:
            self.loop.call_soon_threadsafe(self._broadcast, snapshot)

    def _broadcast(self, snapshot):
        for q in list(self.subscribers):
            if not q.full():
                q.put_nowait(snapshot)

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.5)
            if self.show and self.show.state in ("playing", "waiting"):
                self._broadcast(self.show.snapshot())

    async def _client(self, reader, writer):
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                    if req.get("cmd") == "subscribe":
                        await self._stream(writer)
                        return
                    resp = self.handle(req)
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
        ticker = asyncio.create_task(self._ticker())
        log.info("escuchando en %s", path)
        if ready:
            ready.set()
        try:
            # No serve_forever(): al cancelarse espera (wait_closed) a que se vayan todos los
            # clientes antes de dejar correr este finally, y un suscriptor no se va nunca.
            await asyncio.Future()
        finally:
            ticker.cancel()
            for writer in list(self._writers):
                writer.close()
            server.close()


def socket_path():
    return store.RUN / "engine.sock"


def main():
    from .audio import Player

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    profile = profiles.get(config["profile"])
    player = Player(profile["device"], profile["matrix"])  # sin el device, sale y systemd reintenta
    log.info("audio abierto: perfil %s", config["profile"])
    engine = Engine(player, config)
    if config["setlist"]:
        try:
            engine.load(config["setlist"])
        except EngineError as e:
            log.warning("no se cargó la set list guardada: %s", e)
    asyncio.run(engine.serve(socket_path()))


if __name__ == "__main__":
    main()
