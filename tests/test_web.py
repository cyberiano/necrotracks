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
    assert body["items"] == [{"song": "uno", "behavior": "wait", "wait": 5.0, "block": "Bloque 1", "note": ""}]
    assert body["problems"] == [] and body["songs"]["uno"]["name"] == "Uno"
    assert body["songs"]["uno"]["video"] is None  # con video: la altura (720, 1080…), para marcarlo en el Show

    assert client.put("/api/setlists/ensayo", json={"name": "x", "items": [{"song": "nada"}]}).status_code == 400
    bad_wait = {"name": "x", "items": [{"song": "uno", "behavior": "wait", "wait": 0}]}
    assert client.put("/api/setlists/ensayo", json=bad_wait).status_code == 400
    assert setlists.get("ensayo")["name"] == "Ensayo del viernes"  # lo rechazado no se guardó

    r = client.delete("/api/songs/uno")
    assert r.status_code == 409 and "Ensayo del viernes" in r.json()["detail"]
    assert client.delete("/api/setlists/ensayo").status_code == 200  # sin engine: no está sonando
    assert client.delete("/api/songs/uno").status_code == 200
    assert library.list_songs() == []


def test_renombrar_cancion(client, tmp_path):
    library.import_song(write(tmp_path / "Obsolete Stimulus click L - foh R.wav", tone(220)))
    slug = library.list_songs()[0]["slug"]
    setlists.create("Ensayo", [slug])

    r = client.patch(f"/api/songs/{slug}", json={"name": "  Obsolete Stimulus  "})
    assert r.status_code == 200 and r.json()["name"] == "Obsolete Stimulus"
    assert r.json()["slug"] == slug  # el slug no cambia: es con lo que la encuentran las set lists
    assert setlists.get("ensayo")["items"][0]["song"] == slug
    assert client.get("/api/songs").json()[0]["name"] == "Obsolete Stimulus"

    assert client.patch(f"/api/songs/{slug}", json={"name": "  "}).status_code == 400
    assert client.patch("/api/songs/nada", json={"name": "X"}).status_code == 404
    store.set_playing(True)
    try:
        assert client.patch(f"/api/songs/{slug}", json={"name": "Otra"}).status_code == 409
    finally:
        store.set_playing(False)
    assert library.get_song(slug)["name"] == "Obsolete Stimulus"


def test_setlist_en_pdf(client, tmp_path):
    library.import_song(write(tmp_path / "Uno.wav", tone(220)), name="Necrópolis")
    setlists.create("Ensayo del viernes", ["necropolis"])
    for mode in ("piso", "tecnica"):
        r = client.get(f"/api/setlists/ensayo-del-viernes/pdf?mode={mode}")
        # octet-stream a propósito: con application/pdf el iPhone lo abre en su visor, y adentro de la web
        # instalada en el inicio eso deja la app trabada, sin botones y sin forma de volver
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
        assert f"ensayo-del-viernes-{mode}.pdf" in r.headers["content-disposition"]
        assert r.content.startswith(b"%PDF") and len(r.content) > 800
    assert client.get("/api/setlists/ensayo-del-viernes/pdf?mode=volar").status_code == 400
    assert client.get("/api/setlists/nada/pdf").status_code == 404


def test_observaciones_por_cancion(client, tmp_path):
    library.import_song(write(tmp_path / "Uno.wav", tone(220)), name="Uno")
    client.post("/api/setlists", json={"name": "Sabado"})
    r = client.put("/api/setlists/sabado", json={"name": "Sabado", "items": [
        {"song": "uno", "note": "  Arranca Juan solo,\n  entramos en el segundo riff  "}]})
    assert r.status_code == 200
    assert r.json()["items"][0]["note"] == "Arranca Juan solo, entramos en el segundo riff"  # una sola línea
    assert client.get("/api/setlists/sabado").json()["items"][0]["note"] == "Arranca Juan solo, entramos en el segundo riff"

    largo = client.put("/api/setlists/sabado", json={"name": "Sabado", "items": [{"song": "uno", "note": "x" * 300}]})
    assert len(largo.json()["items"][0]["note"]) == setlists.NOTE_MAX  # es una línea, no un párrafo
    assert client.get("/api/setlists/sabado/pdf?mode=tecnica").content.startswith(b"%PDF")

    sin = client.put("/api/setlists/sabado", json={"name": "Sabado", "items": [{"song": "uno"}]})
    assert sin.json()["items"][0]["note"] == ""  # sin observación: sigue andando igual


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


