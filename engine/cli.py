"""CLI de prueba: reproduce uno o más renders seguidos (stream siempre abierto) y reporta xruns.

    python -m engine.cli FILE [FILE ...] [--device iRig] [--profile irig-mono-click] [--seconds N]
"""
import argparse
import logging
import os
import threading
import time

from .audio import SAMPLERATE, Player

# Salidas × [FOH L, FOH R, Click, Guía]. 0.5 en la suma mono: nunca puede clipear.
PROFILES = {
    "irig-mono-click": [[0.5, 0.5, 0, 0], [0, 0, 1, 0]],
    "irig-stereo": [[1, 0, 0, 0], [0, 1, 0, 0]],
    # Como la banda arma hoy sus archivos simples: L = click, R = pista mono
    "irig-click-pista": [[0, 0, 1, 0], [0.5, 0.5, 0, 0]],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--device", default="iRig")
    ap.add_argument("--profile", default="irig-mono-click", choices=PROFILES)
    ap.add_argument("--seconds", type=float, help="cortar a los N segundos (con fade)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    done = threading.Event()
    player = Player(args.device, PROFILES[args.profile])
    pending = list(args.files)

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
        if args.seconds and elapsed >= args.seconds:
            player.stop()
            time.sleep(0.5)
            break
    player.close()
    print(f"FIN pos={player.position / SAMPLERATE:.1f}s xruns={player.underflows} starved={player.starved}")


if __name__ == "__main__":
    main()
