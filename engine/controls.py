"""Controles MIDI: footswitches del iRig (o cualquier pedalera MIDI) → comandos del show.

Cada control se identifica por una clave: "pc:CANAL:N" (Program Change), "cc:CANAL:N" (Control
Change) o "note:CANAL:N". El mapa clave → acción vive en config.json y se arma con MIDI Learn.
Un control dispara una sola acción; una acción puede tener varios controles (por ejemplo, el
mismo footswitch en modo normal y en modo stomp).

Reglas que salen de medir el iRig (ver docs/PLAN.md):
- Antirrebote: una pisada llegó como 0 → 127 → 0 en 140 ms. Se ignora el mismo control dentro
  de los 200 ms de su última pisada aceptada.
- En modo stomp cada footswitch es un interruptor (alterna 127 / 0): cualquier valor es una pisada.
- Un CC que alguna vez mandó un valor distinto de 0 y 127 es continuo (pedal de expresión):
  nunca cuenta como pisada ni se puede aprender.
"""
import threading
import time

ACTIONS = {"play_pause": "Play / Pausa", "stop": "Stop", "next": "Siguiente", "prev": "Anterior"}
DEBOUNCE = 0.2
DEFAULT_PORT = "iRig Stomp IO MIDI 1"
# iRig Stomp I/O, canal 1: modo normal (Program Change 0–3) y modo stomp (CC 20–23).
DEFAULT_MAP = {
    "pc:0:0": "prev", "pc:0:1": "next", "pc:0:2": "stop", "pc:0:3": "play_pause",
    "cc:0:20": "prev", "cc:0:21": "next", "cc:0:22": "stop", "cc:0:23": "play_pause",
}
IRIG_NAMES = {
    **{f"pc:0:{i}": f"Footswitch {i + 1}" for i in range(4)},
    **{f"cc:0:{20 + i}": f"Footswitch {i + 1} en modo stomp" for i in range(4)},
    "cc:0:91": "Footswitch 1 mantenido", "cc:0:90": "Footswitch 2 mantenido",
    "cc:0:26": "Toe switch del pedal", "cc:0:11": "Pedal de expresión",
}


def control_key(msg):
    if msg.type == "program_change":
        return f"pc:{msg.channel}:{msg.program}"
    if msg.type == "control_change":
        return f"cc:{msg.channel}:{msg.control}"
    if msg.type == "note_on" and msg.velocity > 0:
        return f"note:{msg.channel}:{msg.note}"
    return None


def label(key):
    kind, channel, number = key.split(":")
    text = f"{ {'pc': 'Program Change', 'cc': 'CC', 'note': 'Nota'}[kind]} {number} · canal {int(channel) + 1}"
    name = IRIG_NAMES.get(key)
    return f"{name} ({text})" if name else text


class Controls:
    def __init__(self, mapping, on_action, clock=time.monotonic, debounce=DEBOUNCE):
        self.map = {k: a for k, a in (mapping or {}).items() if a in ACTIONS}
        self.on_action = on_action  # recibe la acción, desde el hilo de MIDI
        self.clock = clock
        self.debounce = debounce
        self.port_name = None
        self._port = None
        self._last = None  # (clave, acción, aprendida, instante)
        self._accepted = {}
        self._continuous = set()
        self._learning = None  # (acción, callback con la clave aprendida)
        self._lock = threading.Lock()

    def handle(self, msg):
        """Lo llama el hilo de MIDI con cada mensaje."""
        key = control_key(msg)
        if key is None:
            return
        if msg.type == "control_change" and msg.value not in (0, 127):
            self._continuous.add(key)
            return
        if key in self._continuous:
            return
        now = self.clock()
        with self._lock:
            if now - self._accepted.get(key, float("-inf")) < self.debounce:
                return
            self._accepted[key] = now
            learning, self._learning = self._learning, None
            if learning:
                action, done = learning
                self.map[key] = action
            else:
                action = self.map.get(key)
            self._last = (key, action, bool(learning), time.monotonic())
        if learning:
            done(key)  # la pisada que se aprende no dispara la acción
        elif action:
            self.on_action(action)

    def learn(self, action, done):
        with self._lock:
            self._learning = (action, done)

    def cancel_learn(self):
        with self._lock:
            self._learning = None

    def forget(self, action):
        with self._lock:
            self.map = {k: a for k, a in self.map.items() if a != action}

    def mapping(self):
        with self._lock:
            return dict(self.map)

    def snapshot(self):
        with self._lock:
            last = None
            if self._last:
                key, action, learned, at = self._last
                last = {"key": key, "label": label(key), "action": action, "learned": learned,
                        "ago": round(time.monotonic() - at, 1)}
            return {"port": self.port_name, "map": dict(self.map), "labels": {k: label(k) for k in self.map},
                    "actions": ACTIONS, "last": last, "learning": self._learning[0] if self._learning else None}

    @property
    def connected(self):
        return self._port is not None

    def open(self, wanted):
        """Abre el primer puerto de entrada cuyo nombre contenga `wanted`. Devuelve si quedó abierto."""
        import mido

        if self._port is not None:
            return True
        names = [n for n in mido.get_input_names() if wanted in n]
        if not names:
            return False
        self._port = mido.open_input(names[0], callback=self.handle)
        self.port_name = names[0]
        return True
