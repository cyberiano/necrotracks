import zipfile

import numpy as np
import pytest
import soundfile as sf

from conftest import SR, tone, write
from engine import library, store


def read_render(slug):
    x, sr = sf.read(str(library.render_path(slug)), dtype="float32", always_2d=True)
    assert sr == SR and x.shape[1] == 4
    return x


def test_carpeta_con_stems(tmp_path):
    src = tmp_path / "Mi Canción"
    l, r, c = tone(220), tone(330), tone(1000, amp=0.5)
    write(src / "foh_L.wav", l)
    write(src / "FOH_R.wav", r)
    write(src / "click.wav", c)
    song = library.import_song(src)
    assert song["slug"] == "mi-cancion" and song["name"] == "Mi Canción"
    assert song["foh"] == "estéreo" and song["click"] and not song["guia"]
    assert song["duration"] == pytest.approx(1.0)
    x = read_render("mi-cancion")
    np.testing.assert_allclose(x[:, 0], l, atol=1e-4)
    np.testing.assert_allclose(x[:, 1], r, atol=1e-4)
    np.testing.assert_allclose(x[:, 2], c, atol=1e-4)
    assert not x[:, 3].any()
    assert [s["slug"] for s in library.list_songs()] == ["mi-cancion"]


def test_archivo_estereo_suelto_exige_formato(tmp_path):
    f = write(tmp_path / "Sands of time.wav", np.stack([tone(1000), tone(220)], axis=1))
    with pytest.raises(library.ImportProblem, match="click-pista"):
        library.import_song(f)
    assert library.list_songs() == []


def test_formato_simple_de_la_banda(tmp_path):
    click, pista = tone(1000), tone(220)
    f = write(tmp_path / "Sands of time.wav", np.stack([click, pista], axis=1))
    song = library.import_song(f, layout="click-pista")
    assert song["foh"] == "mono" and song["click"] and song["layout"] == "click-pista"
    x = read_render(song["slug"])
    np.testing.assert_allclose(x[:, 0], pista, atol=1e-4)
    np.testing.assert_allclose(x[:, 1], pista, atol=1e-4)
    np.testing.assert_allclose(x[:, 2], click, atol=1e-4)


def test_archivo_mono_suelto_es_pista(tmp_path):
    song = library.import_song(write(tmp_path / "tema.wav", tone(220)))
    x = read_render(song["slug"])
    assert song["foh"] == "mono" and not song["click"]
    np.testing.assert_allclose(x[:, 0], x[:, 1])


def test_convierte_a_48k(tmp_path):
    f = write(tmp_path / "pista.wav", tone(220, seconds=2.0, sr=44100), sr=44100)
    song = library.import_song(f, name="Convertida")
    assert song["duration"] == pytest.approx(2.0, abs=0.01)
    assert song["original_samplerate"] == [44100]
    assert sf.info(str(library.render_path("convertida"))).samplerate == SR


def test_zip(tmp_path):
    write(tmp_path / "s" / "pista.wav", tone(220))
    write(tmp_path / "s" / "click.wav", tone(1000))
    z = tmp_path / "Tema ZIP.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.write(tmp_path / "s" / "pista.wav", "Tema ZIP/pista.wav")
        zf.write(tmp_path / "s" / "click.wav", "Tema ZIP/click.wav")
        zf.writestr("__MACOSX/._pista.wav", b"basura de macOS")
    song = library.import_song(z)
    assert song["slug"] == "tema-zip" and song["click"] and song["foh"] == "mono"


def test_bloqueado_mientras_suena(tmp_path):
    f = write(tmp_path / "pista.wav", tone(220))
    store.set_playing(True)
    try:
        with pytest.raises(library.ImportProblem, match="sonando"):
            library.import_song(f)
        assert library.import_song(f, force=True)["slug"] == "pista"
    finally:
        store.set_playing(False)


def test_reimportar_reemplaza(tmp_path):
    f = tmp_path / "pista.wav"
    write(f, tone(220))
    library.import_song(f)
    write(f, tone(220, seconds=2.0))
    assert library.import_song(f)["duration"] == pytest.approx(2.0)
    assert [p.name for p in store.library_dir().iterdir()] == ["pista"]


def test_aviso_de_fase(tmp_path):
    t = tone(220)
    song = library.import_song(write(tmp_path / "foh.wav", np.stack([t, -t], axis=1)), name="Fase")
    assert any("fase" in w for w in song["warnings"])


def test_aviso_de_duraciones_distintas(tmp_path):
    src = tmp_path / "tema"
    write(src / "pista.wav", tone(220, seconds=3.0))
    write(src / "click.wav", tone(1000, seconds=1.0))
    song = library.import_song(src)
    assert song["duration"] == pytest.approx(3.0)
    assert any("duración" in w for w in song["warnings"])


def test_archivo_desconocido_no_deja_basura(tmp_path):
    src = tmp_path / "tema"
    write(src / "pista.wav", tone(220))
    write(src / "bajo.wav", tone(110))
    with pytest.raises(library.ImportProblem, match="bajo.wav"):
        library.import_song(src)
    assert not any(store.library_dir().iterdir())
