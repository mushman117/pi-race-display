import collections
import json
import logging
import random
import threading
import time
import urllib.parse

import requests
import websocket

log = logging.getLogger("live")

HOST = "livetiming.formula1.com"
TOPICS = ["Heartbeat", "SessionInfo", "SessionStatus", "TrackStatus", "LapCount", "DriverList",
          "TimingData", "TimingAppData", "RaceControlMessages", "ExtrapolatedClock"]
SEP = "\x1e"
HEADERS = {"User-Agent": "pi-race-display/1.1"}


class AuthRequired(Exception):
    pass


def merge(dst, src):
    if isinstance(src, dict):
        if isinstance(dst, list):
            for k, v in src.items():
                if str(k).isdigit():
                    i = int(k)
                    while len(dst) <= i:
                        dst.append({})
                    dst[i] = merge(dst[i], v)
            return dst
        if not isinstance(dst, dict):
            dst = {}
        for k, v in src.items():
            if k == "_kf":
                continue
            if k == "_deleted":
                for key in (v if isinstance(v, list) else []):
                    dst.pop(str(key), None)
            else:
                dst[k] = merge(dst.get(k), v)
        return dst
    return src


class LiveTiming:
    def __init__(self, delay=0):
        self.delay = max(0, min(120, int(delay)))
        self.lock = threading.Lock()
        self.queue = collections.deque()
        self.state = {}
        self.connected = False
        self._stop = threading.Event()
        self._thread = None
        self._ws = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self.state = {}
        with self.lock:
            self.queue.clear()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        ws, self._ws = self._ws, None
        if ws:
            try:
                ws.close()
            except Exception:
                pass
        self._thread = None
        self.connected = False
        self.state = {}
        with self.lock:
            self.queue.clear()

    def pump(self):
        cutoff = time.monotonic() - self.delay
        changed = False
        with self.lock:
            while self.queue and self.queue[0][0] <= cutoff:
                _, topic, data = self.queue.popleft()
                self.state[topic] = merge(self.state.get(topic), data)
                changed = True
        return changed

    def _push(self, topic, data, stop=None):
        if stop is not None and stop.is_set():
            return
        with self.lock:
            self.queue.append((time.monotonic(), topic, data))

    def _run(self, stop):
        backoff = 2
        while not stop.is_set():
            try:
                self._connect(stop)
                backoff = 2
            except AuthRequired:
                log.warning("live timing: F1 refused access (401); retrying in 5 minutes")
                backoff = 300
            except Exception as e:
                log.warning("live timing: %s", e)
            self.connected = False
            stop.wait(backoff)
            backoff = min(backoff * 2, 300)

    def _connect(self, stop):
        s = requests.Session()
        s.headers.update(HEADERS)
        r = s.post(f"https://{HOST}/signalrcore/negotiate", params={"negotiateVersion": 1}, timeout=15)
        if r.status_code == 401:
            raise AuthRequired()
        r.raise_for_status()
        neg = r.json()
        token = neg.get("connectionToken") or neg.get("connectionId")
        cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items()) or None
        ws = websocket.create_connection(
            f"wss://{HOST}/signalrcore?" + urllib.parse.urlencode({"id": token}),
            header=[f"{k}: {v}" for k, v in HEADERS.items()], cookie=cookie, timeout=10)
        self._ws = ws
        try:
            ws.send(json.dumps({"protocol": "json", "version": 1}) + SEP)
            ws.send(json.dumps({"type": 1, "invocationId": "0", "target": "Subscribe",
                                "arguments": [TOPICS]}) + SEP)
            self.connected = True
            log.info("live timing connected")
            last_rx = last_ping = time.monotonic()
            while not stop.is_set():
                now = time.monotonic()
                if now - last_ping > 10:
                    ws.send(json.dumps({"type": 6}) + SEP)
                    last_ping = now
                if now - last_rx > 60:
                    raise ConnectionError("no data for 60 s")
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    continue
                last_rx = time.monotonic()
                for part in raw.split(SEP):
                    if part.strip():
                        self._handle(json.loads(part), stop)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _handle(self, msg, stop):
        t = msg.get("type")
        if t == 1 and str(msg.get("target", "")).lower() == "feed":
            a = msg.get("arguments") or []
            if len(a) >= 2:
                self._push(a[0], a[1], stop)
        elif t == 3:
            if msg.get("error"):
                raise ConnectionError(f"subscribe failed: {msg['error']}")
            if isinstance(msg.get("result"), dict):
                for topic, data in msg["result"].items():
                    self._push(topic, data, stop)
        elif t == 7:
            raise ConnectionError(f"server closed: {msg.get('error', '')}")


