import asyncio
import tempfile
import threading
from pathlib import Path

import mido

from engine import daemon, store
from engine.controls import DEFAULT_MAP, Controls, label
from test_daemon import request
from test_show import FakePlayer


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(mapping=DEFAULT_MAP):
    sent, clock = [], Clock()
    return Controls(mapping, sent.append, clock=clock), sent, clock


def pc(n):
    return mido.Message("program_change", program=n)


def cc(n, value):
    return mido.Message("control_change", control=n, value=value)


def test_footswitches_del_irig_en_modo_normal():
    c, sent, clock = make()
    for n in range(4):
        c.handle(pc(n))
        clock.t += 1
    assert sent == ["prev", "next", "stop", "play_pause"]


def test_antirrebote_en_modo_stomp():
    c, sent, clock = make()
    for value in (0, 127, 0):  # el rebote medido: 0 → 127 → 0 en 140 ms
        c.handle(cc(23, value))
        clock.t += 0.07
    assert sent == ["play_pause"]
    c.handle(cc(22, 127))  # otro footswitch dentro de la ventana sí cuenta
    assert sent == ["play_pause", "stop"]
    clock.t += 0.5
    c.handle(cc(23, 127))
    assert sent == ["play_pause", "stop", "play_pause"]


def test_el_pedal_de_expresion_no_es_un_boton():
    c, sent, clock = make({"cc:0:11": "stop"})
    c.handle(cc(11, 64))
    clock.t += 1
    c.handle(cc(11, 127))  # llega al tope: sigue siendo el pedal
    assert sent == []


def test_midi_learn():
    c, sent, clock = make({})
    learned = []
    c.handle(cc(11, 80))  # alguien movió el pedal antes
    c.learn("next", learned.append)
    clock.t += 1
    c.handle(cc(11, 127))
    assert learned == [] and c.snapshot()["learning"] == "next"
    c.handle(pc(1))
    assert learned == ["pc:0:1"] and c.map == {"pc:0:1": "next"}
    assert sent == []  # la pisada que se aprende no dispara nada
    clock.t += 1
    c.handle(pc(1))
    assert sent == ["next"]
    c.learn("stop", learned.append)  # el mismo control pasa a otra acción
    clock.t += 1
    c.handle(pc(1))
    assert c.map == {"pc:0:1": "stop"}
    c.forget("stop")
    assert c.map == {}
    assert c.snapshot()["last"]["key"] == "pc:0:1"


def test_nombres_de_los_controles():
    assert label("pc:0:2") == "Footswitch 3 (Program Change 2 · canal 1)"
    assert label("cc:0:90") == "Footswitch 2 mantenido (CC 90 · canal 1)"
    assert label("cc:3:7") == "CC 7 · canal 4"


def test_learn_por_el_socket():
    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))
    # Ruta corta: un socket unix admite ~104 caracteres en macOS y tmp_path es más largo.
    path = Path(tempfile.mkdtemp(dir="/tmp")) / "engine.sock"

    async def scenario():
        ready = asyncio.Event()
        server = asyncio.create_task(engine.serve(path, ready))
        await asyncio.wait_for(ready.wait(), 2)

        learn = asyncio.create_task(request(path, {"cmd": "learn", "action": "next"}))
        while engine.controls.snapshot()["learning"] != "next":
            await asyncio.sleep(0.01)
        note = mido.Message("note_on", note=60, velocity=100)
        threading.Thread(target=engine.controls.handle, args=(note,)).start()  # "el hilo de MIDI"
        resp = await learn
        assert resp == {"ok": True, "control": "note:0:60", "label": "Nota 60 · canal 1"}
        assert store.read_json(daemon.config_path())["midi"]["map"]["note:0:60"] == "next"

        ctl = (await request(path, {"cmd": "controls"}))["controls"]
        assert ctl["last"]["key"] == "note:0:60" and ctl["map"]["pc:0:1"] == "next"
        assert (await request(path, {"cmd": "unlearn", "action": "next"}))["ok"]
        assert "pc:0:1" not in store.read_json(daemon.config_path())["midi"]["map"]
        assert not (await request(path, {"cmd": "learn", "action": "volar"}))["ok"]
        server.cancel()

    asyncio.run(scenario())
