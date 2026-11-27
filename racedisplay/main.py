import argparse
import datetime as dt
import logging
import os
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


class Ctx:
    pass


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
    touch = output.Touch() if use_fb else None
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
            tap = ev.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN) or (
                ev.type == pygame.KEYDOWN and ev.key in (pygame.K_SPACE, pygame.K_RIGHT))
            if tap and time.monotonic() - last_tap > 0.4:
                last_tap = time.monotonic()
                if alerts.current:
                    alerts.dismiss()
                else:
                    idx, switched = (idx + 1) % len(names), time.monotonic()

        if touch and touch.tapped() and time.monotonic() - last_tap > 0.4:
            last_tap = time.monotonic()
            if alerts.current:
                alerts.dismiss()
            else:
                idx, switched = (idx + 1) % len(names), time.monotonic()

        ctx.now = dt.datetime.now(dt.timezone.utc)
        session = store.active_session(ctx.now)
        want = args.demo or (cfg["live"]["enabled"] and session is not None)
        if want and not live.running:
            alerts.reset()
            live.start()
        elif not want and live.running:
            live.stop()
            alerts.reset()
        if live.pump():
            alerts.update(live.state)

        a = alerts.active() if want else None
        has_timing = bool((live.state.get("TimingData") or {}).get("Lines"))
        try:
            if a:
                screens.alert(surf, a)
            elif want and has_timing:
                screens.live(surf, ctx, live.state)
            elif session and not args.demo:
                screens.countdown(surf, ctx)
            else:
                if time.monotonic() - switched >= rotate:
                    idx, switched = (idx + 1) % len(names), time.monotonic()
                screens.ROTATION[names[idx]](surf, ctx)
        except Exception:
            logging.exception("draw failed")
            ui.message(surf, "DISPLAY ERROR", "Check the log: journalctl -u racedisplay")
            idx, switched = (idx + 1) % len(names), time.monotonic()
        out.present(surf)
        clock.tick(cfg["display"]["fps"])
