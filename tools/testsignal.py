"""Genera un render de prueba de 4 canales (48k / 24-bit) a bajo nivel.

    python tools/testsignal.py salida.wav --minutes 30

ch1/ch2 = senos de 220/330 Hz a -40 dBFS, ch3 = click de 1 kHz a 120 BPM a -30 dBFS, ch4 = silencio.
"""
import argparse

import numpy as np
import soundfile as sf

SR = 48000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--minutes", type=float, default=1)
    args = ap.parse_args()

    click = np.zeros(SR // 2, np.float32)  # 120 BPM = un golpe cada medio segundo
    n = int(0.015 * SR)
    click[:n] = 0.0316 * np.sin(2 * np.pi * 1000 * np.arange(n) / SR) * np.linspace(1, 0, n)
    click = np.tile(click, 2)

    with sf.SoundFile(args.out, "w", SR, 4, subtype="PCM_24") as f:
        for sec in range(int(args.minutes * 60)):
            t = (np.arange(SR) + sec * SR) / SR
            block = np.zeros((SR, 4), np.float32)
            block[:, 0] = 0.01 * np.sin(2 * np.pi * 220 * t)
            block[:, 1] = 0.01 * np.sin(2 * np.pi * 330 * t)
            block[:, 2] = click
            f.write(block)


if __name__ == "__main__":
    main()
