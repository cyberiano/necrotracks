"""CLI de Necrotracks, hasta que exista la web.

    python -m engine.cli import CARPETA|ZIP|ARCHIVO [--name N] [--layout click-pista|foh] [--force]
    python -m engine.cli songs
    python -m engine.cli setlists
    python -m engine.cli setlist new NOMBRE CANCION... [--behavior B] [--wait S]
    python -m engine.cli setlist show NOMBRE
    python -m engine.cli setlist check NOMBRE
    python -m engine.cli setlist set NOMBRE N [--behavior B] [--wait S]
    python -m engine.cli setlist block NOMBRE BLOQUE DESDE HASTA [--behavior B] [--wait S]
    python -m engine.cli show NOMBRE [--profile P] [--device D]      tocar la set list con el teclado
    python -m engine.cli play ARCHIVO... [--profile P] [--seconds N]  prueba de audio (soak)

Las canciones se nombran por su slug (ver `songs`). Los números de canción empiezan en 1.
"""
import argparse
import logging
import os
import sys
import threading
import time

from . import library, profiles, setlists
from .show import Show

STATE_LABEL = {"stopped": "PARADO", "playing": "SONANDO", "paused": "PAUSA", "waiting": "ESPERA"}


def _mmss(seconds):
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def _die(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def cmd_import(a):
    try:
        song = library.import_song(a.src, name=a.name, layout=a.layout, force=a.force)
    except library.ImportProblem as e:
        _die(f"No se importó: {e}")
    yes = lambda v: "sí" if v else "no"
    print(f"Importada: {song['name']} ({song['slug']}), {_mmss(song['duration'])}")
    print(f"  pista: {song['foh'] or '-'} | click: {yes(song['click'])} | guía: {yes(song['guia'])} | MIDI: {yes(song['midi'])}")
    for w in song["warnings"]:
        print(f"  ⚠ {w}")


def cmd_songs(a):
    songs = library.list_songs()
    if not songs:
        print("La biblioteca está vacía.")
    for s in songs:
        flags = " ".join(n for n, on in (("click", s["click"]), ("guía", s["guia"]), ("MIDI", s["midi"])) if on)
        warn = f"  ⚠ {len(s['warnings'])} aviso(s)" if s["warnings"] else ""
        print(f"{s['slug']:<28} {_mmss(s['duration']):>6}  pista {s['foh'] or '-':<8} {flags:<16} {s['name']}{warn}")


def cmd_setlists(a):
    for sl in setlists.list_setlists():
        print(f"{sl['slug']:<28} {len(sl['items']):>3} canciones   {sl['name']}")


def _load(name):
    sl = setlists.get(name)
    if sl is None:
        _die(f"No existe la set list '{name}'")
    return sl


def _print_setlist(sl):
    print(f"{sl['name']}  ({len(sl['items'])} canciones)")
    block = object()
    for i, item in enumerate(sl["items"], 1):
        if item["block"] != block:
            block = item["block"]
            print(f"  ── {block or 'sin bloque'}")
        song = library.get_song(item["song"])
        name = song["name"] if song else f"{item['song']} (¡no está en la biblioteca!)"
        print(f"  {i:>2}. {name:<32} {setlists.describe(item)}")


def cmd_setlist_new(a):
    try:
        sl = setlists.create(a.name, a.songs, a.behavior, a.wait)
    except ValueError as e:
        _die(str(e))
    _print_setlist(sl)


def cmd_setlist_show(a):
    _print_setlist(_load(a.name))


def cmd_setlist_check(a):
    problems = setlists.check(_load(a.name))
    for p in problems:
        print(f"  ✗ {p}")
    if problems:
        sys.exit(1)
    print("✓ Lista para tocar")


def cmd_setlist_set(a):
    sl = _load(a.name)
    try:
        setlists.set_item(sl, a.n - 1, a.behavior, a.wait)
    except (ValueError, IndexError) as e:
        _die(str(e))
    setlists.save(sl)
    _print_setlist(sl)


def cmd_setlist_block(a):
    sl = _load(a.name)
    try:
        setlists.assign_block(sl, a.block, a.start - 1, a.end - 1, a.behavior, a.wait)
    except (ValueError, IndexError) as e:
        _die(str(e))
    setlists.save(sl)
    _print_setlist(sl)


def _print_state(s):
    pos = f" {_mmss(s['position'])}/{_mmss(s['duration'])}" if s["state"] in ("playing", "paused") else ""
    wait = f" (arranca en {s['wait_remaining']:.0f} s)" if s["wait_remaining"] is not None else ""
    nxt = f"  → próxima: {s['next']}" if s["next"] else ""
    block = f"[{s['block']}] " if s["block"] else ""
    print(f"{STATE_LABEL[s['state']]:<8} {s['index'] + 1}/{s['count']} {block}{s['song']}{pos}{wait}{nxt}", flush=True)


def cmd_show(a):
    from .audio import Player

    sl = _load(a.name)
    problems = setlists.check(sl)
    if problems:
        for p in problems:
            print(f"  ✗ {p}")
        _die("La set list tiene problemas: corregilos antes de tocar.")
    prof = profiles.get(a.profile)
    player = Player(a.device or prof["device"], prof["matrix"])
    songs = {item["song"]: library.get_song(item["song"]) for item in sl["items"]}
    show = Show(player, sl, songs, library.render_path, on_change=_print_state)
    show.start()
    print("Enter = play/pausa · s = stop · n = siguiente · p = anterior · g N = ir a la N · ? = estado · q = salir")
    _print_state(show.snapshot())
    keys = {"": "play_pause", "s": "stop", "n": "next", "p": "prev"}
    for line in sys.stdin:
        line = line.strip().lower()
        if line == "q":
            break
        if line == "?":
            _print_state(show.snapshot())
        elif line.startswith("g ") and line[2:].strip().isdigit():
            show.send("goto", int(line[2:]) - 1)
        elif line in keys:
            show.send(keys[line])
    show.send("stop")
    time.sleep(0.3)
    player.close()


def cmd_play(a):
    from .audio import SAMPLERATE, Player

    prof = profiles.get(a.profile)
    done = threading.Event()
    player = Player(a.device or prof["device"], prof["matrix"])
    pending = list(a.files)

    def next_song():
        if not pending:
            done.set()
            return
        path = pending.pop(0)
        print(f"== {time.strftime('%H:%M:%S')} ▶ {os.path.basename(path)}", flush=True)
        player.play(path)

    player.on_end = next_song
    next_song()
    t0 = last = time.monotonic()
    while not done.wait(1):
        elapsed = time.monotonic() - t0
        if time.monotonic() - last >= 10:
            last = time.monotonic()
            print(f"[{elapsed:7.1f}s] pos={player.position / SAMPLERATE:7.1f}s "
                  f"xruns={player.underflows} starved={player.starved} load={os.getloadavg()[0]:.2f}", flush=True)
        if a.seconds and elapsed >= a.seconds:
            player.stop()
            time.sleep(0.5)
            break
    player.close()
    print(f"FIN pos={player.position / SAMPLERATE:.1f}s xruns={player.underflows} starved={player.starved}")


def _behavior_args(p, behavior, wait):
    p.add_argument("--behavior", choices=list(setlists.BEHAVIORS), default=behavior,
                   help="; ".join(f"{k} = {v}" for k, v in setlists.BEHAVIORS.items()))
    p.add_argument("--wait", type=float, default=wait, help="segundos de espera (con --behavior wait)")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="necrotracks")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="importar una canción (carpeta, ZIP o archivo)")
    p.add_argument("src")
    p.add_argument("--name", help="nombre de la canción (por defecto, el de la carpeta o archivo)")
    p.add_argument("--layout", choices=list(library.LAYOUTS),
                   help="para un único archivo estéreo: click-pista (L = click, R = pista) o foh (pista estéreo)")
    p.add_argument("--force", action="store_true", help="importar aunque esté sonando (no recomendado)")
    p.set_defaults(fn=cmd_import)

    sub.add_parser("songs", help="listar la biblioteca").set_defaults(fn=cmd_songs)
    sub.add_parser("setlists", help="listar las set lists").set_defaults(fn=cmd_setlists)

    sp = sub.add_parser("setlist", help="crear y editar set lists").add_subparsers(dest="sub", required=True)
    p = sp.add_parser("new")
    p.add_argument("name")
    p.add_argument("songs", nargs="+")
    _behavior_args(p, setlists.DEFAULT_BEHAVIOR, 0)
    p.set_defaults(fn=cmd_setlist_new)
    for name, fn in (("show", cmd_setlist_show), ("check", cmd_setlist_check)):
        p = sp.add_parser(name)
        p.add_argument("name")
        p.set_defaults(fn=fn)
    p = sp.add_parser("set", help="cambiar el comportamiento de la canción N")
    p.add_argument("name")
    p.add_argument("n", type=int)
    _behavior_args(p, None, None)
    p.set_defaults(fn=cmd_setlist_set)
    p = sp.add_parser("block", help="agrupar las canciones DESDE..HASTA en un bloque")
    p.add_argument("name")
    p.add_argument("block")
    p.add_argument("start", type=int)
    p.add_argument("end", type=int)
    _behavior_args(p, None, 0)
    p.set_defaults(fn=cmd_setlist_block)

    for name, fn in (("show", cmd_show), ("play", cmd_play)):
        p = sub.add_parser(name)
        if name == "show":
            p.add_argument("name")
        else:
            p.add_argument("files", nargs="+")
            p.add_argument("--seconds", type=float, help="cortar a los N segundos (con fade)")
        p.add_argument("--profile", default=profiles.DEFAULT, choices=list(profiles.PROFILES))
        p.add_argument("--device", help="pisar el device del perfil")
        p.set_defaults(fn=fn)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
