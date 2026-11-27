import argparse
import datetime as dt
import logging
import os
import subprocess
import time
import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

import pygame

from . import output, screens, ui
from .data import Store
from .livetiming import DemoTiming, LiveTiming
from .race import Alerts

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = {
    "display": {"output": "fb", "rotate": 0, "rotate_seconds": 15, "places": 10, "fps": 5, "screens": list(screens.ROTATION)},
    "live": {"enabled": True, "stream_delay": 0},
    "general": {"timezone": ""},
}


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


def menu_tap(menu, pos, surf):
    hit = next((k for k, r in screens.MENU_BUTTONS.items()
                if r.x <= pos[0] < r.x + r.w and r.y <= pos[1] < r.y + r.h), None)
    menu["since"] = time.monotonic()
    if hit == "cancel":
        return None
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

        held = touch.held()
        if held >= LONG_PRESS and menu is None:
            touch.consume()
            menu = {"since": time.monotonic()}
        for pos in touch.taps():
            if time.monotonic() - last_tap < 0.4:
                continue
            last_tap = time.monotonic()
            if menu is not None:
                menu = menu_tap(menu, pos, surf)
            elif alerts.current:
                alerts.dismiss()
            elif finished_at is not None and live_key != done_key:
                done_key = live_key
            else:
                idx, switched = (idx + 1) % len(names), time.monotonic()
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

        a = alerts.active() if want else None
        has_timing = bool((live.state.get("TimingData") or {}).get("Lines"))
        try:
            if menu is not None:
                screens.system_menu(surf, menu)
            elif a:
                screens.alert(surf, a)
            elif want and has_timing:
                screens.live(surf, ctx, live.state)
            elif session and not args.demo and key != done_key:
                screens.countdown(surf, ctx)
            else:
                if time.monotonic() - switched >= rotate:
                    idx, switched = (idx + 1) % len(names), time.monotonic()
                screens.ROTATION[names[idx]](surf, ctx)
        except Exception:
            logging.exception("draw failed")
            ui.message(surf, "DISPLAY ERROR", "Check the log: journalctl -u racedisplay")
            idx, switched = (idx + 1) % len(names), time.monotonic()
        if menu is None and held > 1.0:
            screens.hold_hint(surf, (held - 1.0) / (LONG_PRESS - 1.0))
        out.present(surf)
        clock.tick(cfg["display"]["fps"])
