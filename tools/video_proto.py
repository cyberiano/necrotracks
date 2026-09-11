"""Prueba de video en la Pi: mpv al HDMI sin audio, midiendo la CPU de mpv y los cuadros perdidos.

Uso (en la Pi, con python3 del sistema): python3 tools/video_proto.py VIDEO SEGUNDOS [HWDEC] [VO]
    HWDEC: v4l2m2m (default), v4l2m2m-copy, no      VO: gpu (default), drm

Medido en la 3B+ (2026-09-11), H.264 30 fps, sin el engine sonando:
    v4l2m2m + gpu (drmprime-overlay, sin copias):  720p 14 % de CPU, 1080p 16 %, 0 cuadros perdidos
    v4l2m2m-copy + drm:                             720p 270 %, pierde ~6 cuadros por segundo
    software:                                       720p 334 %, pierde ~10 cuadros por segundo
"""
import json
import os
import socket
import subprocess
import sys
import time

video, seconds = sys.argv[1], float(sys.argv[2])
hwdec = sys.argv[3] if len(sys.argv) > 3 else "v4l2m2m"
vo = sys.argv[4] if len(sys.argv) > 4 else "gpu"
sock = "/tmp/mpv-proto.sock"
args = ["mpv", f"--vo={vo}", f"--hwdec={hwdec}", "--no-audio", "--loop-file=inf", "--really-quiet",
        f"--input-ipc-server={sock}", video]
if vo == "gpu":
    args.insert(2, "--gpu-context=drm")
p = subprocess.Popen(args, stderr=subprocess.PIPE, text=True)
hz = os.sysconf("SC_CLK_TCK")


def prop(name):
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2)
            s.connect(sock)
            s.sendall((json.dumps({"command": ["get_property", name]}) + "\n").encode())
            return json.loads(s.makefile().readline()).get("data")
    except (OSError, ValueError):
        return None


def ticks(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return int(f[11]) + int(f[12])


def temp():
    return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000


print(f"mpv {' '.join(args[1:-1])}", flush=True)
time.sleep(4)
if p.poll() is not None:
    print("mpv salió:", p.returncode, p.stderr.read()[-800:])
    sys.exit(1)
print(f"hwdec activo: {prop('hwdec-current')}  vo: {prop('current-vo')}  "
      f"video: {prop('width')}x{prop('height')} @ {prop('container-fps')}", flush=True)
t0 = last_t = time.monotonic()
last_c = ticks(p.pid)
cpus, drops = [], None
try:
    while time.monotonic() - t0 < seconds and p.poll() is None:
        time.sleep(5)
        now, c = time.monotonic(), ticks(p.pid)
        cpu = 100 * (c - last_c) / hz / (now - last_t)
        cpus.append(cpu)
        last_t, last_c = now, c
        drops = prop("frame-drop-count")
        print(f"[{now - t0:5.0f}s] mpv cpu={cpu:5.1f}%  load={os.getloadavg()[0]:.2f}  {temp():.0f}°C  "
              f"pos={prop('time-pos') or 0:6.1f}  drop={drops} dec_drop={prop('decoder-frame-drop-count')}",
              flush=True)
finally:
    p.terminate()
    p.wait(5)
if cpus:
    print(f"FIN: cpu media {sum(cpus) / len(cpus):.1f}% (máx {max(cpus):.1f}%) sobre 400%  cuadros perdidos={drops}")
