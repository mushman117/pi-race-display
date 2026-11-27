import logging
import os
import threading

import pygame

from .ui import H, W

log = logging.getLogger("output")


DRAG_PX = 15


class Framebuffer:
    def __init__(self, dev="/dev/fb0", rotate=0):
        self.rotate = rotate == 180
        base = f"/sys/class/graphics/{os.path.basename(dev)}"
        self.bpp = int(open(f"{base}/bits_per_pixel").read())
        self.fw, self.fh = (int(x) for x in open(f"{base}/virtual_size").read().split(","))
        try:
            self.stride = int(open(f"{base}/stride").read())
        except OSError:
            self.stride = self.fw * self.bpp // 8
        self.fd = os.open(dev, os.O_WRONLY)
        self.conv = pygame.Surface((W, H), 0, 16, (0xF800, 0x07E0, 0x001F, 0)) if self.bpp == 16 else None
        self.tobytes = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
        log.info("framebuffer %s: %dx%d %d bpp, stride %d", dev, self.fw, self.fh, self.bpp, self.stride)

    def present(self, surf):
        if self.rotate:
            surf = pygame.transform.flip(surf, True, True)
        if self.conv:
            self.conv.blit(surf, (0, 0))
            data = self.conv.get_buffer().raw
        else:
            data = self.tobytes(surf, "BGRA")
        row = len(data) // H
        out = W * self.bpp // 8
        if self.stride == row == out:
            os.pwrite(self.fd, data, 0)
        else:
            for y in range(min(H, self.fh)):
                os.pwrite(self.fd, data[y * row:y * row + out], y * self.stride)


class SDLOutput:
    def present(self, surf):
        pygame.display.flip()


class Touch:
    def __init__(self, rotate=0):
        self.rotate = rotate == 180
        self._lock = threading.Lock()
        self._taps = []
        self._down_at = None
        self._down_pos = (0, 0)
        self._pos = [0, 0]
        self._consumed = False
        self._dragging = False
        self._last_y = 0
        self._dy = 0
        try:
            import evdev
        except ImportError:
            log.warning("python3-evdev missing: touch disabled")
            return
        self.evdev = evdev
        threading.Thread(target=self._run, daemon=True).start()

    def _find(self):
        e = self.evdev.ecodes
        for path in self.evdev.list_devices():
            d = self.evdev.InputDevice(path)
            caps = d.capabilities()
            abs_codes = [c[0] if isinstance(c, tuple) else c for c in caps.get(e.EV_ABS, [])]
            if e.BTN_TOUCH in caps.get(e.EV_KEY, []) or e.ABS_MT_TRACKING_ID in abs_codes:
                log.info("touch device: %s", d.name)
                return d, abs_codes
        return None, []

    def _scale(self, d, code, size):
        try:
            info = d.absinfo(code)
            span = max(1, info.max - info.min)
            return lambda v: int((v - info.min) * (size - 1) / span)
        except OSError:
            return lambda v: v

    def _run(self):
        import time
        e = self.evdev.ecodes
        while True:
            try:
                d, codes = self._find()
                if not d:
                    time.sleep(10)
                    continue
                xc = e.ABS_MT_POSITION_X if e.ABS_MT_POSITION_X in codes else e.ABS_X
                yc = e.ABS_MT_POSITION_Y if e.ABS_MT_POSITION_Y in codes else e.ABS_Y
                sx, sy = self._scale(d, xc, W), self._scale(d, yc, H)
                for ev in d.read_loop():
                    if ev.type == e.EV_ABS and ev.code in (xc, e.ABS_X):
                        self._pos[0] = sx(ev.value)
                    elif ev.type == e.EV_ABS and ev.code in (yc, e.ABS_Y):
                        self._pos[1] = sy(ev.value)
                    elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                        self._press(ev.value == 1)
                    elif ev.type == e.EV_ABS and ev.code == e.ABS_MT_TRACKING_ID:
                        self._press(ev.value >= 0)
                    elif ev.type == e.EV_SYN and self._down_at is not None:
                        self._moved(self._map(self._pos))
            except OSError as err:
                log.warning("touch: %s", err)
                time.sleep(5)

    def _moved(self, p):
        with self._lock:
            if self._down_pos is None:
                self._down_pos, self._last_y = p, p[1]
                return
            if not self._dragging and abs(p[1] - self._down_pos[1]) + abs(p[0] - self._down_pos[0]) > DRAG_PX:
                self._dragging = self._consumed = True
            if self._dragging:
                self._dy += p[1] - self._last_y
            self._last_y = p[1]

    def drag(self):
        with self._lock:
            dy, self._dy = self._dy, 0
        return dy

    def _map(self, p):
        x, y = p
        return (W - 1 - x, H - 1 - y) if self.rotate else (x, y)

    def _press(self, down):
        import time
        with self._lock:
            if down and self._down_at is None:
                self._down_at = time.monotonic()
                self._down_pos = None
                self._consumed = self._dragging = False
                self._dy = 0
            elif not down and self._down_at is not None:
                if not self._consumed:
                    self._taps.append(self._down_pos or self._map(self._pos))
                self._down_at = None

    def held(self):
        import time
        with self._lock:
            return 0.0 if self._down_at is None or self._consumed else time.monotonic() - self._down_at

    def consume(self):
        with self._lock:
            self._consumed = True

    def taps(self):
        with self._lock:
            t, self._taps = self._taps, []
        return t


