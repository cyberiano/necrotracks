"""Set lists: orden de canciones, bloques y comportamiento de cada una.

Se guardan en setlists/<slug>.json:
    {"name": ..., "slug": ..., "items": [{"song": slug, "behavior": ..., "wait": s, "block": nombre|null,
                                          "note": "observación"}]}

El bloque es solo una etiqueta para la UI: agrupa canciones y, al asignarlo, puede aplicarles
un comportamiento por defecto que después se cambia canción por canción.

La observación es lo que la banda prepara para ese show ("arranca Juan solo", "sample al final"):
va pegada a la canción **dentro de esta set list**, no a la canción de la biblioteca, porque cambia
de show en show. Se ve en letra chica en el reproductor y en las dos hojas impresas.
"""
import soundfile as sf

from . import library, store

BEHAVIORS = {
    "stop": "Detenerse al finalizar",
    "arm_next": "Preparar la siguiente y esperar Play",
    "auto_next": "Reproducir la siguiente",
    "wait": "Esperar N segundos y reproducir la siguiente",
    "repeat": "Repetir la canción",
}
DEFAULT_BEHAVIOR = "arm_next"
NOTE_MAX = 200  # la observación es una línea de letra chica, no un párrafo


def describe(item):
    if item["behavior"] == "wait":
        return f"Esperar {item['wait']:g} s y reproducir la siguiente"
    return BEHAVIORS[item["behavior"]]


def _validate(behavior, wait):
    if behavior not in BEHAVIORS:
        raise ValueError(f"Comportamiento desconocido '{behavior}'. Hay: {', '.join(BEHAVIORS)}")
    if behavior == "wait" and not wait > 0:
        raise ValueError("'wait' necesita una cantidad de segundos mayor que 0")


def make_item(song, behavior=DEFAULT_BEHAVIOR, wait=0, block=None, note=""):
    _validate(behavior, wait)
    # Una línea: en el piso, a la distancia, un párrafo no se lee (y en la hoja no entra)
    note = " ".join(str(note or "").split())[:NOTE_MAX]
    return {"song": song, "behavior": behavior, "wait": float(wait), "block": block, "note": note}


def _path(slug):
    return store.setlists_dir() / f"{slug}.json"


def get(name):
    return store.read_json(_path(store.slugify(name)))


def list_setlists():
    d = store.setlists_dir()
    if not d.exists():
        return []
    return sorted((store.read_json(p) for p in d.glob("*.json")), key=lambda s: s["name"].lower())


def save(setlist):
    store.write_json(_path(setlist["slug"]), setlist)


def delete(name):
    _path(store.slugify(name)).unlink()


def create(name, songs, behavior=DEFAULT_BEHAVIOR, wait=0):
    missing = [s for s in songs if library.get_song(s) is None]
    if missing:
        raise ValueError(f"No están en la biblioteca: {', '.join(missing)}")
    setlist = {"name": name, "slug": store.slugify(name), "items": [make_item(s, behavior, wait) for s in songs]}
    save(setlist)
    return setlist


def set_item(setlist, index, behavior=None, wait=None):
    item = setlist["items"][index]
    behavior = behavior or item["behavior"]
    wait = item["wait"] if wait is None else float(wait)
    _validate(behavior, wait)
    item.update(behavior=behavior, wait=wait)


def assign_block(setlist, block, start, end, behavior=None, wait=0):
    """Etiqueta las canciones start..end (inclusive) con un bloque y, si se indica,
    les aplica un comportamiento por defecto."""
    items = setlist["items"]
    if not 0 <= start <= end < len(items):
        raise IndexError(f"Rango fuera de la set list: {start + 1}..{end + 1} (hay {len(items)} canciones)")
    if behavior:
        _validate(behavior, wait)
    for item in items[start:end + 1]:
        item["block"] = block
        if behavior:
            item.update(behavior=behavior, wait=float(wait))


def move(setlist, src, dst):
    items = setlist["items"]
    items.insert(dst, items.pop(src))


def check(setlist):
    """Chequeo pre-show: problemas que impiden tocar (lista vacía = todo bien)."""
    problems = []
    if not setlist["items"]:
        problems.append("La set list está vacía")
    for i, item in enumerate(setlist["items"], 1):
        song = library.get_song(item["song"])
        if song is None:
            problems.append(f"{i}. '{item['song']}' no está en la biblioteca")
            continue
        try:
            info = sf.info(str(library.render_path(item["song"])))
        except Exception:
            problems.append(f"{i}. {song['name']}: falta el render o no se puede abrir")
            continue
        if info.samplerate != library.SAMPLERATE or info.channels != 4:
            problems.append(f"{i}. {song['name']}: render inválido ({info.samplerate} Hz, {info.channels} canales)")
        try:
            _validate(item["behavior"], item["wait"])
        except ValueError as e:
            problems.append(f"{i}. {song['name']}: {e}")
    return problems
