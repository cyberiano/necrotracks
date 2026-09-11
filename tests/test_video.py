import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from engine import library
from engine.video import LOGO_ZOOM, Video

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="sin ffmpeg")


class FakeMpv:
    def __init__(self):
        self.cmds = []
        self.props = {"time-pos": 0.0, "eof-reached": False}

    def alive(self):
        return True

    def command(self, *args):
        self.cmds.append(args)
        if args[0] == "get_property":
            return self.props.get(args[1])


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def st(state, slug="uno", pos=0.0):
    return {"state": state, "song_slug": slug, "position": pos}


def make(tmp_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    mpv, clock = FakeMpv(), Clock()
    v = Video(None, lambda s: f"/v/{s}.mp4" if s == "uno" else None, logo, delay=0.1, mpv=mpv, clock=clock)
    return v, mpv, clock, logo


def test_logo_cuando_no_suena_video(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    v.step(st("stopped"))
    assert mpv.cmds == [("loadfile", str(logo), "replace", -1, f"video-zoom={LOGO_ZOOM},pause=no")]
    v.step(st("stopped"))
    assert len(mpv.cmds) == 1  # nada nuevo
    v.step(st("playing", slug="dos", pos=3))  # canción sin video: sigue el logo
    assert len(mpv.cmds) == 1


def test_video_sigue_al_audio(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    v.step(st("stopped"))
    v.step(st("playing", pos=0.5))
    # posición 0,5 − latencia 0,1 + 0,33 de adelanto por lo que tarda mpv en arrancar
    assert mpv.cmds[-1] == ("loadfile", "/v/uno.mp4", "replace", -1, "start=0.730,video-zoom=0,pause=no")
    v.step(st("paused", pos=0.8))
    assert mpv.cmds[-1] == ("set_property", "pause", True)
    v.step(st("playing", pos=0.8))
    assert mpv.cmds[-1] == ("set_property", "pause", False)
    v.step(st("stopped"))
    assert mpv.cmds[-1][1] == str(logo)


def test_sincronia(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    v.step(st("playing", pos=0.1))
    n = len(mpv.cmds)
    clock.t += 1.1
    mpv.props["time-pos"] = 10.1 + 0.02  # dentro de la tolerancia: nada
    v.step(st("playing", pos=10.2))
    assert not [c for c in mpv.cmds[n:] if c[0] != "get_property"]
    clock.t += 1.1
    mpv.props["time-pos"] = 20.1 + 0.1  # 100 ms adelantado: frena un poco (ganancia 0,5, tope 3 %)
    v.step(st("playing", pos=20.2))
    assert mpv.cmds[-1] == ("set_property", "speed", 0.97)
    clock.t += 1.1
    mpv.props["time-pos"] = 22.1 + 0.05  # entre 30 y 80 ms: no toca nada (sin ida y vuelta)
    n = len(mpv.cmds)
    v.step(st("playing", pos=22.2))
    assert not [c for c in mpv.cmds[n:] if c[0] != "get_property"]
    clock.t += 1.1
    mpv.props["time-pos"] = 35.0  # muy lejos (p. ej. repeat): salta
    v.step(st("playing", pos=30.2))
    assert ("seek", 30.1, "absolute+exact") in mpv.cmds and mpv.cmds[-1] == ("set_property", "speed", 1.0)


def test_sin_logo_queda_negro(tmp_path):
    mpv = FakeMpv()
    v = Video(None, lambda s: None, tmp_path / "no-existe.png", mpv=mpv)
    v.step(st("stopped"))
    assert mpv.cmds == [("stop",)]


@needs_ffmpeg
def test_logo_aplanado_sobre_negro(tmp_path):
    from engine.video import flatten

    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white@0.0:s=40x20,format=rgba",
                    "-frames:v", "1", str(logo)], check=True)
    out = flatten(logo, tmp_path / "plano.png")
    corner = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "crop=1:1:0:0", "-f", "rawvideo",
                             "-pix_fmt", "rgba", "-"], check=True, capture_output=True).stdout
    assert out.name == "plano.png" and list(corner) == [0, 0, 0, 255]  # negro opaco, no transparente


def make_mp4(path, seconds=1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-f", "lavfi",
                    "-i", "aevalsrc=0.5*sin(2*PI*1000*t)|0.1*sin(2*PI*220*t):s=48000:c=stereo", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


@needs_ffmpeg
def test_import_de_mp4(tmp_path):
    src = make_mp4(tmp_path / "Ritual.mp4")
    with pytest.raises(library.ImportProblem, match="click-pista"):  # estéreo sin decir qué es
        library.import_song(src)
    song = library.import_song(src, layout="click-pista")
    assert song["slug"] == "ritual" and song["click"] and song["foh"] == "mono"
    assert (song["video"]["codec"], song["video"]["width"], song["video"]["height"]) == ("h264", 320, 180)
    assert song["video"]["fps"] == 30 and song["warnings"] == []
    assert library.video_path("ritual").exists()
    x, _ = sf.read(str(library.render_path("ritual")), dtype="float32")
    assert np.abs(x[:, 2]).max() > 3 * np.abs(x[:, 0]).max()  # L del video (el fuerte) fue al click


@needs_ffmpeg
def test_video_con_stems_usa_el_audio_de_los_stems(tmp_path):
    from conftest import tone, write

    src = tmp_path / "Necropolis"
    write(src / "foh.wav", tone(220, seconds=1))
    make_mp4(src / "visuales.mp4")
    song = library.import_song(src)
    assert song["video"]["file"] == "visuales.mp4" and song["sources"] == {"foh": "foh.wav"}
