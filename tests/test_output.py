import asyncio
import tempfile
from pathlib import Path

import pytest

from engine import daemon, profiles, store
from test_daemon import request
from test_show import FakePlayer


def make_player(present):
    opened = []

    def make(profile):
        if profile["device"] not in present:
            raise RuntimeError(f"No encuentro un device de audio que contenga {profile['device']!r}")
        opened.append(profile["device"])
        return FakePlayer()

    return make, opened


def test_con_la_interfaz_no_usa_el_respaldo():
    make, opened = make_player({"iRig", "Headphones"})
    _, out = daemon.open_output(dict(daemon.DEFAULT_CONFIG), make)
    assert opened == ["iRig"] and not out["fallback"] and out["profile"] == "irig-click-pista"


def test_sin_la_interfaz_sale_por_el_jack():
    make, opened = make_player({"Headphones"})
    _, out = daemon.open_output(dict(daemon.DEFAULT_CONFIG), make)
    assert opened == ["Headphones"]
    assert out == {"profile": "pi-jack", "label": profiles.PROFILES["pi-jack"]["label"],
                   "wanted": "irig-click-pista", "fallback": True}


def test_sin_respaldo_no_abre_nada():
    make, _ = make_player({"Headphones"})
    with pytest.raises(RuntimeError, match="iRig"):
        daemon.open_output({**daemon.DEFAULT_CONFIG, "fallback": None}, make)


def test_placa_presente(tmp_path):
    cards = tmp_path / "cards"
    cards.write_text(" 0 [IO             ]: USB-Audio - iRig Stomp IO\n 1 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n")
    assert profiles.card_present(profiles.get("irig-click-pista"), cards)
    assert profiles.card_present(profiles.get("pi-jack"), cards)
    cards.write_text(" 1 [Headphones     ]: bcm2835_headpho - bcm2835 Headphones\n")
    assert not profiles.card_present(profiles.get("irig-stereo"), cards)
    assert not profiles.card_present(profiles.get("pi-jack"), tmp_path / "no-existe")


def test_cambiar_la_salida_por_el_socket():
    out = {"profile": "pi-jack", "label": "Jack", "wanted": "irig-click-pista", "fallback": True}
    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG), out)
    restarts = []
    engine.restart = lambda: restarts.append(True)
    # Ruta corta: un socket unix admite ~104 caracteres en macOS y tmp_path es más largo.
    path = Path(tempfile.mkdtemp(dir="/tmp")) / "engine.sock"

    async def scenario():
        ready = asyncio.Event()
        server = asyncio.create_task(engine.serve(path, ready))
        await asyncio.wait_for(ready.wait(), 2)
        assert (await request(path, {"cmd": "state"}))["state"]["output"]["fallback"]
        info = await request(path, {"cmd": "output"})
        assert info["profile"] == "irig-click-pista" and info["fallback"] == "pi-jack" and "pi-jack" in info["profiles"]

        assert not (await request(path, {"cmd": "set_output", "profile": "nada"}))["ok"]
        resp = await request(path, {"cmd": "set_output", "profile": "irig-stereo", "fallback": None})
        assert resp == {"ok": True, "restart": True}
        saved = store.read_json(daemon.config_path())
        assert (saved["profile"], saved["fallback"]) == ("irig-stereo", None)
        await asyncio.sleep(0.7)
        assert restarts == [True]  # reinicia después de responder
        assert (await request(path, {"cmd": "set_output", "profile": "irig-stereo"}))["restart"] is False
        server.cancel()

    asyncio.run(scenario())
