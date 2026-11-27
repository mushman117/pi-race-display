import logging
import os
import threading

import pygame

from .ui import H, W

log = logging.getLogger("output")


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
    def __init__(self):
        self._taps = 0
        self._lock = threading.Lock()
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
                return d
        return None

    def _run(self):
        import time
        e = self.evdev.ecodes
        while True:
            try:
                d = self._find()
                if not d:
                    time.sleep(10)
                    continue
                for ev in d.read_loop():
                    down = (ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH and ev.value == 1) or \
                           (ev.type == e.EV_ABS and ev.code == e.ABS_MT_TRACKING_ID and ev.value >= 0)
                    if down:
                        with self._lock:
                            self._taps += 1
            except OSError as err:
                log.warning("touch: %s", err)
                time.sleep(5)

    def tapped(self):
        with self._lock:
            n, self._taps = self._taps, 0
        return n > 0