class MouseTouch:
    def __init__(self):
        self._taps, self._down_at, self._down_pos, self._consumed = [], None, (0, 0), False
        self._dragging, self._last_y, self._dy = False, 0, 0

    def drag(self):
        dy, self._dy = self._dy, 0
        return dy

    def handle(self, ev):
        import time
        if ev.type == pygame.MOUSEMOTION and self._down_at is not None:
            x, y = ev.pos
            if not self._dragging and abs(y - self._down_pos[1]) + abs(x - self._down_pos[0]) > DRAG_PX:
                self._dragging = self._consumed = True
            if self._dragging:
                self._dy += y - self._last_y
            self._last_y = y
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            self._down_at, self._down_pos, self._consumed = time.monotonic(), ev.pos, False
            self._dragging, self._last_y, self._dy = False, ev.pos[1], 0
        elif ev.type == pygame.MOUSEBUTTONUP and self._down_at is not None:
            if not self._consumed:
                self._taps.append(ev.pos)
            self._down_at = None

    def held(self):
        import time
        return 0.0 if self._down_at is None or self._consumed else time.monotonic() - self._down_at

    def consume(self):
        self._consumed = True

    def taps(self):
        t, self._taps = self._taps, []
        return t


class Backlight:
    def __init__(self):
        import glob
        devs = sorted(glob.glob("/sys/class/backlight/*"))
        self.path = devs[0] if devs else None
        self.on, self.level, self._warned = True, 100, False
        self.max = 255
        if self.path:
            try:
                self.max = int(open(f"{self.path}/max_brightness").read())
            except (OSError, ValueError):
                pass
            log.info("backlight %s (max %d)", self.path, self.max)
        else:
            log.info("no backlight control found")

    def _write(self, name, value):
        if not self.path:
            return
        try:
            with open(f"{self.path}/{name}", "w") as f:
                f.write(str(value))
        except OSError as e:
            if not self._warned and name == "brightness":
                log.warning("backlight not writable (%s): run ./install.sh again", e)
                self._warned = True

    def set_level(self, pct):
        self.level = max(10, min(100, int(pct)))
        if self.on:
            self._write("brightness", max(1, round(self.max * self.level / 100)))

    def power(self, on):
        if on == self.on:
            return
        self.on = on
        if on:
            self._write("bl_power", 0)
            self.set_level(self.level)
        else:
            self._write("brightness", 0)
            self._write("bl_power", 4)
