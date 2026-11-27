import collections
import datetime as dt
import re
import time

YELLOW = (255, 205, 0)
RED = (205, 25, 25)
GREEN = (20, 150, 65)
DARK = (18, 18, 24)
AMBER = (235, 130, 0)
RC_SKIP_FLAGS = {"BLUE", "CLEAR", "GREEN", "RED", "CHEQUERED"}
RC_MAJOR = [("DISQUALIFIED", "DISQUALIFIED"), ("BLACK AND WHITE", "BLACK AND WHITE FLAG"),
            ("STOP/GO", "PENALTY"), ("DRIVE THROUGH", "PENALTY"), ("PENALTY", "PENALTY"),
            ("REPRIMAND", "REPRIMAND"), ("UNDER INVESTIGATION", "INVESTIGATION"),
            ("NO FURTHER", "NO FURTHER ACTION"), ("NOTED", "INCIDENT NOTED")]
TRACK = {"2": "YELLOW FLAG", "4": "SAFETY CAR", "5": "RED FLAG", "6": "VIRTUAL SAFETY CAR", "7": "VSC ENDING"}


def to_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def secs(v):
    if not isinstance(v, str) or not v.strip():
        return None
    v = v.strip().lstrip("+")
    try:
        if ":" in v:
            m, s = v.split(":", 1)
            return int(m) * 60 + float(s)
        return float(v)
    except ValueError:
        return None


def as_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        return [v[k] for k in sorted((k for k in v if str(k).isdigit()), key=int)]
    return []


def val(v):
    return (v.get("Value") or "") if isinstance(v, dict) else (v if isinstance(v, str) else "")


def colour(hexstr):
    try:
        h = str(hexstr).lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, TypeError):
        return (128, 128, 128)


def rows(state):
    dl = state.get("DriverList") or {}
    tdata = state.get("TimingData") or {}
    td = tdata.get("Lines") or {}
    app = (state.get("TimingAppData") or {}).get("Lines") or {}
    part = to_int(tdata.get("SessionPart"), 0)
    out = []
    for num, t in td.items():
        if not isinstance(t, dict):
            continue
        d = dl.get(num) if isinstance(dl.get(num), dict) else {}
        a = app.get(num) if isinstance(app.get(num), dict) else {}
        stints = [s for s in as_list(a.get("Stints")) if isinstance(s, dict)]
        best = val(t.get("BestLapTime"))
        bl = as_list(t.get("BestLapTimes"))
        if part and len(bl) >= part and val(bl[part - 1]):
            best = val(bl[part - 1])
        out.append({
            "num": num,
            "pos": to_int(t.get("Position"), 99),
            "tla": d.get("Tla") or num,
            "colour": colour(d.get("TeamColour")),
            "interval": val(t.get("IntervalToPositionAhead")),
            "gap": val(t.get("GapToLeader")),
            "tyre": (stints[-1].get("Compound") or "").upper() if stints else "",
            "grid": to_int(a.get("GridPos")),
            "laps": to_int(t.get("NumberOfLaps"), 0),
            "pit": bool(t.get("InPit") or t.get("PitOut")),
            "pits": to_int(t.get("NumberOfPitStops"), 0),
            "out": bool(t.get("Retired") or t.get("Stopped")),
            "ko": bool(t.get("KnockedOut")),
            "best": best,
        })
    out.sort(key=lambda r: (r["pos"], r["num"]))
    return out


def _utc(s):
    if not isinstance(s, str):
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", s.replace("Z", "+00:00"))
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def remaining(state, delay=0):
    c = state.get("ExtrapolatedClock") or {}
    m = re.match(r"(\d+):(\d+):(\d+)", str(c.get("Remaining", "")))
    if not m:
        return None
    rem = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
    start = _utc(c.get("Utc"))
    if c.get("Extrapolating") and start:
        now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=delay)
        rem -= (now - start).total_seconds()
    return max(0, int(rem))