DEMO_DRIVERS = [
    ("1", "VER", "Red Bull Racing", "3671C6"), ("4", "NOR", "McLaren", "FF8000"),
    ("81", "PIA", "McLaren", "FF8000"), ("16", "LEC", "Ferrari", "E8002D"),
    ("44", "HAM", "Ferrari", "E8002D"), ("63", "RUS", "Mercedes", "27F4D2"),
    ("12", "ANT", "Mercedes", "27F4D2"), ("14", "ALO", "Aston Martin", "229971"),
    ("23", "ALB", "Williams", "64C4FF"), ("10", "GAS", "Alpine", "0093CC"),
    ("55", "SAI", "Williams", "64C4FF"), ("6", "HAD", "Racing Bulls", "6692FF"),
]


class DemoTiming(LiveTiming):
    LAPS = 20

    def _run(self, stop):
        while not stop.is_set():
            self._race(stop)
            stop.wait(15)

    @staticmethod
    def _lines(order, gaps, lap):
        out, prev = {}, 0.0
        for i, n in enumerate(order):
            g = gaps[n]
            lead = f"LAP {lap}"
            out[n] = {"Position": str(i + 1), "Line": i + 1, "NumberOfLaps": lap,
                      "GapToLeader": lead if i == 0 else f"+{g:.3f}",
                      "IntervalToPositionAhead": {"Value": lead if i == 0 else f"+{g - prev:.3f}"},
                      "InPit": False, "PitOut": False, "Retired": False}
            prev = g
        return out

    def _race(self, stop):
        rnd = random.Random()
        order = [d[0] for d in DEMO_DRIVERS]
        rnd.shuffle(order)
        gaps = {n: i * 1.4 for i, n in enumerate(order)}
        self.connected = True
        snap = {
            "SessionInfo": {"Type": "Race", "Name": "Race",
                            "Meeting": {"Name": "Demo Grand Prix", "Circuit": {"ShortName": "Demo"}}},
            "SessionStatus": {"Status": "Started"},
            "TrackStatus": {"Status": "1", "Message": "AllClear"},
            "LapCount": {"CurrentLap": 1, "TotalLaps": self.LAPS},
            "DriverList": {n: {"RacingNumber": n, "Tla": t, "TeamName": tm, "TeamColour": c}
                           for n, t, tm, c in DEMO_DRIVERS},
            "TimingAppData": {"Lines": {n: {"GridPos": str(i + 1), "Stints": [
                {"Compound": rnd.choice(["SOFT", "MEDIUM"]), "TotalLaps": 0}]} for i, n in enumerate(order)}},
            "TimingData": {"Lines": self._lines(order, gaps, 1)},
            "RaceControlMessages": {"Messages": []},
        }
        for k, v in snap.items():
            self._push(k, v, stop)
        lap, tick, sc = 1, 0, False
        while not stop.is_set() and lap < self.LAPS:
            stop.wait(1.5)
            tick += 1
            if not sc and rnd.random() < 0.35:
                i = rnd.randrange(1, 10)
                order[i - 1], order[i] = order[i], order[i - 1]
            g = 0.0
            for i, n in enumerate(order):
                if i:
                    g += 0.4 if sc else (0.6 if i == 4 else rnd.uniform(0.3, 2.5))
                gaps[n] = round(g, 3)
            if tick % 2 == 0:
                lap += 1
                if lap == 8:
                    sc = True
                    self._push("TrackStatus", {"Status": "4", "Message": "SCDeployed"}, stop)
                if lap == 11:
                    sc = False
                    self._push("TrackStatus", {"Status": "1", "Message": "AllClear"}, stop)
                if lap == 13:
                    self._push("TimingAppData", {"Lines": {order[6]: {"Stints": {"1": {"Compound": "HARD"}}}}}, stop)
                self._push("LapCount", {"CurrentLap": lap}, stop)
            self._push("TimingData", {"Lines": self._lines(order, gaps, lap)}, stop)
        self._push("RaceControlMessages", {"Messages": {"0": {"Category": "Flag", "Flag": "CHEQUERED"}}}, stop)
        self._push("SessionStatus", {"Status": "Finished"}, stop)
