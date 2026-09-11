"""Biblioteca de canciones: importa stems y genera el render de 4 canales.

Cada canción vive en library/<slug>/:
    song.json       nombre, duración, qué canales tiene, avisos
    stems/          los archivos originales, tal como se importaron
    render.wav      4 ch / 48 kHz / 24 bit: FOH L, FOH R, Click, Guía (lo que falte va en silencio)
    automation.mid  opcional

Se importa una canción por vez: una carpeta o un ZIP con archivos nombrados por rol (ver
ROLES), o un único archivo de audio. Un único archivo estéreo es ambiguo y hay que decir qué
es: "click-pista" (L = click, R = pista mono, el formato simple de la banda) o "foh" (pista
estéreo). Adivinarlo mal mandaría el click al PA.
"""
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

from . import store

SAMPLERATE = 48000
RENDER_CHANNELS = ("foh_l", "foh_r", "click", "guia")
BLOCK = SAMPLERATE  # 1 s por bloque al renderizar
AUDIO_EXT = {".wav", ".wave", ".flac", ".aif", ".aiff", ".mp3", ".ogg"}
MIDI_EXT = {".mid", ".midi"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
LAYOUTS = {"click-pista": "click_pista", "foh": "foh"}

# nombre de archivo (sin extensión, normalizado) → rol
ROLES = {
    "foh": "foh", "pista": "foh",
    "foh_l": "foh_l", "pista_l": "foh_l",
    "foh_r": "foh_r", "pista_r": "foh_r",
    "click": "click", "clic": "click",
    "guia": "guia", "guide": "guia",
    "click_pista": "click_pista",
}

CONVENTIONS = (
    "Nombres reconocidos (sin importar mayúsculas ni extensión): foh o pista (mono o estéreo), "
    "foh_L + foh_R (mono), click, guia, click-pista (estéreo: L = click, R = pista) y un .mid "
    "opcional. Un único archivo suelto se importa indicando si es click-pista o foh. Un video (MP4 o MOV, "
    "H.264 hasta 1080p a 30 fps) se muestra por HDMI; si viene sin otro audio, se usa el suyo."
)


class ImportProblem(Exception):
    pass


def render_path(slug):
    return store.library_dir() / slug / "render.wav"


def video_path(slug):
    path = store.library_dir() / slug / "video.mp4"
    return path if path.exists() else None


def get_song(slug):
    return store.read_json(store.library_dir() / slug / "song.json")


def list_songs():
    lib = store.library_dir()
    if not lib.exists():
        return []
    songs = [store.read_json(p) for p in lib.glob("*/song.json") if not p.parent.name.endswith((".new", ".old"))]
    return sorted((s for s in songs if s), key=lambda s: s["name"].lower())


def delete_song(slug):
    shutil.rmtree(store.library_dir() / slug)


def import_song(src, name=None, layout=None, force=False, rate=None):
    """Importa una canción y devuelve su song.json. Si ya existía, la reemplaza recién cuando
    la nueva quedó completa: si algo falla, la anterior queda intacta."""
    if store.is_playing() and not force:
        raise ImportProblem("Está sonando: no se importa durante la reproducción "
                            "(escribir en la SD puede trabar al iRig).")
    if layout is not None and layout not in LAYOUTS:
        raise ImportProblem(f"El formato tiene que ser uno de: {', '.join(LAYOUTS)}")
    src = Path(src)
    if not src.exists():
        raise ImportProblem(f"No existe: {src}")
    name = name or (src.name if src.is_dir() else src.stem)
    slug = store.slugify(name)
    lib = store.library_dir()
    lib.mkdir(parents=True, exist_ok=True)
    new, old, final = lib / f"{slug}.new", lib / f"{slug}.old", lib / slug
    shutil.rmtree(new, ignore_errors=True)
    try:
        song = _build(src, new, name, slug, layout, rate, guard=not force)
    except BaseException as e:
        shutil.rmtree(new, ignore_errors=True)
        if isinstance(e, store.Busy):  # arrancó a sonar a mitad del import
            raise ImportProblem(str(e)) from None
        raise
    shutil.rmtree(old, ignore_errors=True)
    if final.exists():
        final.rename(old)
    new.rename(final)
    shutil.rmtree(old, ignore_errors=True)
    return song


def _norm(stem):
    s = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[\s\-.]+", "_", s).strip("_")


def _members(src):
    """Archivos de entrada como (nombre, abrir): abrir() devuelve un archivo binario."""
    if src.is_dir():
        return [(p.name, lambda p=p: p.open("rb"))
                for p in sorted(src.iterdir()) if p.is_file() and not p.name.startswith(".")]
    if src.suffix.lower() == ".zip":
        zf = zipfile.ZipFile(src)
        return [(Path(i.filename).name, lambda i=i: zf.open(i)) for i in zf.infolist()
                if not i.is_dir() and "__MACOSX" not in i.filename and not Path(i.filename).name.startswith(".")]
    return [(src.name, lambda: src.open("rb"))]


def _build(src, dest, name, slug, layout, rate, guard=False):
    stems = dest / "stems"
    stems.mkdir(parents=True)
    audio, midi, videos, warnings = [], [], [], []
    for fname, opener in _members(src):
        ext = Path(fname).suffix.lower()
        if ext not in AUDIO_EXT | MIDI_EXT | VIDEO_EXT:
            continue
        # El video va aparte, tal cual (se muestra por HDMI); stems/ guarda los originales de audio y MIDI.
        target = dest / "video.mp4" if ext in VIDEO_EXT else stems / fname
        if target.exists():
            raise ImportProblem("Hay más de un video" if ext in VIDEO_EXT else f"Archivo repetido: {fname}")
        with opener() as fi, open(target, "wb") as fo:
            store.copy_stream(fi, fo, rate, guard=guard)
        (videos if ext in VIDEO_EXT else audio if ext in AUDIO_EXT else midi).append(fname)
    video = None
    if videos:
        video, video_warnings = _probe_video(dest / "video.mp4", videos[0])
        warnings += video_warnings
        if not audio:  # sin otro audio se usa el del video, con las mismas reglas que un archivo suelto
            wav = f"{Path(videos[0]).stem}.wav"
            with tempfile.TemporaryDirectory(prefix="necrotracks-") as tmp:  # en RAM: nada de escribir de golpe en la SD
                _extract_audio(dest / "video.mp4", Path(tmp) / wav, videos[0])
                with open(Path(tmp) / wav, "rb") as fi, open(stems / wav, "wb") as fo:
                    store.copy_stream(fi, fo, rate, guard=guard)
            audio.append(wav)
    if not audio:
        raise ImportProblem(f"No hay archivos de audio. {CONVENTIONS}")
    if len(midi) > 1:
        raise ImportProblem(f"Hay más de un MIDI: {', '.join(midi)}")

    roles = _roles(stems, audio, layout)
    infos = {role: sf.info(str(stems / f)) for role, f in roles.items()}
    _validate(roles, infos)

    durations ={role: i.frames / i.samplerate for role, i in infos.items()}
    if max(durations.values()) - min(durations.values()) > 0.5:
        detail = ", ".join(f"{roles[r]} {d:.1f} s" for r, d in durations.items())
        warnings.append(f"Los archivos no tienen la misma duración ({detail}): se completó con silencio")
    rates = sorted({i.samplerate for i in infos.values()})
    if rates != [SAMPLERATE]:
        warnings.append(f"Convertido de {'/'.join(map(str, rates))} Hz a {SAMPLERATE} Hz")

    with tempfile.TemporaryDirectory(prefix="necrotracks-") as tmp:
        sources = {role: stems / f if infos[role].samplerate == SAMPLERATE
                   else _resample(stems / f, Path(tmp) / f"{role}.wav")
                   for role, f in roles.items()}
        frames, peak, rms = _render(sources, dest / "render.wav", rate, guard)

    stereo = "foh_l" in roles or ("foh" in roles and infos["foh"].channels == 2)
    if stereo and rms["foh"] > 1e-6:
        loss = 20 * np.log10(rms["foh"] / max(rms["mono"], 1e-12))
        if loss > 3.5:
            warnings.append(f"La pista pierde {loss:.1f} dB al sumarla a mono: posible problema de fase. "
                            "Escuchala con el perfil mono antes de tocarla.")
    if midi:
        shutil.copyfile(stems / midi[0], dest / "automation.mid")

    has = set(roles)
    song = {
        "name": name,
        "slug": slug,
        "duration": round(frames / SAMPLERATE, 2),
        "foh": "estéreo" if stereo else ("mono" if has & {"foh", "click_pista"} else None),
        "click": bool(has & {"click", "click_pista"}),
        "guia": "guia" in has,
        "midi": bool(midi),
        "video": video,
        "layout": "click-pista" if "click_pista" in has else "stems",
        "sources": roles,
        "original_samplerate": rates,
        "peaks_db": {ch: round(float(20 * np.log10(p)), 1) for ch, p in zip(RENDER_CHANNELS, peak) if p > 0},
        "warnings": warnings,
        "imported": datetime.now().isoformat(timespec="seconds"),
    }
    store.write_json(dest / "song.json", song)
    return song


def _run(args, what):
    try:
        r = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError:
        raise ImportProblem(f"Falta {args[0]} para importar video (bin/bootstrap.sh lo instala)") from None
    if r.returncode:
        raise ImportProblem(f"{what}: {r.stderr.strip()[-300:]}")
    return r.stdout


def _probe_video(path, fname):
    """Datos del video y avisos si la Pi no lo va a poder mostrar bien (decodifica por hardware
    solo H.264, hasta 1080p30)."""
    out = json.loads(_run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=codec_name,width,height,avg_frame_rate", "-of", "json", str(path)],
                          f"No se pudo leer {fname}"))
    if not out.get("streams"):
        raise ImportProblem(f"{fname} no tiene video")
    s = out["streams"][0]
    num, den = (s.get("avg_frame_rate") or "0/1").split("/")
    fps = round(int(num) / int(den), 2) if int(den) else 0.0
    info = {"file": fname, "codec": s["codec_name"], "width": s["width"], "height": s["height"], "fps": fps}
    warnings = []
    if info["codec"] != "h264":
        warnings.append(f"El video es {info['codec']}: la Pi decodifica por hardware solo H.264. "
                        "Exportalo en H.264 o se va a ver trabado.")
    if info["width"] > 1920 or info["height"] > 1080:
        warnings.append(f"El video es de {info['width']}×{info['height']}: la Pi no pasa de 1080p. Exportalo en 1080p o 720p.")
    if fps > 30.5:
        warnings.append(f"El video tiene {fps:g} cuadros por segundo: exportalo a 30 o menos.")
    return info, warnings


