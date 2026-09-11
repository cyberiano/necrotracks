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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
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
    scale: float = 100
    x: float = 0
    y: float = 0
    pattern: bool | None = None


@app.post("/api/fit")
async def post_fit(body: FitIn):
    req = {"cmd": "set_fit", "fit": {"mode": body.mode, "scale": body.scale, "x": body.x, "y": body.y}}
    if body.pattern is not None:
        req["pattern"] = body.pattern
    return _ok(await engine_request(req))


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


@app.put("/api/incoming/{filename}")
async def put_incoming(filename: str, request: Request):
    name = Path(filename).name
    if not name or name.startswith("."):
        raise HTTPException(400, f"Nombre de archivo inválido: {filename}")
    if store.is_playing():
        raise HTTPException(409, PLAYING)
    d = incoming_dir()
    d.mkdir(parents=True, exist_ok=True)
    throttle = store.Throttle(guard=True)
    buf = bytearray()
    try:
        with open(d / name, "wb") as fo:
            async for chunk in request.stream():
                buf += chunk
                if len(buf) >= UPLOAD_CHUNK:
                    await run_in_threadpool(_write, fo, bytes(buf), throttle)
                    buf.clear()
            if buf:
                await run_in_threadpool(_write, fo, bytes(buf), throttle)
    except store.Busy as e:
        (d / name).unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from None
    return {"ok": True, "size": (d / name).stat().st_size}


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
        items = [setlists.make_item(i.song, i.behavior, i.wait, (i.block or "").strip() or None) for i in body.items]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    sl.update(name=name, items=items)  # el slug no cambia al renombrar
    setlists.save(sl)
    return _setlist_view(sl)


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
