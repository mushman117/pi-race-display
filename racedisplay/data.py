import collections
import datetime as dt
import json
import logging
import random
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("data")
JOLPICA = "https://api.jolpi.ca/ergast/f1"
METEO = "https://api.open-meteo.com/v1/forecast"
HEADERS = {"User-Agent": "pi-race-display/1.0"}
CACHE = Path.home() / ".cache" / "racedisplay"
UTC = dt.timezone.utc

SESSION_FIELDS = [
    ("FirstPractice", "Practice 1", "Practice"),
    ("SecondPractice", "Practice 2", "Practice"),
    ("ThirdPractice", "Practice 3", "Practice"),
    ("SprintQualifying", "Sprint Qualifying", "Qualifying"),
    ("SprintShootout", "Sprint Shootout", "Qualifying"),
    ("Sprint", "Sprint", "Race"),
    ("Qualifying", "Qualifying", "Qualifying"),
]
DURATION = {"Practice": 70, "Qualifying": 75, "Sprint Qualifying": 55, "Sprint Shootout": 55,
            "Sprint": 70, "Race": 180}
SHORT = {"Practice 1": "FP1", "Practice 2": "FP2", "Practice 3": "FP3", "Sprint Qualifying": "SQ",
         "Sprint Shootout": "SS", "Sprint": "SPRINT", "Qualifying": "QUALI", "Race": "RACE"}
WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Fog",
       51: "Drizzle", 53: "Drizzle", 55: "Drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
       66: "Freezing rain", 67: "Freezing rain", 71: "Snow", 73: "Snow", 75: "Heavy snow",
       80: "Showers", 81: "Showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "Thunderstorm",
       99: "Thunderstorm"}


def _get(path, **params):
    params.setdefault("limit", 100)
    for attempt in range(3):
        r = requests.get(f"{JOLPICA}/{path}.json", params=params, headers=HEADERS, timeout=20)
        if r.status_code != 429:
            break
        wait = int(r.headers.get("Retry-After", "60") or 60)
        log.info("Jolpica rate limit, waiting %ss", wait)
        time.sleep(min(wait, 300))
    r.raise_for_status()
    time.sleep(0.4)
    return r.json()["MRData"]


def _ts(d):
    if not isinstance(d, dict) or not d.get("date"):
        return None
    t = (d.get("time") or "00:00:00Z").replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(f"{d['date']}T{t}").astimezone(UTC)
    except ValueError:
        return None


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


class Session:
    __slots__ = ("name", "kind", "start", "race")

    def __init__(self, name, kind, start, race):
        self.name, self.kind, self.start, self.race = name, kind, start, race

    @property
    def end(self):
        return self.start + dt.timedelta(minutes=DURATION.get(self.name, DURATION.get(self.kind, 90)))

    @property
    def short(self):
        return SHORT.get(self.name, self.name.upper())


def parse_races(raw):
    races = []
    for r in raw:
        race = {"name": r.get("raceName", ""), "round": int(r.get("round", 0)),
                "season": int(r.get("season", 0)), "circuit": r.get("Circuit", {}), "sessions": []}
        for key, label, kind in SESSION_FIELDS:
            t = _ts(r.get(key))
            if t:
                race["sessions"].append(Session(label, kind, t, race))
        t = _ts(r)
        if t:
            race["sessions"].append(Session("Race", "Race", t, race))
        race["sessions"].sort(key=lambda s: s.start)
        race["date"] = t
        races.append(race)
    return races


