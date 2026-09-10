"""CLI de prueba de la Fase 1: reproduce un render y reporta xruns.

    python -m engine.cli FILE [--device iRig] [--profile irig-mono-click] [--seconds N]
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
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--device", default="iRig")
    ap.add_argument("--profile", default="irig-mono-click", choices=PROFILES)
    ap.add_argument("--seconds", type=float, help="cortar a los N segundos (con fade)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    done = threading.Event()
    player = Player(args.device, PROFILES[args.profile])
    player.on_end = done.set
    player.play(args.file)
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