def session_info(state):
    si = state.get("SessionInfo") or {}
    lc = state.get("LapCount") or {}
    meeting = si.get("Meeting") or {}
    name = si.get("Name") or si.get("Type") or "Session"
    kind = si.get("Type") or "Race"
    if kind not in ("Race", "Qualifying", "Practice"):
        kind = "Qualifying" if "Qualifying" in name or "Shootout" in name else "Race" if "Sprint" in name or "Race" in name else "Practice"
    return {
        "kind": kind,
        "name": name,
        "meeting": meeting.get("Name") or "",
        "status": (state.get("SessionStatus") or {}).get("Status") or "",
        "lap": to_int(lc.get("CurrentLap"), 0),
        "total": to_int(lc.get("TotalLaps"), 0),
        "track": str((state.get("TrackStatus") or {}).get("Status") or "1"),
        "part": to_int((state.get("TimingData") or {}).get("SessionPart"), 0),
    }


def closest_battle(rs, top=10):
    best = None
    for i in range(1, min(top, len(rs))):
        gap = secs(rs[i]["interval"])
        if gap is not None and gap < 1.0 and not rs[i]["pit"] and not rs[i - 1]["pit"]:
            if best is None or gap < best[2]:
                best = (rs[i - 1], rs[i], gap)
    return best


def status_line(state, rs, rc=None):
    info = session_info(state)
    if info["track"] in TRACK:
        bg = RED if info["track"] == "5" else YELLOW
        return TRACK[info["track"]], bg
    if rc:
        return rc, AMBER
    if info["status"] in ("Finished", "Finalised", "Ends"):
        return "CHEQUERED FLAG", None
    if info["kind"] == "Race":
        b = closest_battle(rs)
        if b:
            a, c, gap = b
            return f"BATTLE FOR P{a['pos']}: {c['tla']} {gap:.1f}s behind {a['tla']}", None
    return "", None