def _extract_audio(video, dst, fname):
    _run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(video), "-vn", "-map", "0:a:0",
          "-c:a", "pcm_s24le", "-ar", str(SAMPLERATE), str(dst)], f"No se pudo sacar el audio de {fname} (¿tiene audio?)")


def _roles(stems, audio, layout):
    """rol → nombre de archivo."""
    if len(audio) == 1:
        fname = audio[0]
        role = ROLES.get(_norm(Path(fname).stem))
        if layout:
            role = LAYOUTS[layout]
        elif role is None:
            if sf.info(str(stems / fname)).channels != 1:
                raise ImportProblem(
                    f"'{fname}' es estéreo y no dice qué es. Indicá el formato: click-pista "
                    "(L = click, R = pista mono, el formato simple de la banda) o foh (pista estéreo).")
            role = "foh"
        return {role: fname}
    if layout:
        raise ImportProblem("El formato se indica solo al importar un único archivo")
    roles, unknown = {}, []
    for fname in audio:
        role = ROLES.get(_norm(Path(fname).stem))
        if role is None:
            unknown.append(fname)
        elif role in roles:
            raise ImportProblem(f"Hay dos archivos para '{role}': {roles[role]} y {fname}")
        else:
            roles[role] = fname
    if unknown:
        raise ImportProblem(f"No reconozco: {', '.join(unknown)}. {CONVENTIONS}")
    return roles


