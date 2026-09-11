"""Web de Necrotracks: configuración (biblioteca, set lists, import) y Show Mode.

Es un proceso aparte del engine: si la web se cae, el show sigue. Los comandos del show van al
engine por su socket (ver engine/daemon.py); la biblioteca y las set lists se leen y escriben
directo en disco. El estado en vivo va por Server-Sent Events: va en una sola dirección, no
necesita dependencias extra y el navegador reconecta solo.

Import: el navegador sube los archivos de a uno (PUT /api/incoming/NOMBRE, cuerpo crudo) a una
carpeta de espera en la SD, con el mismo tope de velocidad que el import, y después pide
POST /api/import. Nada de esto corre mientras suena.
"""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from engine import daemon, library, profiles, setlists, store

STATIC = Path(__file__).parent / "static"
ENGINE_COMMANDS = {"play", "pause", "play_pause", "stop", "next", "prev", "goto", "load"}
PLAYING = "Está sonando: no se suben ni se importan archivos durante la reproducción."
UPLOAD_CHUNK = 512 * 1024
RETRY = 2  # segundos entre reintentos contra el engine
PING = 10  # segundos sin novedades del engine → ping, para que el navegador sepa que la web vive

app = FastAPI(title="Necrotracks", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_importing = asyncio.Lock()


class Cmd(BaseModel):
    cmd: str
    index: int | None = None
    setlist: str | None = None


class NewSetlist(BaseModel):
    name: str


class Item(BaseModel):
    song: str
    behavior: str = setlists.DEFAULT_BEHAVIOR
    wait: float = 0
    block: str | None = None
    note: str = ""


class SetlistIn(BaseModel):
    name: str
    items: list[Item]


class ImportIn(BaseModel):
    name: str | None = None
    layout: str | None = None


def incoming_dir():
    return store.DATA / "incoming"


# ── Engine ─────────────────────────────────────────────────────────────────────

async def engine_request(req, timeout=3):
    try:
        reader, writer = await asyncio.open_unix_connection(str(daemon.socket_path()))
    except OSError:
        raise HTTPException(503, "El engine no está corriendo (¿el iRig está enchufado?)") from None
    try:
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout)
    except (OSError, asyncio.TimeoutError):
        line = b""
    finally:
        writer.close()
    if not line:
        raise HTTPException(503, "El engine no responde")
    return json.loads(line)


async def engine_state():
    try:
        return (await engine_request({"cmd": "state"}))["state"]
    except HTTPException:
        return None


def _sse(msg, event=None):
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {json.dumps(msg, ensure_ascii=False)}\n\n"


@app.get("/api/events")
async def events():
    async def stream():
        while True:
            try:
                reader, writer = await asyncio.open_unix_connection(str(daemon.socket_path()))
            except OSError:
                yield _sse({"engine": False})
                await asyncio.sleep(RETRY)
                continue
            try:
                writer.write(b'{"cmd": "subscribe"}\n')
                await writer.drain()
                while True:
                    try:
                        line = await asyncio.wait_for(reader.readline(), PING)
                    except asyncio.TimeoutError:
                        yield _sse({}, "ping")
                        continue
                    if not line:
                        break
                    yield _sse({"engine": True, "state": json.loads(line)["state"]})
            except OSError:
                pass
            finally:
                writer.close()
            yield _sse({"engine": False})
            await asyncio.sleep(RETRY)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/state")
async def get_state():
    state = await engine_state()
    return {"engine": False} if state is None else {"engine": True, "state": state}


@app.post("/api/cmd")
async def post_cmd(body: Cmd):
    if body.cmd not in ENGINE_COMMANDS:
        raise HTTPException(400, f"Comando desconocido: {body.cmd}")
    req = {"cmd": body.cmd}
    if body.cmd == "goto":
        req["index"] = body.index
    elif body.cmd == "load":
        req["setlist"] = body.setlist
    resp = await engine_request(req)
    if not resp.get("ok"):
        raise HTTPException(409, resp.get("error") or "El engine rechazó el comando")
    return resp


class ActionIn(BaseModel):
    action: str


def _ok(resp):
    if not resp.get("ok"):
        raise HTTPException(409, resp.get("error") or "El engine rechazó el pedido")
    return resp


@app.get("/api/controls")
async def get_controls():
    return _ok(await engine_request({"cmd": "controls"}))["controls"]


