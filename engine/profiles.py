"""Perfiles de hardware: qué device se abre y cómo se rutea el render a sus salidas.

matrix: una fila por salida física, una columna por canal del render [FOH L, FOH R, Click, Guía].
"""
from pathlib import Path

PROFILES = {
    # Como la banda arma hoy sus shows simples: L = click, R = pista mono. 0.5 en la suma: nunca clipea.
    "irig-click-pista": {"label": "iRig · L = click, R = pista", "device": "iRig",
                         "matrix": [[0, 0, 1, 0], [0.5, 0.5, 0, 0]]},
    "irig-mono-click": {"label": "iRig · L = pista, R = click", "device": "iRig",
                        "matrix": [[0.5, 0.5, 0, 0], [0, 0, 1, 0]]},
    "irig-stereo": {"label": "iRig · pista estéreo, sin click", "device": "iRig",
                    "matrix": [[1, 0, 0, 0], [0, 1, 0, 0]]},
    # Respaldo: el jack de 3,5 mm de la Pi. Es PWM y tiene soplido: para ensayar o una emergencia, no para el PA.
    "pi-jack": {"label": "Jack de la Pi · L = click, R = pista", "device": "Headphones",
                "matrix": [[0, 0, 1, 0], [0.5, 0.5, 0, 0]]},
}
DEFAULT = "irig-click-pista"
FALLBACK = "pi-jack"


def get(name):
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"No existe el perfil '{name}'. Hay: {', '.join(PROFILES)}") from None


def card_present(profile, cards="/proc/asound/cards"):
    """¿Está conectada la placa del perfil? Mira ALSA directo: PortAudio no vuelve a listar los
    devices con el stream abierto."""
    try:
        return profile["device"].lower() in Path(cards).read_text().lower()
    except OSError:
        return False