class Alerts:
    def __init__(self):
        self.reset()

    def reset(self):
        self.primed = False
        self.prev = {}
        self.prev_pits = {}
        self.track = "1"
        self.rc_seen = 0
        self.pit_time = {}
        self.laps = {}
        self.battle_cd = {}
        self.queue = collections.deque()
        self.current = None
        self.until = 0.0
        self.rc_text = None
        self.rc_until = 0.0

    def rc_line(self):
        return self.rc_text if time.monotonic() < self.rc_until else None

    def active(self):
        now = time.monotonic()
        if self.current and now < self.until:
            return self.current
        self.current = None
        if self.queue:
            self.current = self.queue.popleft()
            self.until = now + self.current["dur"]
        return self.current

    def dismiss(self):
        self.until = 0.0

    def _add(self, title, lines, bg, dur, fg=(255, 255, 255), cars=None, chequered=False, body=None):
        if title in ("OVERTAKE", "BATTLE", "INCIDENT NOTED") and len(self.queue) >= 4:
            return
        self.queue.append({"title": title, "lines": lines, "bg": bg, "fg": fg, "dur": dur,
                           "cars": cars or [], "chequered": chequered, "body": body})

    @staticmethod
    def _order(rs):
        pos = {r["num"]: r["pos"] for r in rs if r["pos"] < 99}
        return pos if len(set(pos.values())) == len(pos) else None

    def update(self, state):
        info = session_info(state)
        rs = rows(state)
        now = time.monotonic()
        for r in rs:
            if r["pit"] or r["pits"] != self.prev_pits.get(r["num"], r["pits"]):
                self.pit_time[r["num"]] = now
            self.prev_pits[r["num"]] = r["pits"]
        track = info["track"]
        msgs = as_list((state.get("RaceControlMessages") or {}).get("Messages"))
        race = info["kind"] == "Race"
        if not self.primed:
            self.primed = True
            self.track = track
            self.rc_seen = len(msgs)
            self.prev = self._order(rs) or {}
            self._lap_check(rs, prime=True)
            return
        if track != self.track:
            final = info["total"] and info["lap"] >= info["total"]
            if track == "5":
                self._add("RED FLAG", ["Session stopped"], RED, 7)
            elif race and track == "4" and not final:
                self._add("SAFETY CAR", ["Safety Car deployed"], YELLOW, 7, fg=DARK)
            elif race and track == "6" and not final:
                self._add("VIRTUAL SAFETY CAR", ["VSC deployed"], YELLOW, 7, fg=DARK)
            elif track == "1" and self.track in ("4", "5", "6", "7"):
                self._add("GREEN FLAG", ["Racing resumes" if race else "Session resumes"], GREEN, 5)
            self.track = track
        if len(msgs) < self.rc_seen:
            self.rc_seen = 0
        for m in msgs[self.rc_seen:]:
            if isinstance(m, dict) and str(m.get("Flag", "")).upper() == "CHEQUERED":
                if race and rs:
                    w = rs[0]
                    top = "   ".join(f"P{r['pos']} {r['tla']}" for r in rs[1:5])
                    self._add("CHEQUERED FLAG", [f"{w['tla']} WINS", top], DARK, 12, cars=[w], chequered=True)
                else:
                    self._add("CHEQUERED FLAG", [f"{info['name']} complete"], DARK, 8, chequered=True)
            elif isinstance(m, dict):
                self._race_control(m, now)
        self.rc_seen = len(msgs)
        if race and info["status"] == "Started" and track in ("1", "2"):
            self._overtakes(rs, now)
        if race:
            self._lap_check(rs, prime=track not in ("1", "2"))
        order = self._order(rs)
        if order:
            self.prev = order

    def _race_control(self, m, now):
        text = " ".join(str(m.get("Message") or "").split())
        flag = str(m.get("Flag") or "").upper()
        if not text or flag in RC_SKIP_FLAGS or m.get("Category") in ("SafetyCar", "Drs"):
            return
        self.rc_text, self.rc_until = text, now + 10
        up = text.upper()
        if "TRACK LIMITS" in up or "DELETED" in up:
            return
        for key, title in RC_MAJOR:
            if key in up:
                self._add(title, [], AMBER if title != "DISQUALIFIED" else RED, 8, fg=DARK, body=text)
                return

    def _overtakes(self, rs, now):
        cur = self._order(rs)
        if not cur or not self.prev:
            return
        by = {r["num"]: r for r in rs}

        def pitted(n):
            return by[n]["pit"] or now - self.pit_time.get(n, -1e9) < 60

        for a, pa in sorted(cur.items(), key=lambda x: x[1]):
            if pa > 10 or a not in self.prev:
                continue
            for b, pb in cur.items():
                if b == a or b not in self.prev:
                    continue
                if self.prev[a] > self.prev[b] and pa < pb:
                    if pitted(a) or pitted(b) or by[b]["out"]:
                        continue
                    self._add("OVERTAKE", [f"{by[a]['tla']} passes {by[b]['tla']}", f"for P{pa}"],
                              DARK, 4, cars=[by[a], by[b]])

    def _lap_check(self, rs, prime=False):
        for i, r in enumerate(rs):
            h = self.laps.setdefault(r["num"], {"lap": r["laps"], "rec": collections.deque(maxlen=2)})
            if r["laps"] <= h["lap"]:
                continue
            h["lap"] = r["laps"]
            if prime:
                h["rec"].clear()
                continue
            if i == 0 or r["pos"] > 10 or r["pit"]:
                continue
            ahead = rs[i - 1]
            h["rec"].append((ahead["num"], secs(r["interval"])))
            rec = h["rec"]
            if len(rec) == 2 and rec[0][0] == rec[1][0] == ahead["num"] and all(
                    g is not None and g < 1.0 for _, g in rec):
                key = (ahead["num"], r["num"])
                if r["laps"] - self.battle_cd.get(key, -99) >= 8:
                    self.battle_cd[key] = r["laps"]
                    self._add("BATTLE", [f"{r['tla']} {rec[-1][1]:.1f}s behind {ahead['tla']}",
                                         f"for P{ahead['pos']}"], DARK, 5, cars=[ahead, r])