@app.post("/api/learn")
async def post_learn(body: ActionIn):
    return _ok(await engine_request({"cmd": "learn", "action": body.action}, timeout=daemon.LEARN_TIMEOUT + 5))


@app.post("/api/unlearn")
async def post_unlearn(body: ActionIn):
    return _ok(await engine_request({"cmd": "unlearn", "action": body.action}))


class OutputIn(BaseModel):
    profile: str
    fallback: str | None = None


@app.get("/api/output")
async def get_output():
    return _ok(await engine_request({"cmd": "output"}))


@app.post("/api/output")
async def post_output(body: OutputIn):
    return _ok(await engine_request({"cmd": "set_output", "profile": body.profile, "fallback": body.fallback}))


class HdmiIn(BaseModel):
    mode: str = "auto"


@app.get("/api/hdmi")
async def get_hdmi():
    return _ok(await engine_request({"cmd": "hdmi"}))


@app.post("/api/hdmi")
async def post_hdmi(body: HdmiIn):
    return _ok(await engine_request({"cmd": "set_hdmi", "mode": body.mode}))


class FitIn(BaseModel):
    mode: str = "fit"
    scale: float | None = None  # una sola escala: de antes de que ancho y alto fueran por separado
    scale_x: float = 100
    scale_y: float = 100
    x: float = 0
    y: float = 0
    pattern: bool | None = None


def _fit(body):
    f = {"mode": body.mode, "scale_x": body.scale_x, "scale_y": body.scale_y, "x": body.x, "y": body.y}
    if body.scale is not None:
        f["scale_x"] = f["scale_y"] = body.scale
    return f


@app.post("/api/fit")
async def post_fit(body: FitIn):
    req = {"cmd": "set_fit", "fit": _fit(body)}
    if body.pattern is not None:
        req["pattern"] = body.pattern
    return _ok(await engine_request(req))


@app.post("/api/idle-fit")
async def post_idle_fit(body: FitIn):
    return _ok(await engine_request({"cmd": "set_idle_fit", "fit": _fit(body)}))


# ── Red WiFi ───────────────────────────────────────────────────────────────────
# Todo por NetworkManager (nmcli), que es quien maneja la red en la Pi. Cambiar de red la saca de la
# actual: la web se corta y hay que buscarla en la red nueva. Si no engancha ninguna conocida, al
# reiniciar vuelve el hotspot (necrotracks-hotspot.service), que por eso nunca se borra desde acá.

HOTSPOT = "necrotracks-hotspot"
WIFI_CONNECT_TIMEOUT = 45
WIFI_SCAN_TIMEOUT = 30  # un escaneo forzado tarda unos segundos


