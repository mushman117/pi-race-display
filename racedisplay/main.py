import argparse
import datetime as dt
import json
import logging
import os
import subprocess
import time
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import pygame

from . import output, screens, ui
from .data import CACHE, Store
from .livetiming import DemoTiming, LiveTiming
from .race import Alerts

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "display": {"output": "fb", "rotate": 0, "rotate_seconds": 15, "places": 10, "fps": 5, "screens": list(screens.ROTATION)},
    "live": {"enabled": True, "stream_delay": 0},
    "general": {"timezone": ""},
    "power": {"mode": "always", "brightness": 80, "sleep": True, "sleep_start": "23:00", "sleep_end": "07:00",
              "wake_for_sessions": True, "auto_dim": True, "dim_percent": 50},
}
SETTINGS = CACHE / "settings.json"
WAKE_SECONDS = 120


def load_settings(cfg):
    p = cfg["power"]
    s = {"mode": p["mode"], "brightness": int(p["brightness"]), "sleep": bool(p["sleep"]),
         "sleep_start": p["sleep_start"], "sleep_end": p["sleep_end"], "auto_dim": bool(p["auto_dim"])}
    try:
        s.update(json.loads(SETTINGS.read_text()))
    except (OSError, ValueError):
        pass
    if s["mode"] not in ("always", "race_weekends"):
        s["mode"] = "always"
    for k in ("sleep_start", "sleep_end"):
        if not _hm(s[k]):
            s[k] = DEFAULTS["power"][k]
    s["dim_percent"] = max(10, min(100, int(p["dim_percent"])))
    return s


def shift_time(v, minutes):
    t = _hm(v)
    total = (t.hour * 60 + t.minute + minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def save_settings(s):
    try:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(s))
    except OSError as e:
        logging.warning("settings not saved: %s", e)


def _hm(v):
    try:
        h, m = str(v).split(":")
        return dt.time(int(h), int(m))
    except ValueError:
        return None


def asleep(local, start, end):
    if not start or not end or start == end:
        return False
    t = local.time()
    return start <= t < end if start < end else (t >= start or t < end)


def race_weekend(store, now):
    for r in store.races:
        if r["sessions"] and r.get("date"):
            if r["sessions"][0].start - dt.timedelta(hours=24) <= now <= r["date"] + dt.timedelta(hours=4):
                return True
    return False


def load_config(path):
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if path.exists():
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    if cfg["display"]["places"] not in (5, 10, 15, 20):
        cfg["display"]["places"] = 10
    return cfg


LONG_PRESS = 3.0
FINISHED = ("Finished", "Finalised", "Ends")
HOLD_AFTER = 15 * 60
POWER = {"reboot": ("REBOOTING", "Back in about a minute", "reboot"),
         "shutdown": ("SHUTTING DOWN", "Unplug when the green light stops flashing", "poweroff")}


class Ctx:
    pass


class types_backlight:
    level = 100

    def set_level(self, pct):
        self.level = pct

    def power(self, on):
        pass


def menu_tap(menu, pos, surf, ctx):
    hit = next((k for k, r in screens.menu_buttons(menu).items()
                if r.x <= pos[0] < r.x + r.w and r.y <= pos[1] < r.y + r.h), None)
    menu["since"] = time.monotonic()
    if hit != "shutdown":
        menu["confirm"] = False
    if hit == "cancel":
        return None
    if hit in ("settings", "back"):
        menu["page"] = "settings" if hit == "settings" else "main"
        return menu
    st = ctx.settings
    if hit in ("sleep_toggle", "dim_toggle"):
        key = "sleep" if hit == "sleep_toggle" else "auto_dim"
        st[key] = not st[key]
        save_settings(st)
        return menu
    if hit in ("start_minus", "start_plus", "end_minus", "end_plus"):
        key = "sleep_start" if hit.startswith("start") else "sleep_end"
        st[key] = shift_time(st[key], 30 if hit.endswith("plus") else -30)
        save_settings(st)
        return menu
    if hit in ("dim", "bright"):
        ctx.settings["brightness"] = max(10, min(100, ctx.settings["brightness"] + (10 if hit == "bright" else -10)))
        save_settings(ctx.settings)
        return menu
    if hit in ("mode_always", "mode_weekend"):
        ctx.settings["mode"] = "always" if hit == "mode_always" else "race_weekends"
        save_settings(ctx.settings)
        return menu
    if hit == "shutdown" and not menu.get("confirm"):
        menu["confirm"] = True
        return menu
    if hit in POWER:
        title, sub, verb = POWER[hit]
        screens.power_message(surf, title, sub)
        ui.present(surf)
        r = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", verb], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            logging.error("%s failed: %s", verb, r.stderr.strip())
            return {"since": time.monotonic(), "message": "Not permitted: run ./install.sh again"}
        time.sleep(30)
    return menu


