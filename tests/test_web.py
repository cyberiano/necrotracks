import asyncio
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import tone, write
from engine import daemon, library, setlists, store
from test_show import FakePlayer
from web import app as web


@pytest.fixture
def client():
    return TestClient(web.app)


def test_biblioteca_y_set_lists(client, tmp_path):
    library.import_song(write(tmp_path / "Uno.wav", tone(220)), name="Uno")
    assert [s["slug"] for s in client.get("/api/songs").json()] == ["uno"]

    assert client.post("/api/setlists", json={"name": "Ensayo"}).status_code == 200
    assert client.post("/api/setlists", json={"name": "ensayo"}).status_code == 409

    r = client.put("/api/setlists/ensayo", json={"name": "Ensayo del viernes", "items": [
        {"song": "uno", "behavior": "wait", "wait": 5, "block": " Bloque 1 "}]})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "ensayo" and body["name"] == "Ensayo del viernes"
    assert body["items"] == [{"song": "uno", "behavior": "wait", "wait": 5.0, "block": "Bloque 1"}]
    assert body["problems"] == [] and body["songs"]["uno"]["name"] == "Uno"

    assert client.put("/api/setlists/ensayo", json={"name": "x", "items": [{"song": "nada"}]}).status_code == 400
    bad_wait = {"name": "x", "items": [{"song": "uno", "behavior": "wait", "wait": 0}]}
    assert client.put("/api/setlists/ensayo", json=bad_wait).status_code == 400
    assert setlists.get("ensayo")["name"] == "Ensayo del viernes"  # lo rechazado no se guardó

    r = client.delete("/api/songs/uno")
    assert r.status_code == 409 and "Ensayo del viernes" in r.json()["detail"]
    assert client.delete("/api/setlists/ensayo").status_code == 200  # sin engine: no está sonando
    assert client.delete("/api/songs/uno").status_code == 200
    assert library.list_songs() == []


def test_import_por_la_web(client, tmp_path):
    stereo = write(tmp_path / "x.wav", np.stack([tone(1000), tone(220)], axis=1)).read_bytes()
    assert client.delete("/api/incoming").status_code == 200
    assert client.put("/api/incoming/Mi tema.wav", content=stereo).status_code == 200

    r = client.post("/api/import", json={})  # estéreo sin decir qué es
    assert r.status_code == 400 and "click-pista" in r.json()["detail"]

    r = client.post("/api/import", json={"layout": "click-pista"})  # reintento: el archivo sigue subido
    assert r.status_code == 200
    assert (r.json()["slug"], r.json()["foh"], r.json()["click"]) == ("mi-tema", "mono", True)
    assert not web.incoming_dir().exists()
    assert client.post("/api/import", json={}).status_code == 400  # ya no hay nada subido


def test_import_de_varios_archivos_pide_nombre(client, tmp_path):
    client.delete("/api/incoming")
    for name, freq in (("foh.wav", 220), ("click.wav", 1000)):
        client.put(f"/api/incoming/{name}", content=write(tmp_path / name, tone(freq)).read_bytes())
    assert client.post("/api/import", json={}).status_code == 400
    r = client.post("/api/import", json={"name": "Sands of time"})
    assert r.status_code == 200 and r.json()["slug"] == "sands-of-time"


def test_nada_de_escrituras_mientras_suena(client, tmp_path):
    store.set_playing(True)
    try:
        assert client.put("/api/incoming/a.wav", content=b"x").status_code == 409
    finally:
        store.set_playing(False)


def test_import_se_corta_si_arranca_a_sonar(tmp_path, monkeypatch):
    library.import_song(write(tmp_path / "Uno.wav", tone(220)), name="Uno")
    before = (store.library_dir() / "uno" / "render.wav").read_bytes()
    checks = iter([False])  # el chequeo inicial da "parado"; los siguientes, "sonando"
    monkeypatch.setattr(store, "is_playing", lambda: next(checks, True))
    with pytest.raises(library.ImportProblem, match="Arrancó la reproducción"):
        library.import_song(write(tmp_path / "Uno.wav", tone(440)), name="Uno")
    assert (store.library_dir() / "uno" / "render.wav").read_bytes() == before  # la anterior, intacta
    assert not (store.library_dir() / "uno.new").exists()


def test_pantalla_de_reposo(client):
    d = store.DATA / "reposo"
    assert client.put("/api/idle/fondo.png", content=b"png").status_code == 200
    assert [p.name for p in d.iterdir()] == ["fondo.png"]
    assert client.put("/api/idle/otro.jpg", content=b"jpg").status_code == 200  # reemplaza: queda uno solo
    assert [p.name for p in d.iterdir()] == ["otro.jpg"]
    assert client.put("/api/idle/animado.gif", content=b"gif").status_code == 400
    store.set_playing(True)
    try:
        assert client.put("/api/idle/fondo.png", content=b"png").status_code == 409
    finally:
        store.set_playing(False)
    assert client.delete("/api/idle").status_code == 200 and not d.exists()  # vuelve al logo


def test_engine_caido(client):
    assert client.post("/api/cmd", json={"cmd": "play"}).status_code == 503
    assert client.get("/api/state").json() == {"engine": False}
    assert client.post("/api/cmd", json={"cmd": "volar"}).status_code == 400


def test_comandos_al_engine(client, tmp_path, monkeypatch):
    for name in ("Uno", "Dos"):
        library.import_song(write(tmp_path / f"{name}.wav", tone(220)), name=name)
    setlists.create("Ensayo", ["uno", "dos"])
    # Ruta corta: un socket unix admite ~104 caracteres en macOS y tmp_path es más largo.
    path = Path(tempfile.mkdtemp(dir="/tmp")) / "engine.sock"
    monkeypatch.setattr(daemon, "socket_path", lambda: path)
    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))
    loop = asyncio.new_event_loop()
    server = loop.create_task(engine.serve(path))

    def run():
        try:
            loop.run_until_complete(server)
        except asyncio.CancelledError:
            pass

    threading.Thread(target=run, daemon=True).start()

    def state_until(predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            st = client.get("/api/state").json()
            if st["engine"] and predicate(st["state"]):
                return st["state"]
            time.sleep(0.02)
        raise AssertionError(f"el engine no llegó al estado esperado: {st}")

    try:
        state_until(lambda s: s["setlist"] is None)
        assert client.post("/api/cmd", json={"cmd": "load", "setlist": "ensayo"}).status_code == 200
        assert client.post("/api/cmd", json={"cmd": "goto", "index": 1}).status_code == 200
        st = state_until(lambda s: s["index"] == 1)
        assert (st["slug"], st["song"], st["state"]) == ("ensayo", "Dos", "stopped")
        r = client.post("/api/cmd", json={"cmd": "load", "setlist": "nada"})
        assert r.status_code == 409 and "No existe" in r.json()["detail"]
    finally:
        if engine.show:
            engine.show.send("quit")
        loop.call_soon_threadsafe(server.cancel)
