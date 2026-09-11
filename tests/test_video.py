import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from engine import library
from engine.video import IDLE_FIT_DEFAULT, Video, fit_opts

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="sin ffmpeg")


class FakeMpv:
    def __init__(self):
        self.cmds = []
        self.props = {"time-pos": 0.0, "eof-reached": False}
        self.overlays = []

    def alive(self):
        return True

    def command(self, *args):
        self.cmds.append(args)
        if args[0] == "loadfile":
            self.props["path"] = args[1]
        if args[0] == "get_property":
            return self.props.get(args[1])

    def overlay(self, opacity):
        self.overlays.append((round(opacity, 2), self.props.get("path")))


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
    v = Video(None, lambda s: f"/v/{s}.mp4" if s == "uno" else None, lambda: logo, delay=0.1, mpv=mpv, clock=clock,
              fade=0)
    return v, mpv, clock, logo


def test_logo_cuando_no_suena_video(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    v.step(st("stopped"))
    assert mpv.cmds == [("loadfile", str(logo), "replace", -1, f"{fit_opts(IDLE_FIT_DEFAULT)},pause=no")]
    assert "video-scale-x=0.54,video-scale-y=0.54," in mpv.cmds[0][4]  # 54 %: como se veía el logo
    v.step(st("stopped"))
    assert len(mpv.cmds) == 1  # nada nuevo
    v.step(st("playing", slug="dos", pos=3))  # canción sin video: sigue el logo
    assert len(mpv.cmds) == 1


def test_video_sigue_al_audio(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    v.step(st("stopped"))
    v.step(st("playing", pos=0.5))
    # posición 0,5 − latencia 0,1 + 0,33 de adelanto por lo que tarda mpv en arrancar
    assert mpv.cmds[-1] == ("loadfile", "/v/uno.mp4", "replace", -1,
                            "start=0.730,keepaspect=yes,panscan=0,video-zoom=0,video-scale-x=1,video-scale-y=1,"
                            "video-pan-x=0,video-pan-y=0,background-color=#00000000,pause=no")
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
    v = Video(None, lambda s: None, lambda: None, mpv=mpv, fade=0)
    v.step(st("stopped"))
    assert mpv.cmds == [("stop",)]


def test_fundido_a_negro_al_cambiar(tmp_path, monkeypatch):
    import engine.video as video

    v, mpv, clock, logo = make(tmp_path)
    monkeypatch.setattr(video.time, "sleep", lambda s: setattr(clock, "t", clock.t + s))
    v.fade = 0.5
    v.step(st("stopped"))  # recién levantado: el logo aparece desde negro
    assert mpv.overlays[0] == (1.0, None) and mpv.overlays[-1] == (0.0, str(logo)) and len(mpv.overlays) == 11
    mpv.overlays.clear()
    v.step(st("playing", pos=0.5))
    # el cambio de archivo pasa con la pantalla en negro: nunca se ve el reposo agrandándose ni la consola
    assert mpv.overlays[:10] == [(round(i / 10, 2), str(logo)) for i in range(1, 11)]
    assert mpv.overlays[10] == (0.9, "/v/uno.mp4") and mpv.overlays[-1] == (0.0, "/v/uno.mp4")
    # posición 0,5 − latencia 0,1 + 0,5 que siguió sonando durante el fundido + 0,33 de adelanto
    assert [c for c in mpv.cmds if c[0] == "loadfile"][-1][4].startswith("start=1.230,")
    mpv.overlays.clear()
    v.step(st("stopped"))
    assert mpv.overlays[9] == (1.0, "/v/uno.mp4") and mpv.overlays[-1] == (0.0, str(logo))


def test_reposo_aplanado_una_sola_vez(tmp_path, monkeypatch):
    import os

    import engine.video as video

    calls = []
    monkeypatch.setattr(video, "flatten", lambda image, out: (calls.append(image), out)[1])
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    mpv = FakeMpv()
    v = Video(None, lambda s: "/v/uno.mp4", lambda: logo, mpv=mpv, cache_dir=tmp_path, fade=0)
    for state in ("stopped", "playing", "stopped", "playing", "stopped"):
        v.step(st(state))
    assert calls == [logo] and mpv.cmds[-1][1] == str(tmp_path / "reposo-plano.png")  # Stop sin esperar a ffmpeg
    os.utime(logo, (1, 1))  # otra imagen con el mismo nombre: se aplana de nuevo
    v.step(st("stopped"))
    assert calls == [logo, logo]


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


def test_modo_hdmi_automatico():
    from engine.video import pick_mode

    adaptador = ["1024x768", "1920x1080", "1360x768", "1280x720", "800x600"]  # el TS35505 de Cristian
    assert pick_mode(adaptador) == "1920x1080"  # pide 4:3 pero hay 16:9: los visuales son 16:9
    assert pick_mode(adaptador, "1360x768") == "1360x768"  # elegido a mano
    assert pick_mode(adaptador, "999x9") == "1920x1080"  # uno que no ofrece: automático
    assert pick_mode(["1280x720", "1920x1080"]) == "1280x720"  # pantalla ancha: la que pide
    assert pick_mode(["1024x768", "800x600"]) == "1024x768"  # sin 16:9: la que pide
    assert pick_mode(["3840x2160", "1920x1080i", "1920x1080", "1024x768"]) == "1920x1080"  # tope 1080p, sin entrelazado
    assert pick_mode([]) is None


def test_modos_de_la_pantalla(tmp_path):
    from engine.video import hdmi_modes

    card = tmp_path / "card1-HDMI-A-1"
    card.mkdir()
    (card / "status").write_text("connected\n")
    (card / "modes").write_text("1024x768\n1920x1080\n1920x1080\n1360x768\n")
    assert hdmi_modes(tmp_path) == ["1024x768", "1920x1080", "1360x768"]
    (card / "status").write_text("disconnected\n")
    assert hdmi_modes(tmp_path) == []


def test_encaje_del_video():
    from engine.video import check_fit, fit_opts

    assert check_fit(None) == {"mode": "fit", "scale_x": 100, "scale_y": 100, "x": 0.0, "y": 0.0}
    assert check_fit({"scale": 80}) == {"mode": "fit", "scale_x": 80, "scale_y": 80, "x": 0.0, "y": 0.0}  # config vieja
    # ancho y alto por separado: pantallas que deforman (mpv los ignora con keepaspect=no, o sea en Estirar)
    assert fit_opts({"mode": "fill", "scale_x": 90, "scale_y": 100, "x": 5, "y": -2.5}) == \
        "keepaspect=yes,panscan=1,video-zoom=0,video-scale-x=0.9,video-scale-y=1,video-pan-x=0.05,video-pan-y=-0.025"
    assert fit_opts({"mode": "stretch"}).startswith("keepaspect=no,panscan=0,")
    assert check_fit({"x": -50, "y": 50})["x"] == -50  # se puede correr hasta la mitad
    for bad in ({"mode": "zoom"}, {"scale_x": 200}, {"scale_y": 10}, {"x": 60}):
        with pytest.raises(ValueError):
            check_fit(bad)


def test_patron_y_encaje_en_vivo(tmp_path):
    from pathlib import Path

    v, mpv, clock, logo = make(tmp_path)
    v.pattern, v.pattern_on = Path("/run/patron.png"), True
    v.step(st("stopped"))
    assert mpv.cmds[-1] == ("loadfile", "/run/patron.png", "replace", -1,
                            "keepaspect=yes,panscan=0,video-zoom=0,video-scale-x=1,video-scale-y=1,"
                            "video-pan-x=0,video-pan-y=0,background-color=#00000000,pause=no")
    v.set_fit({"mode": "fit", "scale_x": 95, "scale_y": 100, "x": 1, "y": 0})  # en vivo, sobre el patrón
    assert ("set_property", "video-scale-x", 0.95) in mpv.cmds
    assert ("set_property", "video-pan-x", 0.01) in mpv.cmds
    v.step(st("playing", pos=0.5))  # arranca a sonar: se apaga el patrón y va el video, con el encaje nuevo
    assert not v.pattern_on and mpv.cmds[-1][1] == "/v/uno.mp4" and "video-scale-x=0.95" in mpv.cmds[-1][4]
    v.step(st("stopped"))
    assert mpv.cmds[-1][1] == str(logo)  # parado de nuevo: el logo, no el patrón
    n = len(mpv.cmds)
    v.set_fit({"scale_x": 100, "scale_y": 100})  # con el logo no toca mpv
    assert len(mpv.cmds) == n


def test_archivo_de_reposo(tmp_path):
    from engine.video import idle_file

    logo = tmp_path / "video-logo.png"
    assert idle_file(tmp_path, logo) is None  # nada: negro
    logo.write_bytes(b"png")
    assert idle_file(tmp_path, logo) == logo
    d = tmp_path / "reposo"
    d.mkdir()
    (d / "notas.txt").write_text("no")
    (d / ".subiendo.png").write_bytes(b"a medio subir")
    assert idle_file(tmp_path, logo) == logo  # ni lo que no es imagen o video ni lo que se está subiendo
    (d / "Visuales.MP4").write_bytes(b"mp4")
    assert idle_file(tmp_path, logo) == d / "Visuales.MP4"


def test_reposo_con_video_en_loop_y_su_propio_encaje(tmp_path):
    import os

    idle = tmp_path / "reposo.mp4"
    idle.write_bytes(b"mp4")
    mpv, clock = FakeMpv(), Clock()
    v = Video(None, lambda s: None, lambda: idle, mpv=mpv, clock=clock,
              idle_fit={"mode": "fill", "scale_x": 100, "scale_y": 100}, fade=0)
    v.step(st("stopped"))
    assert mpv.cmds[-1] == ("loadfile", str(idle), "replace", -1, "keepaspect=yes,panscan=1,video-zoom=0,"
                            "video-scale-x=1,video-scale-y=1,video-pan-x=0,video-pan-y=0,"
                            "background-color=#00000000,loop-file=inf,pause=no")
    v.set_idle_fit({"mode": "fit", "scale_x": 80, "scale_y": 80})  # en vivo
    assert ("set_property", "panscan", 0.0) in mpv.cmds
    n = len(mpv.cmds)
    v.set_fit({"scale_x": 90, "scale_y": 90})  # el encaje de los videos no toca el reposo
    v.step(st("playing", slug="sin-video", pos=2))  # canción sin video: sigue el reposo, sin recargar
    assert len(mpv.cmds) == n
    os.utime(idle, (1, 1))  # se subió otro archivo con el mismo nombre: se recarga
    v.step(st("stopped"))
    assert len(mpv.cmds) == n + 1 and mpv.cmds[-1][1] == str(idle) and "video-scale-x=0.8" in mpv.cmds[-1][4]


def test_reinicia_mpv_si_no_esta_en_el_modo_que_corresponde(tmp_path):
    v, mpv, clock, logo = make(tmp_path)
    restarts = []
    mpv.restart = lambda: restarts.append(True)
    mpv.mode, mpv.current_mode = (lambda: "1920x1080"), "1024x768"  # arrancó antes de que la pantalla diera sus modos
    v._check_mode()
    assert restarts == [True]
    mpv.current_mode = "1920x1080"  # ya está bien: nada
    v._check_mode()
    mpv.mode = lambda: None  # sin pantalla: nada
    v._check_mode()
    assert restarts == [True]


def test_resolucion_por_el_engine(monkeypatch):
    from engine import daemon, store
    from test_show import FakePlayer

    monkeypatch.setattr(daemon, "hdmi_modes", lambda: ["1024x768", "1920x1080", "1360x768"])
    restarts = []

    fits = []

    class FakeVideo:
        pattern_on = False
        set_fit = staticmethod(fits.append)

        class mpv:
            current_mode = "1920x1080"
            restart = staticmethod(lambda: restarts.append(True))

    engine = daemon.Engine(FakePlayer(), dict(daemon.DEFAULT_CONFIG))
    engine.video = FakeVideo
    info = engine.handle({"cmd": "hdmi"})
    assert (info["modes"][0], info["mode"], info["current"], info["video"]) == ("1024x768", "auto", "1920x1080", True)
    assert engine.handle({"cmd": "set_hdmi", "mode": "1360x768"}) == {"ok": True}
    assert restarts == [True] and store.read_json(daemon.config_path())["hdmi_mode"] == "1360x768"
    with pytest.raises(daemon.EngineError, match="no ofrece"):
        engine.handle({"cmd": "set_hdmi", "mode": "800x480"})

    assert info["fit"] == {"mode": "fit", "scale_x": 100, "scale_y": 100, "x": 0.0, "y": 0.0} and info["pattern"] is False
    resp = engine.handle({"cmd": "set_fit", "fit": {"mode": "fill", "scale_x": 96, "scale_y": 100, "x": 0, "y": 1},
                          "pattern": True})
    assert resp["fit"]["scale_x"] == 96 and FakeVideo.pattern_on is True and fits[-1]["mode"] == "fill"
    assert store.read_json(daemon.config_path())["video_fit"]["y"] == 1.0
    with pytest.raises(ValueError, match="escala"):
        engine.handle({"cmd": "set_fit", "fit": {"scale_x": 10}})


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
