"""Perfiles de hardware: qué device se abre y cómo se rutea el render a sus salidas.

matrix: una fila por salida física, una columna por canal del render [FOH L, FOH R, Click, Guía].
"""
PROFILES = {
    # Como la banda arma hoy sus shows simples: L = click, R = pista mono. 0.5 en la suma: nunca clipea.
    "irig-click-pista": {"device": "iRig", "matrix": [[0, 0, 1, 0], [0.5, 0.5, 0, 0]]},
    "irig-mono-click": {"device": "iRig", "matrix": [[0.5, 0.5, 0, 0], [0, 0, 1, 0]]},
    "irig-stereo": {"device": "iRig", "matrix": [[1, 0, 0, 0], [0, 1, 0, 0]]},
}
DEFAULT = "irig-click-pista"


def get(name):
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"No existe el perfil '{name}'. Hay: {', '.join(PROFILES)}") from None