def _terse(line):
    r"""Una línea de `nmcli -t`: campos separados por ':', con '\:' cuando el valor trae uno."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur, esc = cur + ch, False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [*out, cur]


def _nmcli(*args, sudo=False, timeout=15):
    cmd = (["sudo", "-n"] if sudo else []) + ["nmcli", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


def _fields(line, n):
    return (_terse(line) + [""] * n)[:n]


def _wifi_device():
    for line in _nmcli("-t", "-f", "DEVICE,TYPE,STATE", "device", "status").splitlines():
        dev, kind, state = _fields(line, 3)
        if kind == "wifi" and not dev.startswith("p2p"):
            return dev, state
    return None, None


def _wifi_ip(device):
    for line in _nmcli("-t", "-f", "IP4.ADDRESS", "device", "show", device).splitlines():
        parts = _terse(line)
        if len(parts) > 1 and parts[1]:
            return parts[1].split("/")[0]
    return None


@app.get("/api/wifi")
def get_wifi(rescan: bool = False):
    try:
        device, state = _wifi_device()
    except (OSError, subprocess.SubprocessError):
        device = None  # sin NetworkManager (p. ej. la Mac)
    if not device:
        return {"available": False, "networks": [], "saved": []}
    # Sin sudo, nmcli NO escanea: contesta lo que tiene en caché, que puede ser solo la red conectada
    # (probado en la Pi: sin sudo, 1 red; con sudo y escaneo, 10). "yes" = escanear ahora (tarda unos
    # segundos, es el botón "Buscar redes"); "auto" = escanear si la caché está vieja.
    listing = _nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                     "--rescan", "yes" if rescan else "auto", sudo=True, timeout=WIFI_SCAN_TIMEOUT)
    best, ssid = {}, None
    for line in listing.splitlines():
        use, name, signal, security = _fields(line, 4)
        if not name:
            continue  # redes ocultas: no se listan
        if use == "*":
            ssid = name
        net = {"ssid": name, "signal": int(signal or 0), "security": security or "abierta", "in_use": use == "*"}
        if net["signal"] >= best.get(name, {}).get("signal", -1):  # la misma red puede venir de varias antenas
            best[name] = net
    saved = [{"name": name, "active": dev == device}
             for name, kind, dev in (_fields(line, 3) for line in
                                     _nmcli("-t", "-f", "NAME,TYPE,DEVICE", "connection", "show").splitlines())
             if kind == "802-11-wireless"]
    return {"available": True, "device": device, "state": state, "ssid": ssid, "ip": _wifi_ip(device),
            "hotspot": any(s["name"] == HOTSPOT and s["active"] for s in saved),
            "networks": sorted(best.values(), key=lambda n: -n["signal"]), "saved": saved}


class WifiIn(BaseModel):
    ssid: str
    password: str | None = None


@app.post("/api/wifi")
async def post_wifi(body: WifiIn):
    """Conecta la Pi a otra red. La contraseña no se guarda ni se registra acá: va derecho a nmcli."""
    ssid = body.ssid.strip()
    if not ssid:
        raise HTTPException(400, "Poné el nombre de la red")
    if store.is_playing():
        raise HTTPException(409, "Está sonando: cambiá de red con la reproducción parada")
    args = ["device", "wifi", "connect", ssid] + (["password", body.password] if body.password else [])
    try:
        await run_in_threadpool(lambda: _nmcli(*args, sudo=True, timeout=WIFI_CONNECT_TIMEOUT))
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, (e.stderr or "").strip()[-200:] or f"No se pudo conectar a '{ssid}'") from None
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, "nmcli no respondió: la Pi puede estar cambiando de red") from None
    return {"ok": True}


@app.delete("/api/wifi/{name}")
def delete_wifi(name: str):
    """Olvida una red guardada. El hotspot no: es lo único que queda si en el escenario no hay red conocida."""
    if name == HOTSPOT:
        raise HTTPException(409, "El hotspot Necrotracks no se borra: es la red de respaldo")
    if store.is_playing():
        raise HTTPException(409, "Está sonando: tocá la red con la reproducción parada")
    try:
        _nmcli("connection", "delete", name, sudo=True)
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(400, f"No se pudo borrar '{name}'") from None
    return {"ok": True}


# ── Sistema (temperatura y apagado) ────────────────────────────────────────────

POWER = {"off": "poweroff", "reboot": "reboot"}


class PowerIn(BaseModel):
    action: str


def _temperature():
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None  # no es una Pi (p. ej. la Mac)


def _throttled():
    """La bandera de la Pi: 0 = nunca le faltó tensión ni recortó por calor. None si no se puede leer."""
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2)
        return int(out.stdout.split("=")[1], 16)
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


def _uptime():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _free():
    try:
        return shutil.disk_usage(store.DATA).free
    except OSError:
        return None


@app.get("/api/system")
def get_system():
    return {"temp": _temperature(), "throttled": _throttled(), "free": _free(), "uptime": _uptime()}


@app.post("/api/power")
def post_power(body: PowerIn):
    """Apaga o reinicia la Pi. Nunca mientras suena: cortaría el show."""
    if body.action not in POWER:
        raise HTTPException(400, f"Acción desconocida: {body.action}")
    if store.is_playing():
        raise HTTPException(409, "Está sonando: frená la reproducción primero")
    try:
        subprocess.run(["sudo", "-n", "systemctl", POWER[body.action]], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise HTTPException(500, f"No se pudo {'apagar' if body.action == 'off' else 'reiniciar'}: {e}") from None
    return {"ok": True}


@app.get("/api/info")
def get_info():
    return {
        "behaviors": setlists.BEHAVIORS,
        "default_behavior": setlists.DEFAULT_BEHAVIOR,
        "layouts": list(library.LAYOUTS),
        "conventions": library.CONVENTIONS,
        "profile": daemon.load_config()["profile"],
        "profiles": list(profiles.PROFILES),
    }


# ── Biblioteca ─────────────────────────────────────────────────────────────────

def _usage():
    used = {}
    for sl in setlists.list_setlists():
        for item in sl["items"]:
            used.setdefault(item["song"], []).append(sl["name"])
    return {slug: sorted(set(names)) for slug, names in used.items()}


@app.get("/api/songs")
def get_songs():
    used = _usage()
    return [{**s, "used_in": used.get(s["slug"], [])} for s in library.list_songs()]


class SongIn(BaseModel):
    name: str


@app.patch("/api/songs/{slug}")
def patch_song(slug: str, body: SongIn):
    """Renombra una canción. El slug no cambia: es con lo que la encuentran las set lists y el video."""
    song = library.get_song(slug)
    if song is None:
        raise HTTPException(404, "No existe esa canción")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "La canción necesita un nombre")
    if store.is_playing():
        raise HTTPException(409, "Está sonando: renombrá con la reproducción parada")
    song["name"] = name
    store.write_json(store.library_dir() / slug / "song.json", song)
    return song


@app.delete("/api/songs/{slug}")
def delete_song(slug: str):
    if library.get_song(slug) is None:
        raise HTTPException(404, "No existe esa canción")
    if store.is_playing():
        raise HTTPException(409, "Está sonando: no se borran canciones durante la reproducción")
    used = _usage().get(slug)
    if used:
        raise HTTPException(409, f"Está en: {', '.join(used)}. Sacala de esas set lists primero.")
    library.delete_song(slug)
    return {"ok": True}


def _write(fo, data, throttle):
    fo.write(data)
    throttle.wrote(len(data), fo)


@app.delete("/api/incoming")
def clear_incoming():
    shutil.rmtree(incoming_dir(), ignore_errors=True)
    return {"ok": True}


async def _save_upload(request, path):
    """Guarda el cuerpo crudo del pedido en `path` con el tope de escritura. Si arranca a sonar, corta (409)."""
    throttle = store.Throttle(guard=True)
    buf = bytearray()
    try:
        with open(path, "wb") as fo:
            async for chunk in request.stream():
                buf += chunk
                if len(buf) >= UPLOAD_CHUNK:
                    await run_in_threadpool(_write, fo, bytes(buf), throttle)
                    buf.clear()
            if buf:
                await run_in_threadpool(_write, fo, bytes(buf), throttle)
    except store.Busy as e:
        path.unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from None


@app.put("/api/incoming/{filename}")
async def put_incoming(filename: str, request: Request):
    name = Path(filename).name
    if not name or name.startswith("."):
        raise HTTPException(400, f"Nombre de archivo inválido: {filename}")
    if store.is_playing():
        raise HTTPException(409, PLAYING)
    d = incoming_dir()
    d.mkdir(parents=True, exist_ok=True)
    await _save_upload(request, d / name)
    return {"ok": True, "size": (d / name).stat().st_size}


# ── Pantalla de reposo (lo que muestra el HDMI cuando no suena un video) ──────

IDLE_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def idle_dir():
    return store.DATA / "reposo"


@app.put("/api/idle/{filename}")
async def put_idle(filename: str, request: Request):
    """Reemplaza la pantalla de reposo por una imagen o un video (queda uno solo)."""
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if not name or name.startswith(".") or ext not in IDLE_IMAGE_EXT | library.VIDEO_EXT:
        raise HTTPException(400, "Tiene que ser una imagen (PNG, JPG, WebP) o un video (MP4, MOV)")
    if store.is_playing():
        raise HTTPException(409, PLAYING)
    d = idle_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".subiendo{ext}"
    await _save_upload(request, tmp)
    warnings = []
    if ext in library.VIDEO_EXT:
        try:
            _, warnings = await run_in_threadpool(library._probe_video, tmp, name)
        except library.ImportProblem as e:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, str(e)) from None
    for p in d.iterdir():
        if p != tmp:
            p.unlink()
    tmp.rename(d / name)
    return {"ok": True, "file": name, "warnings": warnings}


@app.delete("/api/idle")
def delete_idle():
    """Vuelve al logo."""
    shutil.rmtree(idle_dir(), ignore_errors=True)
    return {"ok": True}


@app.post("/api/import")
async def post_import(body: ImportIn):
    if _importing.locked():
        raise HTTPException(409, "Ya hay un import en curso")
    async with _importing:
        d = incoming_dir()
        files = sorted(p for p in d.iterdir() if p.is_file()) if d.exists() else []
        if not files:
            raise HTTPException(400, "No hay archivos subidos")
        name = (body.name or "").strip() or None
        if len(files) > 1 and not name:
            raise HTTPException(400, "Son varios archivos: poné el nombre de la canción")
        src = files[0] if len(files) == 1 else d
        try:
            song = await run_in_threadpool(library.import_song, src, name=name, layout=body.layout or None)
        except library.ImportProblem as e:
            raise HTTPException(400, str(e)) from None  # los archivos quedan: se puede reintentar con otro formato
        except Exception as e:
            raise HTTPException(400, f"No se pudo importar: {e}") from None
        shutil.rmtree(d, ignore_errors=True)
        return song


# ── Set lists ──────────────────────────────────────────────────────────────────

def _get_setlist(slug):
    sl = setlists.get(slug)
    if sl is None:
        raise HTTPException(404, "No existe esa set list")
    return sl


def _setlist_view(sl):
    songs = {slug: library.get_song(slug) for slug in {i["song"] for i in sl["items"]}}
    return {**sl,
            "songs": {k: {"name": v["name"], "duration": v["duration"]} if v else None for k, v in songs.items()},
            "problems": setlists.check(sl)}


@app.get("/api/setlists")
def get_setlists():
    out = []
    for sl in setlists.list_setlists():
        durations = [(library.get_song(i["song"]) or {}).get("duration", 0) for i in sl["items"]]
        out.append({"name": sl["name"], "slug": sl["slug"], "count": len(sl["items"]),
                    "duration": sum(durations), "problems": setlists.check(sl)})
    return out


@app.post("/api/setlists")
def post_setlist(body: NewSetlist):
    name = body.name.strip()
    try:
        slug = store.slugify(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if setlists.get(slug):
        raise HTTPException(409, f"Ya existe una set list '{slug}'")
    return _setlist_view(setlists.create(name, []))


@app.get("/api/setlists/{slug}")
def get_setlist(slug: str):
    return _setlist_view(_get_setlist(slug))


@app.put("/api/setlists/{slug}")
def put_setlist(slug: str, body: SetlistIn):
    sl = _get_setlist(slug)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "La set list necesita un nombre")
    missing = sorted({i.song for i in body.items if library.get_song(i.song) is None})
    if missing:
        raise HTTPException(400, f"No están en la biblioteca: {', '.join(missing)}")
    try:
        items = [setlists.make_item(i.song, i.behavior, i.wait, (i.block or "").strip() or None, i.note)
                 for i in body.items]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    sl.update(name=name, items=items)  # el slug no cambia al renombrar
    setlists.save(sl)
    return _setlist_view(sl)


@app.get("/api/setlists/{slug}/pdf")
def get_setlist_pdf(slug: str, mode: str = "piso"):
    """La set list en PDF. Desde la Mac alcanza con imprimir la hoja; en el iPhone, la web instalada en el
    inicio no tiene la función de imprimir de Safari, así que el archivo lo tiene que armar la Pi."""
    if mode not in ("piso", "tecnica"):
        raise HTTPException(400, f"Hoja desconocida: {mode}")
    sl = _setlist_view(_get_setlist(slug))
    try:
        from .pdf import setlist_pdf
    except ImportError:  # falta fpdf2 (deploy viejo): que se entienda, en vez de un 500
        raise HTTPException(503, "Falta la librería de PDF en la Pi: corré bin/deploy.sh sin --fast") from None
    # octet-stream a propósito: con application/pdf, el iPhone lo abre en su visor. Adentro de la web
    # instalada en el inicio eso es una trampa: ocupa toda la pantalla, sin botones y sin forma de volver
    # (hay que cerrar la app). Así el navegador lo baja en vez de mostrarlo.
    return Response(setlist_pdf(sl, mode, setlists.BEHAVIORS), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-{mode}.pdf"'})


@app.delete("/api/setlists/{slug}")
async def delete_setlist(slug: str):
    _get_setlist(slug)
    state = await engine_state()
    if state and state["slug"] == slug and state["state"] != "stopped":
        raise HTTPException(409, "Es la set list que está sonando")
    setlists.delete(slug)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/manifest.webmanifest")
def manifest():  # para instalarla como app; el tipo MIME no lo adivina StaticFiles
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")
