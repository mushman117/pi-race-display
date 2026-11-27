import datetime as dt

import pygame

from . import race, ui
from .ui import (ACCENT, BG, DIM, H, LINE, PANEL, TEXT, W, YELLOW, arrow, header, message, team_colour,
                 text, tyre, wrap)


def _local(t, ctx):
    return t.astimezone(ctx.tz) if ctx.tz else t.astimezone()


def _clock(ctx):
    return _local(ctx.now, ctx).strftime("%H:%M")


def boot(surf):
    centres = [(W // 2 + (i - 2) * 120, H // 2) for i in range(5)]
    for lit in range(6):
        surf.fill(BG)
        for i, c in enumerate(centres):
            pygame.draw.rect(surf, PANEL, (c[0] - 50, c[1] - 90, 100, 180), border_radius=14)
            pygame.draw.circle(surf, ACCENT if i < lit else (50, 15, 15), (c[0], c[1] - 40), 34)
            pygame.draw.circle(surf, ACCENT if i < lit else (50, 15, 15), (c[0], c[1] + 40), 34)
        ui.present(surf)
        pygame.time.wait(550)
    pygame.time.wait(500)
    surf.fill(BG)
    text(surf, "LIGHTS OUT", (W // 2, H // 2), 48, bold=True, anchor="center")
    ui.present(surf)
    pygame.time.wait(800)


def countdown(surf, ctx):
    header(surf, "NEXT SESSION", _clock(ctx))
    s = ctx.store.next_session(ctx.now)
    if not s:
        text(surf, "SEASON COMPLETE", (W // 2, 200), 40, bold=True, anchor="center")
        text(surf, "Next season dates to be confirmed", (W // 2, 250), 22, DIM, anchor="center")
        return
    r = s.race
    text(surf, r["name"].upper(), (24, 58), 30, bold=True, maxw=W - 48)
    text(surf, s.name, (24, 100), 24, DIM)
    delta = max(0, int((s.start - ctx.now).total_seconds()))
    vals = [(delta // 86400, "DAYS"), (delta % 86400 // 3600, "HRS"), (delta % 3600 // 60, "MIN"), (delta % 60, "SEC")]
    for i, (v, label) in enumerate(vals):
        box = pygame.Rect(24 + i * 190, 142, 172, 118)
        pygame.draw.rect(surf, PANEL, box, border_radius=10)
        text(surf, f"{v:02d}", (box.centerx, box.y + 8), 64, bold=True, anchor="midtop")
        text(surf, label, (box.centerx, box.bottom - 26), 16, DIM, anchor="midtop")
    if delta == 0:
        text(surf, "SESSION UNDER WAY: waiting for live timing", (24, 276), 22, YELLOW, bold=True)
    else:
        text(surf, _local(s.start, ctx).strftime("%a %d %b, %H:%M") + " your time", (24, 276), 22)
    w = ctx.store.weather_for(s)
    if w:
        text(surf, f"Track forecast: {w['temp']:.0f}°C, {w['desc']}, rain {w['rain']}%, wind {w['wind']:.0f} km/h",
             (24, 310), 19, DIM, maxw=W - 48)
    pygame.draw.line(surf, LINE, (24, 352), (W - 24, 352))
    sessions = r["sessions"]
    cw = (W - 48) // max(1, len(sessions))
    for i, x in enumerate(sessions):
        col = ACCENT if x is s else DIM if x.end < ctx.now else TEXT
        text(surf, x.short, (24 + i * cw, 366), 16, col, bold=True)
        lt = _local(x.start, ctx)
        text(surf, lt.strftime("%a"), (24 + i * cw, 392), 18, col)
        text(surf, lt.strftime("%H:%M"), (24 + i * cw, 416), 18, col)


def next_track(surf, ctx):
    header(surf, "NEXT TRACK", _clock(ctx))
    r = ctx.store.next_race(ctx.now)
    if not r:
        message(surf, "NO UPCOMING RACE", "Waiting for the next calendar")
        return
    c = r["circuit"]
    loc = c.get("Location", {})
    text(surf, r["name"].upper(), (24, 58), 30, bold=True, maxw=W - 48)
    text(surf, c.get("circuitName", ""), (24, 100), 22, maxw=W - 48)
    text(surf, f"{loc.get('locality', '')}, {loc.get('country', '')}", (24, 130), 18, DIM)
    t = ctx.store.track_for(r) or {}
    total = sum(1 for x in ctx.store.races if x["season"] == r["season"])
    prev = r["season"] - 1
    facts = [
        ("ROUND", f"{r['round']} of {total}"),
        ("RACE DAY", _local(r["date"], ctx).strftime("%a %d %b") if r.get("date") else "TBC"),
        ("RACES HELD", f"{t['held']} since {t['first']}" if t.get("held") else "First race here" if t else "..."),
        ("RACE LAPS", t.get("laps") or "-"),
        (f"{prev} WINNER", t.get("winner") or "-"),
        (f"{prev} FASTEST LAP", f"{t['fastest']} {t.get('fastest_by', '')}" if t.get("fastest") else "-"),
    ]
    for i, (label, value) in enumerate(facts):
        box = pygame.Rect(24 + (i % 3) * 254, 172 + (i // 3) * 140, 242, 126)
        pygame.draw.rect(surf, PANEL, box, border_radius=10)
        text(surf, label, (box.x + 16, box.y + 16), 15, DIM, bold=True)
        lines = wrap(value, 24, box.w - 32, bold=True)[:2]
        for j, ln in enumerate(lines):
            text(surf, ln, (box.x + 16, box.y + 50 + j * 32), 24, bold=True, maxw=box.w - 32)


class Scroll:
    SPEED = 45
    PAUSE = 3
    MANUAL = 10

    def __init__(self):
        self.reset()

    def reset(self):
        import time
        self.offset, self.max = 0.0, 0
        self.t0 = self.last = time.monotonic()
        self.manual_until = 0.0
        self.bottom_at = None

    @property
    def active(self):
        return self.max > 0

    @property
    def done(self):
        import time
        if self.max <= 0:
            return True
        now = time.monotonic()
        return self.bottom_at is not None and now >= self.manual_until and now - self.bottom_at >= self.PAUSE

    def update(self):
        import time
        now = time.monotonic()
        step, self.last = now - self.last, now
        if self.max <= 0 or now < self.manual_until or now - self.t0 < self.PAUSE:
            return
        if self.offset < self.max:
            self.offset = min(self.max, self.offset + self.SPEED * step)
            if self.offset >= self.max:
                self.bottom_at = now

    def drag(self, dy):
        import time
        self.offset = max(0.0, min(self.max, self.offset - dy))
        self.manual_until = time.monotonic() + self.MANUAL
        self.bottom_at = time.monotonic() if self.offset >= self.max else None


def _scroll_list(surf, ctx, top, rh, n, draw_row):
    view = pygame.Rect(0, top, W, H - top)
    sc = ctx.scroll
    sc.max = max(0, n * rh - view.h)
    sc.update()
    off = int(sc.offset)
    surf.set_clip(view)
    for i in range(n):
        y = top + i * rh - off
        if y + rh >= top and y < H:
            draw_row(i, y)
    surf.set_clip(None)
    if sc.max:
        track = view.h - 8
        knob = max(30, track * view.h // (n * rh))
        ky = top + 4 + (track - knob) * off // sc.max
        pygame.draw.rect(surf, LINE, (W - 7, top + 4, 4, track), border_radius=2)
        pygame.draw.rect(surf, DIM, (W - 7, ky, 4, knob), border_radius=2)


def driver_standings(surf, ctx):
    rows = ctx.store.raw.get("drivers") or []
    header(surf, "DRIVER STANDINGS", ctx.store.raw.get("standings_round") or "")
    if not rows:
        message(surf, "NO STANDINGS YET", "Waiting for data")
        return

    def row(i, y):
        d = rows[i]
        cy = y + 18
        pygame.draw.rect(surf, PANEL, (16, y, W - 36, 36), border_radius=6)
        text(surf, d["pos"], (56, cy), 18, bold=True, anchor="midright")
        pygame.draw.rect(surf, team_colour(d["team_id"]), (68, y + 6, 5, 24))
        text(surf, d["name"], (84, cy), 18, anchor="midleft", maxw=230)
        text(surf, d.get("team", ""), (330, cy), 15, DIM, anchor="midleft", maxw=260)
        text(surf, d["points"], (680, cy), 18, bold=True, anchor="midright")
        arrow(surf, d["move"], (706, cy), 14)

    _scroll_list(surf, ctx, 52, 40, len(rows), row)


def team_standings(surf, ctx):
    rows = ctx.store.raw.get("teams") or []
    header(surf, "TEAM STANDINGS", ctx.store.raw.get("standings_round") or "")
    if not rows:
        message(surf, "NO STANDINGS YET", "Waiting for data")
        return

    def row(i, y):
        t = rows[i]
        cy = y + 18
        pygame.draw.rect(surf, PANEL, (16, y, W - 36, 36), border_radius=6)
        text(surf, t["pos"], (56, cy), 20, bold=True, anchor="midright")
        pygame.draw.rect(surf, team_colour(t["team_id"]), (68, y + 6, 6, 24))
        text(surf, t["name"], (86, cy), 20, anchor="midleft", maxw=420)
        text(surf, f"{t['wins']} wins", (580, cy), 16, DIM, anchor="midright")
        text(surf, t["points"], (680, cy), 20, bold=True, anchor="midright")
        arrow(surf, t["move"], (706, cy), 14)

    _scroll_list(surf, ctx, 52, 40, len(rows), row)


def last_result(surf, ctx):
    r = ctx.store.raw.get("last_result")
    header(surf, "LAST RACE RESULT", _clock(ctx))
    if not r:
        message(surf, "NO RESULT YET", "Waiting for data")
        return
    text(surf, f"{r['season']} {r['raceName']}".upper(), (24, 54), 22, bold=True, maxw=W - 48)
    res = r.get("Results", [])

    def row(i, y):
        x = res[i]
        cy = y + 17
        pygame.draw.rect(surf, PANEL, (16, y, W - 36, 34), border_radius=6)
        d, c = x["Driver"], x.get("Constructor", {})
        text(surf, x["position"], (52, cy), 18, bold=True, anchor="midright")
        pygame.draw.rect(surf, team_colour(c.get("constructorId")), (62, y + 5, 5, 24))
        text(surf, f"{d.get('givenName', '')[:1]}. {d.get('familyName', '')}", (78, cy), 18, anchor="midleft", maxw=230)
        text(surf, c.get("name", ""), (320, cy), 15, DIM, anchor="midleft", maxw=170)
        status = x.get("Time", {}).get("time") or x.get("status", "")
        text(surf, status, (640, cy), 16, anchor="midright", maxw=140)
        grid, pos = race.to_int(x.get("grid")), race.to_int(x.get("position"))
        if grid and pos:
            arrow(surf, grid - pos, (666, cy), 14)
        elif grid == 0:
            text(surf, "PIT", (666, cy), 14, DIM, anchor="midleft")

    _scroll_list(surf, ctx, 88, 38, len(res), row)


def on_this_day(surf, ctx):
    today = _local(ctx.now, ctx).date()
    otd = ctx.store.on_this_day(today)
    header(surf, otd["label"] if otd else "ON THIS DAY", today.strftime("%d %B"))
    if not otd:
        message(surf, "BUILDING HISTORY", "Downloading race history, ready in a few minutes")
        return
    p = otd["primary"]
    text(surf, str(p["year"]), (24, 56), 72, ACCENT, bold=True)
    ago = today.year - p["year"]
    text(surf, f"{ago} years ago", (230, 98), 20, DIM)
    y = 150
    for ln in wrap(p["title"], 30, W - 48, bold=True)[:3]:
        text(surf, ln, (24, y), 30, bold=True)
        y += 40
    text(surf, p["detail"], (24, y + 4), 20, DIM, maxw=W - 48)
    y = max(y + 52, 330)
    if otd["others"]:
        pygame.draw.line(surf, LINE, (24, y), (W - 24, y))
        for o in otd["others"]:
            y += 14
            text(surf, str(o["year"]), (24, y), 18, ACCENT, bold=True)
            text(surf, o["title"], (90, y), 18, maxw=W - 114)
            y += 30


def live(surf, ctx, state):
    info = race.session_info(state)
    rs = race.rows(state)
    st = info["status"]
    if info["kind"] == "Race":
        if st == "Inactive" and info["lap"] <= 1:
            badge = "GRID"
        elif st in ("Finished", "Finalised", "Ends"):
            badge = "FINISHED"
        else:
            badge = "LIVE"
        right = f"LAP {info['lap']}/{info['total']}" if info["total"] else ""
    else:
        badge = "FINISHED" if st in ("Finished", "Finalised", "Ends") else "LIVE"
        rem = race.remaining(state, ctx.delay)
        part = f"Q{info['part']}  " if info["kind"] == "Qualifying" and info["part"] else ""
        right = part + (f"{rem // 3600}:{rem % 3600 // 60:02d}:{rem % 60:02d}" if rem is not None else "")
    title = f"{info['meeting']}  {info['name']}".strip().upper()
    header(surf, title, "")
    bw = 96
    pygame.draw.rect(surf, ACCENT if badge == "LIVE" else (70, 70, 80), (W - 16 - bw, 9, bw, 26), border_radius=4)
    text(surf, badge, (W - 16 - bw // 2, 22), 16, bold=True, anchor="center")
    if right:
        text(surf, right, (W - 28 - bw, 22), 20, bold=True, anchor="midright")

    n = max(1, min(ctx.places, len(rs)))
    top, bottom = 72, H - 38
    rh = (bottom - top) // n
    fs = max(12, min(30, int(rh * 0.55)))
    racing = info["kind"] == "Race"
    cols = ({"int": 330, "gap": 470, "tyre": 540, "move": 610, "laps": None} if racing else
            {"int": 360, "gap": 500, "tyre": 570, "move": None, "laps": 700})
    labels = [("INT" if racing else "BEST", cols["int"], "topright"), ("GAP", cols["gap"], "topright"),
              ("TYRE", cols["tyre"], "midtop")]
    if racing:
        labels.append(("+/-", cols["move"] + 14, "midtop"))
    else:
        labels.append(("LAPS", cols["laps"], "topright"))
    for label, x, anchor in labels:
        pos = (x, 52) if anchor != "midtop" else (x, 52)
        text(surf, label, pos, 13, DIM, bold=True, anchor=anchor)
    lead_best = race.secs(rs[0]["best"]) if rs else None
    for i, r in enumerate(rs[:n]):
        y = top + i * rh
        cy = y + rh // 2
        if i % 2 == 0:
            pygame.draw.rect(surf, PANEL, (8, y, W - 16, rh))
        col = DIM if (r["out"] or r["ko"]) else TEXT
        text(surf, r["pos"] if r["pos"] < 99 else "", (60, cy), fs, col, bold=True, anchor="midright")
        pygame.draw.rect(surf, r["colour"], (72, y + max(2, rh // 6), 6, rh - 2 * max(2, rh // 6)))
        tr = text(surf, r["tla"], (90, cy), fs, col, bold=True, anchor="midleft")
        if r["pit"]:
            text(surf, "PIT", (tr.right + 10, cy), max(11, fs - 6), YELLOW, bold=True, anchor="midleft")
        elif r["out"]:
            text(surf, "OUT", (tr.right + 10, cy), max(11, fs - 6), DIM, bold=True, anchor="midleft")
        if racing:
            iv = "LEADER" if i == 0 else r["interval"]
            gap = "" if i == 0 else r["gap"]
        else:
            iv = r["best"]
            b = race.secs(r["best"])
            gap = f"+{b - lead_best:.3f}" if i and b is not None and lead_best is not None else ""
        text(surf, iv, (cols["int"], cy), fs, col, anchor="midright")
        text(surf, gap, (cols["gap"], cy), fs, DIM, anchor="midright")
        tyre(surf, r["tyre"], (cols["tyre"], cy), max(7, min(14, rh // 3)))
        if racing and r["grid"] and r["pos"] < 99:
            arrow(surf, r["grid"] - r["pos"], (cols["move"], cy), max(12, fs - 4))
        if not racing:
            text(surf, r["laps"] or "", (cols["laps"], cy), fs, DIM, anchor="midright")

    al = getattr(ctx, "alerts", None)
    line, bg = race.status_line(state, rs, al.rc_line() if al else None)
    if bg:
        pygame.draw.rect(surf, bg, (0, H - 34, W, 34))
    else:
        pygame.draw.rect(surf, PANEL, (0, H - 34, W, 34))
    fg = (18, 18, 24) if bg in (YELLOW, race.AMBER) else TEXT
    size = next((z for z in (18, 16, 14, 12) if ui.font(z, True).size(line)[0] <= W - 32), 12)
    text(surf, line, (W // 2, H - 17), size, fg, bold=True, anchor="center", maxw=W - 32)


def alert(surf, a):
    surf.fill(a["bg"])
    if a.get("chequered"):
        sq = 20
        for row in range(3):
            for col in range(W // sq):
                if (row + col) % 2 == 0:
                    pygame.draw.rect(surf, (240, 240, 240), (col * sq, row * sq, sq, sq))
                    pygame.draw.rect(surf, (240, 240, 240), (col * sq, H - (row + 1) * sq, sq, sq))
    text(surf, a["title"], (W // 2, 150), 64, a["fg"], bold=True, anchor="center", maxw=W - 40)
    for i, car in enumerate(a.get("cars", [])[:2]):
        x = W // 2 - 160 + i * 320 if len(a["cars"]) > 1 else W // 2
        pygame.draw.rect(surf, car["colour"], (x - 90, 210, 180, 8), border_radius=3)
    if a.get("body"):
        y = 230
        for ln in wrap(a["body"], 28, W - 80, bold=True)[:5]:
            text(surf, ln, (W // 2, y), 28, a["fg"], bold=True, anchor="midtop")
            y += 40
        return
    lines = a.get("lines", [])
    if lines:
        text(surf, lines[0], (W // 2, 270), 40, a["fg"], bold=True, anchor="center", maxw=W - 40)
    for i, ln in enumerate(lines[1:3]):
        text(surf, ln, (W // 2, 330 + i * 36), 24, a["fg"], anchor="center", maxw=W - 40)


ROTATION = {"countdown": countdown, "next_track": next_track, "driver_standings": driver_standings,
            "team_standings": team_standings, "last_result": last_result, "on_this_day": on_this_day}


MENU_BUTTONS = {"reboot": pygame.Rect(40, 150, 230, 180), "shutdown": pygame.Rect(285, 150, 230, 180),
                "cancel": pygame.Rect(530, 150, 230, 180)}


def system_menu(surf, menu):
    surf.fill(BG)
    text(surf, "SYSTEM", (W // 2, 70), 40, bold=True, anchor="center")
    labels = {"reboot": ("REBOOT", PANEL), "cancel": ("CANCEL", PANEL),
              "shutdown": ("TAP AGAIN" if menu.get("confirm") else "SHUT DOWN",
                           ACCENT if menu.get("confirm") else PANEL)}
    for key, rect in MENU_BUTTONS.items():
        label, bg = labels[key]
        pygame.draw.rect(surf, bg, rect, border_radius=14)
        pygame.draw.rect(surf, LINE, rect, 2, border_radius=14)
        text(surf, label, rect.center, 28, bold=True, anchor="center")
    msg = menu.get("message") or ("Tap SHUT DOWN again to confirm" if menu.get("confirm")
                                  else "Closes automatically after 10 seconds")
    text(surf, msg, (W // 2, 400), 20, DIM, anchor="center", maxw=W - 40)


def hold_hint(surf, progress):
    pygame.draw.rect(surf, PANEL, (200, H - 60, 400, 40), border_radius=8)
    pygame.draw.rect(surf, ACCENT, (204, H - 56, int(392 * min(1, progress)), 32), border_radius=6)
    text(surf, "Keep holding for system menu", (W // 2, H - 40), 16, bold=True, anchor="center")


def power_message(surf, title, sub):
    message(surf, title, sub)