class Store:
    def __init__(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.raw = self._load("store") or {}
        self.history = self._load("history") or {}
        self.races = parse_races(self.raw.get("races", []))
        self.error = None
        self._otd = None

    def _load(self, name):
        try:
            return json.loads((CACHE / f"{name}.json").read_text())
        except (OSError, ValueError):
            return None

    def _save(self, name, obj):
        tmp = CACHE / f"{name}.json.tmp"
        tmp.write_text(json.dumps(obj))
        tmp.replace(CACHE / f"{name}.json")

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        last_hist = 0.0
        while True:
            try:
                self.refresh()
                self.error = None
            except Exception as e:
                log.warning("refresh failed: %s", e)
                self.error = str(e)
            if time.time() - last_hist > 86400:
                try:
                    self.refresh_history()
                    last_hist = time.time()
                except Exception as e:
                    log.warning("history refresh failed: %s", e)
            time.sleep(1800 if self.error is None else 120)

    def refresh(self):
        now = dt.datetime.now(UTC)
        raw = dict(self.raw)
        cur = _get("current")["RaceTable"]
        season = int(cur.get("season") or now.year)
        races = cur.get("Races", [])
        if not any(s.end > now for r in parse_races(races) for s in r["sessions"]):
            try:
                races = races + _get(str(season + 1))["RaceTable"].get("Races", [])
            except Exception as e:
                log.info("next season not available: %s", e)
        raw["races"] = races
        raw["drivers"], raw["teams"], raw["standings_round"] = self._standings(season)
        res = _get("current/last/results")["RaceTable"].get("Races") or \
            _get(f"{season - 1}/last/results")["RaceTable"].get("Races") or []
        raw["last_result"] = res[0] if res else None
        with self.lock:
            self.raw = raw
            self.races = parse_races(races)
        nr = self.next_race(now)
        if nr:
            raw["track"] = self._track(nr)
            raw["weather"] = self._weather(self.next_session(now))
        with self.lock:
            self.raw = raw
        self._save("store", raw)

    def _standings(self, season):
        out = []
        rnd = None
        for kind, key in (("driverStandings", "DriverStandings"), ("constructorStandings", "ConstructorStandings")):
            lists = _get(f"current/{kind}")["StandingsTable"]["StandingsLists"] or \
                _get(f"{season - 1}/{kind}")["StandingsTable"]["StandingsLists"]
            if not lists:
                out.append([])
                continue
            sl = lists[0]
            rnd = f"{sl['season']} round {sl['round']}"
            prev = {}
            if int(sl["round"]) > 1:
                pl = _get(f"{sl['season']}/{int(sl['round']) - 1}/{kind}")["StandingsTable"]["StandingsLists"]
                for e in (pl[0][key] if pl else []):
                    ident = e["Driver"]["driverId"] if "Driver" in e else e["Constructor"]["constructorId"]
                    prev[ident] = int(e.get("position") or e.get("positionText") or 0)
            rows = []
            for e in sl[key]:
                pos = int(e.get("position") or 0)
                if "Driver" in e:
                    d = e["Driver"]
                    team = (e.get("Constructors") or [{}])[-1]
                    rows.append({"pos": pos, "name": f"{d.get('givenName', '')[:1]}. {d.get('familyName', '')}",
                                 "code": d.get("code", ""), "team": team.get("name", ""),
                                 "team_id": team.get("constructorId", ""), "points": e.get("points", "0"),
                                 "move": prev[d["driverId"]] - pos if d["driverId"] in prev else 0})
                else:
                    c = e["Constructor"]
                    rows.append({"pos": pos, "name": c.get("name", ""), "team_id": c.get("constructorId", ""),
                                 "points": e.get("points", "0"), "wins": e.get("wins", "0"),
                                 "move": prev[c["constructorId"]] - pos if c["constructorId"] in prev else 0})
            out.append(rows)
        return out[0], out[1], rnd

    def _track(self, race):
        cid = race["circuit"].get("circuitId", "")
        info = {"circuit_id": cid, "season": race["season"]}
        hist = _get(f"circuits/{cid}/races", limit=1)
        info["held"] = int(hist.get("total", 0))
        first = hist["RaceTable"].get("Races") or []
        info["first"] = first[0]["season"] if first else None
        prev = _get(f"{race['season'] - 1}/circuits/{cid}/results")["RaceTable"].get("Races") or []
        if prev and prev[0].get("Results"):
            res = prev[0]["Results"]
            w = res[0]
            info["laps"] = w.get("laps")
            info["winner"] = f"{w['Driver'].get('givenName', '')} {w['Driver'].get('familyName', '')}"
            info["winner_team"] = w.get("Constructor", {}).get("name", "")
            fl = [r for r in res if r.get("FastestLap", {}).get("rank") == "1"]
            if fl:
                info["fastest"] = fl[0]["FastestLap"].get("Time", {}).get("time")
                info["fastest_by"] = fl[0]["Driver"].get("familyName", "")
        return info

    def _weather(self, session):
        if not session:
            return None
        now = dt.datetime.now(UTC)
        if session.start - now > dt.timedelta(days=15):
            return None
        loc = session.race["circuit"].get("Location", {})
        r = requests.get(METEO, params={
            "latitude": loc.get("lat"), "longitude": loc.get("long"), "timezone": "GMT", "forecast_days": 16,
            "hourly": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m"},
            headers=HEADERS, timeout=20)
        r.raise_for_status()
        h = r.json()["hourly"]
        key = session.start.strftime("%Y-%m-%dT%H:00")
        if key not in h["time"]:
            return None
        i = h["time"].index(key)
        return {"session": session.start.isoformat(), "temp": h["temperature_2m"][i],
                "rain": h["precipitation_probability"][i], "wind": h["wind_speed_10m"][i],
                "desc": WMO.get(h["weather_code"][i], "")}

    def refresh_history(self):
        year = dt.datetime.now(UTC).year
        for y in range(1950, year + 1):
            if str(y) in self.history and y < year - 1:
                continue
            races = _get(f"{y}/results/1")["RaceTable"].get("Races", [])
            rows = []
            for r in races:
                if not r.get("Results"):
                    continue
                w = r["Results"][0]
                d = w["Driver"]
                rows.append({"d": r["date"], "s": int(r["season"]), "race": r["raceName"],
                             "drv": f"{d.get('givenName', '')} {d.get('familyName', '')}",
                             "did": d["driverId"], "dob": d.get("dateOfBirth", ""),
                             "nat": d.get("nationality", ""), "team": w.get("Constructor", {}).get("name", "")})
            self.history[str(y)] = rows
            self._save("history", self.history)
            time.sleep(8)
        self._save("history", self.history)
        self._otd = None

    def next_session(self, now):
        for r in self.races:
            for s in r["sessions"]:
                if s.end > now:
                    return s
        return None

    def next_race(self, now):
        s = self.next_session(now)
        return s.race if s else None

    def active_session(self, now, before=10, after=20):
        for r in self.races:
            for s in r["sessions"]:
                if s.start - dt.timedelta(minutes=before) <= now <= s.end + dt.timedelta(minutes=after):
                    return s
        return None

    def weather_for(self, session):
        w = self.raw.get("weather")
        return w if w and session and w.get("session") == session.start.isoformat() else None

    def track_for(self, race):
        t = self.raw.get("track")
        return t if t and race and t.get("circuit_id") == race["circuit"].get("circuitId") else None

    def on_this_day(self, today):
        if self._otd and self._otd[0] == today:
            return self._otd[1]
        wins = sorted((w for rows in self.history.values() for w in rows), key=lambda w: w["d"])
        if not wins:
            return None
        total = collections.Counter(w["did"] for w in wins)
        count = collections.Counter()
        events, drivers = [], {}
        for w in wins:
            count[w["did"]] += 1
            events.append(("win", w["d"][5:], dict(w, n=count[w["did"]], total=total[w["did"]])))
            drivers[w["did"]] = w
        for did, w in drivers.items():
            if len(w.get("dob", "")) == 10:
                events.append(("born", w["dob"][5:], dict(w, total=total[did])))
        result = None
        for off in range(8):
            mds = {(today + dt.timedelta(days=o)).strftime("%m-%d") for o in {off, -off}}
            hits = [e for e in events if e[1] in mds]
            if hits:
                label = "ON THIS DAY" if off == 0 else "THIS WEEK IN HISTORY"

                def score(e):
                    kind, _, w = e
                    if kind == "born":
                        return w["total"] * 0.8 + 5
                    return w["total"] + (40 if w["n"] == 1 else 0) + (10 if w["n"] == w["total"] else 0)

                hits.sort(key=score, reverse=True)
                rnd = random.Random(today.toordinal())
                primary = rnd.choice(hits[:3])
                others = [h for h in hits[:6] if h is not primary][:2]
                result = {"label": label, "primary": self._fmt(primary), "others": [self._fmt(o) for o in others]}
                break
        self._otd = (today, result)
        return result

    @staticmethod
    def _fmt(e):
        kind, _, w = e
        if kind == "win":
            year = int(w["d"][:4])
            title = f"{w['drv']} wins the {w['s']} {w['race']}"
            if w["n"] == 1:
                detail = f"First career Grand Prix win, for {w['team']}"
            elif w["n"] == w["total"]:
                detail = f"Final career win ({w['total']} in total), for {w['team']}"
            else:
                detail = f"{ordinal(w['n'])} of {w['total']} career wins, for {w['team']}"
            return {"year": year, "date": w["d"], "title": title, "detail": detail}
        year = int(w["dob"][:4])
        gp = "Grand Prix" if w["total"] == 1 else "Grands Prix"
        return {"year": year, "date": w["dob"], "title": f"{w['drv']} is born",
                "detail": f"{w['nat']} driver who went on to win {w['total']} {gp}"}