NMCLI = {
    ("-t", "-f", "DEVICE,TYPE,STATE", "device", "status"):
        "wlan0:wifi:connected\nlo:loopback:connected (externally)\np2p-dev-wlan0:wifi-p2p:disconnected\n",
    ("-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"): "IP4.ADDRESS[1]:192.168.1.32/24\n",
    ("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"):
        "*:Akasha:79:WPA2\n :Akasha:41:WPA2\n :Sala\\: ensayo:66:WPA2\n ::22:WPA2\n",  # repetida, con ':' y oculta
    ("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"):
        "netplan-wlan0-Akasha:802-11-wireless:wlan0\nnecrotracks-hotspot:802-11-wireless:\nlo:loopback:lo\n",
}


class FakeRun:
    def __init__(self, out=""):
        self.stdout, self.stderr, self.returncode = out, "", 0


def fake_nmcli(calls):
    def run(cmd, **kw):
        calls.append(cmd)
        assert cmd[0] in ("sudo", "nmcli")
        args = cmd[cmd.index("nmcli") + 1:]
        if "--rescan" in args:  # la clave de la tabla es la lista, sin el modo de escaneo
            args = args[:args.index("--rescan")]
        return FakeRun(NMCLI.get(tuple(args), ""))
    return run


def test_wifi(client, monkeypatch):
    calls = []
    monkeypatch.setattr(web.subprocess, "run", fake_nmcli(calls))
    w = client.get("/api/wifi").json()
    # Sin sudo nmcli no escanea: contesta la caché, que puede tener solo la red conectada (visto en la Pi)
    assert ["sudo", "-n", "nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
            "--rescan", "auto"] in calls
    calls.clear()
    client.get("/api/wifi?rescan=1")  # el botón "Buscar redes" fuerza el escaneo
    assert any(c[-2:] == ["--rescan", "yes"] for c in calls)
    assert (w["available"], w["ssid"], w["ip"]) == (True, "Akasha", "192.168.1.32")
    assert [(n["ssid"], n["signal"]) for n in w["networks"]] == [("Akasha", 79), ("Sala: ensayo", 66)]  # sin repetir
    assert w["saved"] == [{"name": "netplan-wlan0-Akasha", "active": True},
                          {"name": "necrotracks-hotspot", "active": False}]
    assert w["hotspot"] is False

    calls.clear()
    assert client.post("/api/wifi", json={"ssid": "Sala", "password": "secreta"}).status_code == 200
    assert calls == [["sudo", "-n", "nmcli", "device", "wifi", "connect", "Sala", "password", "secreta"]]
    assert client.post("/api/wifi", json={"ssid": "  "}).status_code == 400

    # El hotspot es la única red que queda en el escenario si no hay conocida: no se borra desde la web
    assert client.delete("/api/wifi/necrotracks-hotspot").status_code == 409
    assert client.delete("/api/wifi/netplan-wlan0-Akasha").status_code == 200
    assert calls[-1] == ["sudo", "-n", "nmcli", "connection", "delete", "netplan-wlan0-Akasha"]

    store.set_playing(True)
    try:
        assert client.post("/api/wifi", json={"ssid": "Sala"}).status_code == 409
    finally:
        store.set_playing(False)


def test_sin_networkmanager(client, monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("nmcli")

    monkeypatch.setattr(web.subprocess, "run", boom)
    assert client.get("/api/wifi").json() == {"available": False, "networks": [], "saved": []}


def test_sistema_y_apagado(client, monkeypatch):
    store.DATA.mkdir(parents=True, exist_ok=True)
    s = client.get("/api/system").json()
    assert set(s) == {"temp", "throttled", "free", "uptime"} and s["free"] > 0  # en la Mac no hay sensor: None

    calls = []
    monkeypatch.setattr(web.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    assert client.post("/api/power", json={"action": "reboot"}).status_code == 200
    assert calls == [["sudo", "-n", "systemctl", "reboot"]]
    assert client.post("/api/power", json={"action": "volar"}).status_code == 400
    store.set_playing(True)
    try:
        assert client.post("/api/power", json={"action": "off"}).status_code == 409  # nunca mientras suena
    finally:
        store.set_playing(False)
    assert len(calls) == 1


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
