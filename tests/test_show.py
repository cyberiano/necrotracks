import time
from pathlib import Path

import pytest

from engine import store
from engine.show import Show


class FakePlayer:
    def __init__(self):
        self.calls, self.position, self.on_end = [], 0, None

    def play(self, path):
        self.calls.append(("play", Path(path).parent.name))

    def stop(self):
        self.calls.append(("stop",))

    def pause(self):
        self.calls.append(("pause",))

    def resume(self):
        self.calls.append(("resume",))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(behaviors, waits=None):
    items = [{"song": f"s{i}", "behavior": b, "wait": (waits or {}).get(i, 0.0), "block": None}
             for i, b in enumerate(behaviors)]
    songs = {f"s{i}": {"name": f"Canción {i}", "duration": 100.0} for i in range(len(items))}
    player, clock = FakePlayer(), Clock()
    show = Show(player, {"name": "Test", "slug": "test", "items": items}, songs,
                lambda slug: Path("/lib") / slug / "render.wav", clock=clock)
    return show, player, clock


def test_arm_next():
    show, p, _ = make(["arm_next", "arm_next"])
    show.process("play")
    assert show.state == "playing" and p.calls == [("play", "s0")]
    show.process("ended")
    assert (show.state, show.index) == ("stopped", 1)
    show.process("play")
    assert p.calls[-1] == ("play", "s1")


def test_auto_next_y_fin_de_lista():
    show, p, _ = make(["auto_next", "auto_next"])
    show.process("play")
    show.process("ended")
    assert (show.state, show.index) == ("playing", 1) and p.calls[-1] == ("play", "s1")
    show.process("ended")
    assert (show.state, show.index) == ("stopped", 1)


def test_wait():
    show, p, clock = make(["wait", "stop"], waits={0: 5.0})
    show.process("play")
    show.process("ended")
    assert (show.state, show.index) == ("waiting", 1)
    assert show.snapshot()["wait_remaining"] == pytest.approx(5.0)
    clock.t += 4.9
    show.tick()
    assert show.state == "waiting"
    clock.t += 0.2
    show.tick()
    assert show.state == "playing" and p.calls[-1] == ("play", "s1")


def test_wait_se_cancela_o_se_adelanta():
    show, p, _ = make(["wait", "stop"], waits={0: 5.0})
    show.process("play")
    show.process("ended")
    show.process("stop")
    assert (show.state, show.index) == ("stopped", 1) and p.calls == [("play", "s0")]
    show.process("play")
    assert p.calls[-1] == ("play", "s1")

    show, p, _ = make(["wait", "stop"], waits={0: 5.0})
    show.process("play")
    show.process("ended")
    show.process("play")
    assert show.state == "playing" and p.calls[-1] == ("play", "s1")


def test_repeat():
    show, p, _ = make(["repeat", "stop"])
    show.process("play")
    show.process("ended")
    assert (show.state, show.index) == ("playing", 0) and p.calls == [("play", "s0"), ("play", "s0")]


def test_stop_al_finalizar():
    show, _, _ = make(["stop", "stop"])
    show.process("play")
    show.process("ended")
    assert (show.state, show.index) == ("stopped", 0)


def test_siguiente_y_anterior():
    show, p, _ = make(["arm_next"] * 3)
    for _ in range(3):
        show.process("next")
    assert (show.state, show.index) == ("stopped", 2) and p.calls == []
    show.process("prev")
    show.process("play")
    assert p.calls == [("play", "s1")]
    show.process("next")  # sonando: no hace nada
    show.process("goto", 0)
    assert (show.state, show.index) == ("playing", 1) and p.calls == [("play", "s1")]
    show.process("pause")
    show.process("prev")  # en pausa: tampoco
    assert (show.state, show.index) == ("paused", 1)
    show.process("stop")
    show.process("goto", 0)
    assert (show.state, show.index) == ("stopped", 0)


def test_siguiente_durante_la_espera_la_cancela():
    show, p, _ = make(["wait", "stop", "stop"], waits={0: 5.0})
    show.process("play")
    show.process("ended")
    show.process("next")
    assert (show.state, show.index) == ("stopped", 2) and p.calls == [("play", "s0")]


def test_pausa():
    show, p, _ = make(["arm_next"])
    show.process("play_pause")
    show.process("play_pause")
    assert show.state == "paused" and p.calls[-1] == ("pause",)
    show.process("play_pause")
    assert show.state == "playing" and p.calls[-1] == ("resume",)
    show.process("stop")
    assert show.state == "stopped" and p.calls[-1] == ("stop",)


def test_fin_viejo_se_ignora():
    show, p, _ = make(["auto_next", "auto_next"])
    show.process("ended")
    assert show.state == "stopped" and p.calls == []


def test_bandera_sonando():
    show, _, _ = make(["arm_next"])
    show.process("play")
    assert store.is_playing()
    show.process("stop")
    assert not store.is_playing()


def test_snapshot():
    show, _, _ = make(["arm_next", "arm_next"])
    s = show.snapshot()
    assert (s["song"], s["next"], s["count"], s["state"]) == ("Canción 0", "Canción 1", 2, "stopped")


def test_hilo_con_evento_del_player():
    show, p, _ = make(["auto_next", "stop"])
    show.start()
    show.send("play")
    assert until(lambda: p.calls == [("play", "s0")])
    p.on_end()  # como lo haría el hilo de audio
    assert until(lambda: (show.state, show.index) == ("playing", 1))
    show.send("quit")


def until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False