def _validate(roles, infos):
    has = set(roles)
    for a, b in (("foh", "foh_l"), ("foh", "foh_r"), ("foh", "click_pista"),
                 ("click_pista", "foh_l"), ("click_pista", "foh_r"), ("click_pista", "click")):
        if a in has and b in has:
            raise ImportProblem(f"No se pueden combinar {roles[a]} y {roles[b]}")
    if ("foh_l" in has) != ("foh_r" in has):
        raise ImportProblem("foh_L y foh_R van juntos: falta uno de los dos")
    for role in ("foh_l", "foh_r"):
        if role in has and infos[role].channels != 1:
            raise ImportProblem(f"{roles[role]} tiene que ser mono")
    if "click_pista" in has and infos["click_pista"].channels != 2:
        raise ImportProblem(f"{roles['click_pista']} tiene que ser estéreo (L = click, R = pista)")
    for role, info in infos.items():
        if info.channels > 2:
            raise ImportProblem(f"{roles[role]} tiene {info.channels} canales: solo mono o estéreo")


def _resample(path, dst):
    import soxr

    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    sf.write(str(dst), soxr.resample(x, sr, SAMPLERATE), SAMPLERATE, subtype="PCM_24")
    return dst


def _render(sources, dst, rate, guard=False):
    files = {role: sf.SoundFile(str(p)) for role, p in sources.items()}
    try:
        frames = max(f.frames for f in files.values())
        peak = np.zeros(4)
        sumsq = np.zeros(3)  # FOH L, FOH R, suma mono
        throttle = store.Throttle(rate, guard)
        with open(dst, "w+b") as fo, sf.SoundFile(fo, "w", SAMPLERATE, 4, subtype="PCM_24", format="WAV") as out:
            for start in range(0, frames, BLOCK):
                n = min(BLOCK, frames - start)
                b = np.zeros((n, 4), np.float32)
                for role, f in files.items():
                    d = f.read(n, dtype="float32", always_2d=True)
                    m = len(d)
                    if not m:
                        continue
                    if role == "foh":
                        b[:m, 0], b[:m, 1] = d[:, 0], d[:, -1]
                    elif role == "foh_l":
                        b[:m, 0] = d[:, 0]
                    elif role == "foh_r":
                        b[:m, 1] = d[:, 0]
                    elif role == "click":
                        b[:m, 2] = d.mean(axis=1)
                    elif role == "guia":
                        b[:m, 3] = d.mean(axis=1)
                    elif role == "click_pista":
                        b[:m, 2], b[:m, 0], b[:m, 1] = d[:, 0], d[:, 1], d[:, 1]
                out.write(b)
                out.flush()
                throttle.wrote(n * 4 * 3, fo)
                peak = np.maximum(peak, np.abs(b).max(axis=0))
                mono = 0.5 * (b[:, 0] + b[:, 1])
                sumsq += [float(b[:, 0] @ b[:, 0]), float(b[:, 1] @ b[:, 1]), float(mono @ mono)]
    finally:
        for f in files.values():
            f.close()
    n = max(frames, 1)
    rms = {"foh": float(np.sqrt((sumsq[0] + sumsq[1]) / 2 / n)), "mono": float(np.sqrt(sumsq[2] / n))}
    return frames, [float(p) for p in peak], rms
