import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from conftest import tone, write
from engine import daemon, library, setlists, store
from test_show import FakePlayer


@pytest.fixture
def setlist(tmp_path):
    for name in ("Uno", "Dos"):
        library.import_song(write(tmp_path / f"{name}.wav", tone(220)), name=name)
    setlists.create("Ensayo", ["uno", "dos"], behavior="auto_next")
    return "ensayo"


async def request(path, req):
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    resp = json.loads(await asyncio.wait_for(reader.readline(), 2))
    writer.close()
    return resp


async def next_state(reader, predicate, timeout=2.0):
    async def wait():
        while True:
            state = json.loads(await reader.readline())["state"]
            if predicate(state):
                return state
    return await asyncio.wait_for(wait(), timeout)


def test_protocolo_completo(setlist):
    player = FakePlayer()
    engine = daemon.Engine(player, dict(daemon.DEFAULT_CONFIG))
    # Ruta corta: un socket unix admite ~104 caracteres en macOS y tmp_path es más largo.
    path = Path(tempfile.mkdtemp(dir="/tmp")) / "engine.sock"

    async def scenario():
        ready = asyncio.Event()
        server = asyncio.create_task(engine.serve(path, ready))
        done, _ = await asyncio.wait({server, asyncio.create_task(ready.wait())},
                                     timeout=2, return_when=asyncio.FIRST_COMPLETED)
        if server in done:
            server.result()  # el servidor no arrancó: que se vea el error
        assert ready.is_set()

        assert (await request(path, {"cmd": "play"})) == {"ok": False, "error": "No hay set list cargada"}
        assert (await request(path, {"cmd": "load", "setlist": "Ensayo"}))["ok"]
        assert store.read_json(daemon.config_path())["setlist"] == "ensayo"

        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(b'{"cmd": "subscribe"}\n')
        first = await next_state(reader, lambda s: True)
        assert (first["setlist"], first["song"], first["state"]) == ("Ensayo", "Uno", "stopped")

        await request(path, {"cmd": "play"})
        playing = await next_state(reader, lambda s: s["state"] == "playing")
        assert playing["song"] == "Uno" and player.calls[-1] == ("play", "uno")

        resp = await request(path, {"cmd": "load", "setlist": "ensayo"})
        assert not resp["ok"] and "Frená" in resp["error"]

        player.on_end()  # fin de canción, desde "el hilo de audio"
        second = await next_state(reader, lambda s: s["index"] == 1)
        assert second["song"] == "Dos" and second["state"] == "playing"

        await request(path, {"cmd": "stop"})
        await next_state(reader, lambda s: s["state"] == "stopped")
        assert (await request(path, {"cmd": "state"}))["state"]["index"] == 1
        assert not (await request(path, {"cmd": "volar"}))["ok"]

        writer.close()
        server.cancel()
        await asyncio.wait({server}, timeout=2)
        assert server.cancelled(), "el servidor no terminó al cancelarlo"

    asyncio.run(asyncio.wait_for(scenario(), 10))  # si algo no vuelve, falla y muestra dónde


def test_set_list_con_problemas_no_se_carga(setlist):
    library.render_path("dos").unlink()
    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))
    with pytest.raises(daemon.EngineError, match="problemas"):
        engine.load("ensayo")


def test_recupera_el_cursor_tras_un_reinicio(setlist):
    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))
    engine.load("ensayo")
    engine.show.process("next")  # cursor en la 2
    engine.show.send("quit")
    again = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))  # como tras un reinicio del servicio
    again.load("ensayo")
    assert (again.show.index, again.show.state) == (1, "stopped")
    again.show.send("quit")
