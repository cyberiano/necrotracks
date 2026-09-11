import numpy as np
import pytest
import soundfile as sf

from engine import store

SR = 48000


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA", tmp_path / "data")
    monkeypatch.setattr(store, "RUN", tmp_path / "run")
    monkeypatch.setattr(store, "WRITE_RATE", 1e12)  # sin tope de velocidad en los tests
    return tmp_path / "data"


def tone(freq, seconds=1.0, sr=SR, amp=0.25):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write(path, data, sr=SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), data, sr, subtype="PCM_24")
    return path