def main():
    ap = argparse.ArgumentParser(description="Pi Race Display")
    ap.add_argument("--config", default=str(ROOT / "config.toml"))
    ap.add_argument("--demo", action="store_true", help="simulated race to test the live screens")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of full screen")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    use_fb = cfg["display"]["output"] == "fb" and not args.windowed
    if use_fb:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    elif not args.windowed:
        os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    pygame.init()
    surf = pygame.display.set_mode((ui.W, ui.H), 0 if args.windowed or use_fb else pygame.FULLSCREEN)
    pygame.display.set_caption("Race Display")
    pygame.mouse.set_visible(args.windowed)
    out = output.Framebuffer(rotate=int(cfg["display"]["rotate"])) if use_fb else output.SDLOutput()
    ui.present = out.present
    touch_rotate = int(cfg["display"].get("touch_rotate", cfg["display"]["rotate"]))
    touch = output.Touch(rotate=touch_rotate) if use_fb else output.MouseTouch()
    menu = None
    live_key = done_key = finished_at = None
    seen_started = False
    screens.boot(surf)

    store = Store()
    store.start()
    delay = cfg["live"]["stream_delay"]
    live = DemoTiming(delay) if args.demo else LiveTiming(delay)
    alerts = Alerts()
    names = [n for n in cfg["display"]["screens"] if n in screens.ROTATION] or list(screens.ROTATION)
    rotate = max(5, int(cfg["display"]["rotate_seconds"]))
    ctx = Ctx()
    ctx.store, ctx.places, ctx.delay, ctx.alerts = store, cfg["display"]["places"], live.delay, alerts
    ctx.scroll = screens.Scroll()
    ctx.settings = load_settings(cfg)
    ctx.backlight = output.Backlight() if use_fb else types_backlight()
    ctx.backlight.set_level(ctx.settings["brightness"])
    wake_until = bright_until = 0.0
    dimmed = False
    screen_on = True
    ctx.tz = ZoneInfo(cfg["general"]["timezone"]) if cfg["general"].get("timezone") else None
    idx, switched, last_tap = 0, time.monotonic(), 0.0
    clock = pygame.time.Clock()

    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q)):
                live.stop()
                pygame.quit()
                return
            if isinstance(touch, output.MouseTouch):
                touch.handle(ev)
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_SPACE, pygame.K_RIGHT):
                idx, switched = (idx + 1) % len(names), time.monotonic()

        dy = touch.drag()
        held = touch.held()
        if held >= LONG_PRESS and menu is None:
            touch.consume()
            menu = {"since": time.monotonic()}
        for pos in touch.taps():
            if time.monotonic() - last_tap < 0.4:
                continue
            last_tap = time.monotonic()
            if not screen_on:
                wake_until = bright_until = time.monotonic() + WAKE_SECONDS
                continue
            if dimmed and menu is None:
                bright_until = time.monotonic() + WAKE_SECONDS
                continue
            bright_until = time.monotonic() + WAKE_SECONDS if bright_until > time.monotonic() else bright_until
            if time.monotonic() < wake_until:
                wake_until = time.monotonic() + WAKE_SECONDS
            if menu is not None:
                menu = menu_tap(menu, pos, surf, ctx)
            elif alerts.current:
                alerts.dismiss()
            elif finished_at is not None and live_key != done_key:
                done_key = live_key
            else:
                idx, switched = (idx + 1) % len(names), time.monotonic()
                ctx.scroll.reset()
        if dy and menu is None:
            ctx.scroll.drag(dy)
        if menu is not None and time.monotonic() - menu["since"] > 10:
            menu = None

        ctx.now = dt.datetime.now(dt.timezone.utc)
        session = store.active_session(ctx.now)
        key = session.start.isoformat() if session else None
        if key != live_key:
            live_key, finished_at, seen_started = key, None, False
        want = args.demo or (cfg["live"]["enabled"] and session is not None and key != done_key)
        if want and not live.running:
            alerts.reset()
            live.start()
        elif not want and live.running:
            live.stop()
            alerts.reset()
        if live.pump():
            alerts.update(live.state)
        if session and not args.demo and live.running:
            status = (live.state.get("SessionStatus") or {}).get("Status")
            seen_started = seen_started or status == "Started"
            started = seen_started or ctx.now > session.start + dt.timedelta(minutes=10)
            if status in FINISHED and finished_at is None and started:
                finished_at = time.monotonic()
                logging.info("session finished; rotation resumes in %d min", HOLD_AFTER // 60)
            if finished_at is not None and time.monotonic() - finished_at > HOLD_AFTER:
                done_key = key

        local = ctx.now.astimezone(ctx.tz) if ctx.tz else ctx.now.astimezone()
        on = True
        if ctx.settings["mode"] == "race_weekends" and not race_weekend(store, ctx.now):
            on = False
        if ctx.settings["sleep"] and asleep(local, _hm(ctx.settings["sleep_start"]), _hm(ctx.settings["sleep_end"])):
            on = False
        if cfg["power"]["wake_for_sessions"] and want and not args.demo:
            on = True
        if args.demo or menu is not None or time.monotonic() < wake_until:
            on = True
        if on != screen_on:
            logging.info("screen %s", "on" if on else "off")
            screen_on = on
            ctx.backlight.power(on)
        live_now = want and bool((live.state.get("TimingData") or {}).get("Lines"))
        dimmed = (screen_on and ctx.settings["auto_dim"] and not live_now and menu is None
                  and time.monotonic() >= bright_until)
        target = ctx.settings["brightness"]
        if dimmed:
            target = max(10, target * ctx.settings["dim_percent"] // 100)
        if screen_on and target != ctx.backlight.level:
            ctx.backlight.set_level(target)
        if not screen_on:
            if live.running:
                alerts.active()
            clock.tick(2)
            continue

        a = alerts.active() if want else None
        has_timing = bool((live.state.get("TimingData") or {}).get("Lines"))
        try:
            if menu is not None:
                screens.system_menu(surf, menu, ctx.settings)
            elif a:
                screens.alert(surf, a)
            elif want and has_timing:
                screens.live(surf, ctx, live.state)
            elif session and not args.demo and key != done_key:
                screens.countdown(surf, ctx)
            else:
                elapsed = time.monotonic() - switched
                if elapsed >= rotate and (ctx.scroll.done or elapsed >= 120):
                    idx, switched = (idx + 1) % len(names), time.monotonic()
                    ctx.scroll.reset()
                screens.ROTATION[names[idx]](surf, ctx)
        except Exception:
            logging.exception("draw failed")
            ui.message(surf, "DISPLAY ERROR", "Check the log: journalctl -u racedisplay")
            idx, switched = (idx + 1) % len(names), time.monotonic()
            ctx.scroll.reset()
        if menu is None and held > 1.0:
            screens.hold_hint(surf, (held - 1.0) / (LONG_PRESS - 1.0))
        out.present(surf)
        clock.tick(12 if ctx.scroll.active and not want else cfg["display"]["fps"])
