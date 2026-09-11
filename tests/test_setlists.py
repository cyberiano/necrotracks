import pytest

from conftest import tone, write
from engine import library, setlists


@pytest.fixture
def songs(tmp_path):
    for name in ("Uno", "Dos", "Tres"):
        library.import_song(write(tmp_path / f"{name}.wav", tone(220)), name=name)
    return ["uno", "dos", "tres"]


def test_crear_y_leer(songs):
    sl = setlists.create("Show Necropolis", songs)
    assert sl["slug"] == "show-necropolis"
    assert setlists.get("Show Necropolis")["items"][1]["song"] == "dos"
    assert all(item["behavior"] == setlists.DEFAULT_BEHAVIOR for item in sl["items"])


def test_cancion_inexistente(songs):
    with pytest.raises(ValueError, match="cuatro"):
        setlists.create("X", ["uno", "cuatro"])


def test_esperar_requiere_segundos(songs):
    sl = setlists.create("X", songs)
    with pytest.raises(ValueError):
        setlists.set_item(sl, 0, "wait", 0)
    setlists.set_item(sl, 0, "wait", 5)
    assert sl["items"][0] == {"song": "uno", "behavior": "wait", "wait": 5.0, "block": None}


def test_bloques_con_default_editable(songs):
    sl = setlists.create("X", songs)
    setlists.assign_block(sl, "Bloque 1", 0, 1, behavior="auto_next")
    assert [i["block"] for i in sl["items"]] == ["Bloque 1", "Bloque 1", None]
    assert [i["behavior"] for i in sl["items"]] == ["auto_next", "auto_next", "arm_next"]
    setlists.set_item(sl, 1, "stop")
    assert sl["items"][1] == {"song": "dos", "behavior": "stop", "wait": 0.0, "block": "Bloque 1"}


def test_mover(songs):
    sl = setlists.create("X", songs)
    setlists.move(sl, 2, 0)
    assert [i["song"] for i in sl["items"]] == ["tres", "uno", "dos"]


def test_chequeo_pre_show(songs):
    sl = setlists.create("X", songs)
    assert setlists.check(sl) == []
    library.render_path("dos").unlink()
    problems = setlists.check(sl)
    assert len(problems) == 1 and "Dos" in problems[0]
