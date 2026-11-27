import collections
import os

import pygame

W, H = 800, 480
BG = (11, 11, 16)
PANEL = (26, 26, 34)
LINE = (44, 44, 56)
TEXT = (240, 240, 245)
DIM = (135, 135, 150)
ACCENT = (225, 6, 0)
YELLOW = (255, 205, 0)
UP = (40, 200, 110)
DOWN = (235, 70, 70)
TYRES = {"SOFT": ((220, 30, 40), "S"), "MEDIUM": ((250, 205, 20), "M"), "HARD": ((235, 235, 235), "H"),
         "INTERMEDIATE": ((40, 180, 70), "I"), "WET": ((30, 110, 220), "W")}
TEAM_COLOURS = {"red_bull": (54, 113, 198), "ferrari": (232, 0, 45), "mercedes": (39, 244, 210),
                "mclaren": (255, 128, 0), "aston_martin": (34, 153, 113), "alpine": (0, 147, 204),
                "williams": (100, 196, 255), "rb": (102, 146, 255), "sauber": (82, 226, 82),
                "audi": (190, 190, 200), "haas": (182, 186, 189), "cadillac": (200, 170, 90)}
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_fonts = {}
_cache = collections.OrderedDict()


def present(surf):
    pygame.display.flip()


def team_colour(team_id):
    return TEAM_COLOURS.get(team_id, (128, 128, 128))


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
        _fonts[key] = pygame.font.Font(path if os.path.exists(path) else None, size)
    return _fonts[key]


def render(s, size, colour=TEXT, bold=False):
    key = (s, size, colour, bold)
    img = _cache.get(key)
    if img is None:
        img = font(size, bold).render(s, True, colour)
        _cache[key] = img
        if len(_cache) > 800:
            _cache.popitem(last=False)
    return img


def text(surf, s, pos, size, colour=TEXT, bold=False, anchor="topleft", maxw=None):
    s = str(s)
    img = render(s, size, colour, bold)
    if maxw and img.get_width() > maxw:
        while len(s) > 1 and img.get_width() > maxw:
            s = s[:-1]
            img = render(s.rstrip() + "…", size, colour, bold)
    rect = img.get_rect(**{anchor: pos})
    surf.blit(img, rect)
    return rect


def wrap(s, size, width, bold=False):
    words, lines, line = str(s).split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if font(size, bold).size(trial)[0] <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def header(surf, title, right=""):
    surf.fill(BG)
    pygame.draw.rect(surf, PANEL, (0, 0, W, 44))
    pygame.draw.rect(surf, ACCENT, (0, 0, 6, 44))
    text(surf, title, (20, 22), 20, bold=True, anchor="midleft", maxw=560)
    if right:
        text(surf, right, (W - 16, 22), 18, DIM, anchor="midright")


def arrow(surf, move, pos, size=16):
    x, y = pos
    if not move:
        pygame.draw.rect(surf, DIM, (x - 6, y - 1, 12, 3))
        return
    c = UP if move > 0 else DOWN
    pts = [(x - 7, y + 4), (x + 7, y + 4), (x, y - 5)] if move > 0 else [(x - 7, y - 4), (x + 7, y - 4), (x, y + 5)]
    pygame.draw.polygon(surf, c, pts)
    text(surf, str(abs(move)), (x + 11, y), size, c, bold=True, anchor="midleft")


def tyre(surf, compound, centre, r):
    col, letter = TYRES.get(compound, ((90, 90, 100), "?"))
    if not compound:
        return
    pygame.draw.circle(surf, col, centre, r, max(2, r // 4))
    text(surf, letter, centre, int(r * 1.1), col, bold=True, anchor="center")


def message(surf, title, sub=""):
    surf.fill(BG)
    text(surf, title, (W // 2, H // 2 - 20), 34, bold=True, anchor="center")
    if sub:
        text(surf, sub, (W // 2, H // 2 + 26), 20, DIM, anchor="center", maxw=W - 40)
