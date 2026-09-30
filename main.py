#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
БЛОБ-СКЕЙТ — 2D-игра про рыбу-каплю на скейте (Python + pygame 2).

Запуск на компьютере:
    pip install pygame
    python main.py           (или blob_skate.py — это один и тот же файл)

Сборка под Android: см. README.md рядом с этим файлом (buildozer + GitHub Actions).

Цель: проехать как можно дальше, собирая таблетки и перепрыгивая препятствия.

Управление (клавиатура, ПК):
    ПРОБЕЛ / стрелка вверх / W — прыжок (держите дольше — прыгнете выше)
    стрелка вправо / D          — разгон
    стрелка влево / A           — торможение (в воздухе — наклон)
    P / Esc — пауза     R — рестарт     M — звук     F11 — полный экран

Управление (сенсорный экран, Android):
    Держать экран — прыжок (дольше держать — выше прыжок)
    Педаль в правом нижнем углу — газ, в левом нижнем — тормоз
    Кнопки паузы/звука — в правом верхнем углу; аппаратная «Назад» — пауза

Картинки и звуки рисуются и синтезируются прямо в коде — внешние файлы не нужны
(кроме Android-сборки, где для скорости используются уже готовые fonts/ и audio/ — см. README.md).
"""
import bisect
import json
import math
import os
import random
import sys
from array import array

import pygame
from pygame.locals import *   # даёт неквалифицированные K_*, QUIT, KEYDOWN и т.п.

# =============================================================================
#  ПЛАТФОРМА (Android / сенсорный экран) И ПУТИ
# =============================================================================
# На Android python-for-android кладёт в окружение ANDROID_ARGUMENT / ANDROID_PRIVATE.
IS_ANDROID = ("ANDROID_ARGUMENT" in os.environ) or hasattr(sys, "getandroidapilevel")
# Показывать экранные кнопки: на Android всегда, на ПК — по флагу --touch (для проверки мышкой)
TOUCH_DEFAULT = IS_ANDROID or ("--touch" in sys.argv) or os.environ.get("BLOB_TOUCH") == "1"
# «Лёгкий» режим графики (меньше слоёв и деталей) — включается сам на слабых телефонах
LITE = ("--lite" in sys.argv) or os.environ.get("BLOB_LITE") == "1"
try:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BASE_DIR = os.getcwd()
AUDIO_DIR = os.path.join(_BASE_DIR, "audio")      # готовые WAV (если есть) — быстрее старт
FONT_FILE = os.path.join(_BASE_DIR, "fonts", "DejaVuSans-Bold.ttf")   # шрифт с кириллицей
_APP_BG = getattr(pygame, "APP_WILLENTERBACKGROUND", -991)   # событий жизненного цикла может не быть
_APP_FG = getattr(pygame, "APP_DIDENTERFOREGROUND", -992)    # на этой сборке pygame — тогда просто никогда не сработают

# =============================================================================
#  НАСТРОЙКИ
# =============================================================================
W, H = 1280, 720
FPS = 60
PHYS_DT = 1.0 / 120.0            # физика считается с фиксированным шагом
TITLE = "Блоб-Скейт"

PX_PER_M = 60.0                  # сколько пикселей в «метре» счётчика дистанции
START_X = 0.0                    # где старт (дистанция считается от этой точки)

# --- физика ------------------------------------------------------------------
GRAVITY = 2000.0                 # свободное падение, px/с²
SLOPE_G = 1150.0                 # разгон/торможение на склонах (мягче настоящей гравитации)
RELAX = 1.8                      # как быстро скорость тянется к «крейсерской»
SPEED_MIN = 440.0                # крейсерская скорость на старте, px/с
SPEED_MAX = 770.0                # ...и после SPEED_RAMP_M метров
SPEED_RAMP_M = 2600.0
MIN_SPEED = 170.0                # рыба всегда отталкивается ногой — не встаёт
BOOST_K = 1.32                   # множитель крейсерской скорости при разгоне
BRAKE_K = 0.50                   # ...и при торможении
JUMP_SPEED = 900.0
SPEED_FLOOR = 0.80                # на подъёме рыба не замедляется ниже 80% от крейсерской
JUMP_BUFFER = 0.13               # можно нажать прыжок чуть заранее
COYOTE = 0.09                    # ...или чуть позже схода с земли
TAKEOFF_EPS = 0.03
CRASH_ANGLE = 1.0                # рад: приземление под таким углом = падение
AIR_K, AIR_D, TILT_ACC = 20.0, 4.5, 15.0

SAVE_NAME = "blob_skate_save.json"

# =============================================================================
#  ПАЛИТРА
# =============================================================================
WHITE = (255, 255, 255)
INK = (34, 24, 32)               # тёмный «мультяшный» контур
GOLD = (255, 214, 64)
ORANGE = (255, 138, 36)
RED = (226, 64, 60)
GREEN = (98, 190, 52)


# =============================================================================
#  МЕЛКИЕ ПОМОЩНИКИ
# =============================================================================
def clamp(v, a, b):
    return a if v < a else b if v > b else v


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_col(a, b, t):
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def smoothstep(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def wrap_pi(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def hash01(i, salt=0):
    """Детерминированный «шум» 0..1 по целому числу (для травы, камешков и т.п.)."""
    n = (int(i) * 374761393 + int(salt) * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return (n & 0xFFFF) / 65535.0


def oriented_ellipse(cx, cy, rx, ry, ang, n=20):
    """Точки эллипса, повёрнутого на угол ang (для «ластов» и бликов)."""
    ca, sa = math.cos(ang), math.sin(ang)
    pts = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def blit_rot(dst, img, pos, pivot, ang, scale=1.0):
    """Рисует img, повёрнутую на ang (рад, по часовой стрелке на экране) вокруг точки
    pivot (в координатах картинки) так, что pivot оказывается в точке pos."""
    w, h = img.get_size()
    if abs(ang) > 1e-3 or abs(scale - 1.0) > 1e-3:
        rot = pygame.transform.rotozoom(img, -math.degrees(ang), scale)
    else:
        rot = img
    vx, vy = (pivot[0] - w / 2.0) * scale, (pivot[1] - h / 2.0) * scale
    ca, sa = math.cos(ang), math.sin(ang)
    rx, ry = vx * ca - vy * sa, vx * sa + vy * ca
    cx, cy = pos[0] - rx, pos[1] - ry
    dst.blit(rot, (int(cx - rot.get_width() / 2.0), int(cy - rot.get_height() / 2.0)))


def draw_rrect(surf, color, rect, radius):
    """Скруглённый прямоугольник (с запасным вариантом для старых pygame)."""
    try:
        pygame.draw.rect(surf, color, rect, border_radius=int(radius))
    except TypeError:
        pygame.draw.rect(surf, color, rect)


# =============================================================================
#  ШРИФТЫ И ТЕКСТ (с обводкой в стиле Hill Climb Racing)
# =============================================================================
HEAD_FONTS = [("arialblack", False), ("impact", False), ("segoeuiblack", False), ("arial", True),
              ("dejavusans", True), ("liberationsans", True), ("freesans", True),
              ("verdana", True), ("tahoma", True)]
BODY_FONTS = [("arial", True), ("segoeui", True), ("dejavusans", True), ("liberationsans", True),
              ("freesans", True), ("verdana", True), ("tahoma", True)]
_FONT_PATHS = {}
_FONT_CACHE = {}
_TEXT_CACHE = {}


def _font_ok(path):
    """Проверяем, что в шрифте есть кириллица."""
    try:
        f = pygame.font.Font(path, 20)
    except Exception:
        return False
    try:
        return all(g is not None for g in f.metrics(u"ЖЫФйё"))
    except Exception:
        return True


def _find_font(cands):
    for name, bold in cands:
        try:
            p = pygame.font.match_font(name, bold=bold)
        except Exception:
            p = None
        if p and _font_ok(p):
            return p
    return None


def get_font(size, kind="head"):
    key = (int(size), kind)
    f = _FONT_CACHE.get(key)
    if f is None:
        if kind not in _FONT_PATHS:
            bundled = FONT_FILE if (os.path.isfile(FONT_FILE) and _font_ok(FONT_FILE)) else None
            _FONT_PATHS[kind] = bundled or _find_font(HEAD_FONTS if kind == "head" else BODY_FONTS)
        path = _FONT_PATHS[kind]
        f = pygame.font.Font(path, int(size) if path else int(size * 1.45))
        _FONT_CACHE[key] = f
    return f


def render_text(text, size, color=WHITE, outline=INK, th=3, kind="head"):
    """Текст с толстой обводкой; результат кэшируется."""
    key = (text, int(size), color, outline, th, kind)
    s = _TEXT_CACHE.get(key)
    if s is not None:
        return s
    f = get_font(size, kind)
    base = f.render(text, True, color)
    if th <= 0:
        s = base
    else:
        w, h = base.get_size()
        pad = th + 1
        s = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
        edge = f.render(text, True, outline)
        for dx in range(-th, th + 1):
            for dy in range(-th, th + 1):
                if dx * dx + dy * dy <= th * th + 1:
                    s.blit(edge, (pad + dx, pad + dy))
        s.blit(base, (pad, pad))
    if len(_TEXT_CACHE) > 700:
        _TEXT_CACHE.clear()
    _TEXT_CACHE[key] = s
    return s


def render_gradient_text(text, size, top, bottom, outline=INK, th=5, kind="head"):
    """Крупный заголовок: градиентная заливка + обводка + светлая «фаска» сверху."""
    key = ("grad", text, int(size), top, bottom, outline, th, kind)
    s = _TEXT_CACHE.get(key)
    if s is not None:
        return s
    f = get_font(size, kind)
    base = f.render(text, True, WHITE)
    w, h = base.get_size()
    grad = pygame.Surface((w, h))
    for y in range(h):
        pygame.draw.line(grad, lerp_col(top, bottom, y / float(max(1, h - 1))), (0, y), (w, y))
    base.blit(grad, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    pad = th + 2
    s = pygame.Surface((w + 2 * pad, h + 2 * pad), pygame.SRCALPHA)
    edge = f.render(text, True, outline)
    for dx in range(-th, th + 1):
        for dy in range(-th, th + 1):
            if dx * dx + dy * dy <= th * th + 1:
                s.blit(edge, (pad + dx, pad + dy + 2))      # чуть ниже — объём
    for dx in range(-th + 1, th):
        for dy in range(-th + 1, th):
            if dx * dx + dy * dy <= (th - 1) * (th - 1) + 1:
                s.blit(edge, (pad + dx, pad + dy))
    s.blit(base, (pad, pad))
    _TEXT_CACHE[key] = s
    return s


def blit_text(dst, text, size, pos, anchor="center", color=WHITE, outline=INK, th=3, kind="head", alpha=255):
    s = render_text(text, size, color, outline, th, kind)
    r = s.get_rect()
    setattr(r, anchor, (int(pos[0]), int(pos[1])))
    if alpha < 255:
        s = s.copy()
        s.set_alpha(alpha)
    dst.blit(s, r)
    return r


# =============================================================================
#  КАНВАС С СУПЕРСЭМПЛИНГОМ — рисуем в SS раз крупнее и сглаженно уменьшаем
# =============================================================================
class Canvas:
    """Все спрайты игры рисуются на таком холсте: получается гладкая графика с
    градиентами и контурами (обычный pygame.draw даёт «лесенку»).
    Координаты «дизайнерские»: экранная точка = origin + k * (x, y)."""

    def __init__(self, w, h, ss=None, origin=(0.0, 0.0), k=1.0):
        ss = (2 if LITE else 3) if ss is None else ss
        self.w, self.h, self.ss, self.k = w, h, ss, k
        self.ox, self.oy = origin
        self.s = pygame.Surface((int(w * ss), int(h * ss)), pygame.SRCALPHA)

    def blank(self):
        return Canvas(self.w, self.h, self.ss, (self.ox, self.oy), self.k)

    # --- преобразования ---
    def P(self, p):
        return (int(round((self.ox + p[0] * self.k) * self.ss)),
                int(round((self.oy + p[1] * self.k) * self.ss)))

    def R(self, r):
        return max(1, int(round(r * self.k * self.ss)))

    # --- примитивы ---
    def circle(self, col, c, r, outline=None, ow=0):
        p = self.P(c)
        if outline is not None and ow:
            pygame.draw.circle(self.s, outline, p, self.R(r + ow))
        pygame.draw.circle(self.s, col, p, self.R(r))

    def _erect(self, cx, cy, rx, ry):
        x0, y0 = self.P((cx - rx, cy - ry))
        x1, y1 = self.P((cx + rx, cy + ry))
        return pygame.Rect(x0, y0, max(2, x1 - x0), max(2, y1 - y0))

    def ellipse(self, col, cx, cy, rx, ry, outline=None, ow=0):
        if outline is not None and ow:
            pygame.draw.ellipse(self.s, outline, self._erect(cx, cy, rx + ow, ry + ow))
        pygame.draw.ellipse(self.s, col, self._erect(cx, cy, rx, ry))

    def poly(self, col, pts, outline=None, ow=0):
        P = [self.P(p) for p in pts]
        if outline is not None and ow:
            wd = self.R(ow) * 2
            pygame.draw.polygon(self.s, outline, P)
            pygame.draw.lines(self.s, outline, True, P, wd)
            for q in P:
                pygame.draw.circle(self.s, outline, q, wd // 2)
        pygame.draw.polygon(self.s, col, P)

    def _capsule(self, col, a, b, width):
        A, B = self.P(a), self.P(b)
        r = self.R(width / 2.0)
        dx, dy = B[0] - A[0], B[1] - A[1]
        L = math.hypot(dx, dy)
        if L > 0.5:
            nx, ny = -dy / L * r, dx / L * r
            pygame.draw.polygon(self.s, col, [(A[0] + nx, A[1] + ny), (B[0] + nx, B[1] + ny),
                                              (B[0] - nx, B[1] - ny), (A[0] - nx, A[1] - ny)])
        pygame.draw.circle(self.s, col, A, r)
        pygame.draw.circle(self.s, col, B, r)

    def capsule(self, col, a, b, width, outline=None, ow=0):
        if outline is not None and ow:
            self._capsule(outline, a, b, width + 2 * ow)
        self._capsule(col, a, b, width)

    def taper(self, col, a, ra, b, rb, outline=None, ow=0):
        """Конус с закруглёнными концами: радиус ra у точки a и rb у точки b."""
        def draw(c, ga):
            A, B = self.P(a), self.P(b)
            r1, r2 = self.R(ra + ga), self.R(rb + ga)
            dx, dy = B[0] - A[0], B[1] - A[1]
            L = math.hypot(dx, dy)
            if L > 0.5:
                nx, ny = -dy / L, dx / L
                pygame.draw.polygon(self.s, c, [(A[0] + nx * r1, A[1] + ny * r1), (B[0] + nx * r2, B[1] + ny * r2),
                                                (B[0] - nx * r2, B[1] - ny * r2), (A[0] - nx * r1, A[1] - ny * r1)])
            pygame.draw.circle(self.s, c, A, r1)
            pygame.draw.circle(self.s, c, B, r2)
        if outline is not None and ow:
            draw(outline, ow)
        draw(col, 0)

    def stroke(self, col, pts, width, outline=None, ow=0):
        """Толстая ломаная со скруглёнными стыками."""
        if outline is not None and ow:
            for a, b in zip(pts[:-1], pts[1:]):
                self._capsule(outline, a, b, width + 2 * ow)
        for a, b in zip(pts[:-1], pts[1:]):
            self._capsule(col, a, b, width)

    def rrect(self, col, x, y, w, h, r, outline=None, ow=0):
        def draw(c, x, y, w, h, r):
            x0, y0 = self.P((x, y))
            x1, y1 = self.P((x + w, y + h))
            rr = max(0, min(self.R(r), (x1 - x0) // 2, (y1 - y0) // 2))
            pygame.draw.rect(self.s, c, (x0 + rr, y0, max(1, x1 - x0 - 2 * rr), max(1, y1 - y0)))
            pygame.draw.rect(self.s, c, (x0, y0 + rr, max(1, x1 - x0), max(1, y1 - y0 - 2 * rr)))
            if rr > 0:
                for cx, cy in ((x0 + rr, y0 + rr), (x1 - rr - 1, y0 + rr),
                               (x0 + rr, y1 - rr - 1), (x1 - rr - 1, y1 - rr - 1)):
                    pygame.draw.circle(self.s, c, (cx, cy), rr)
        if outline is not None and ow:
            draw(outline, x - ow, y - ow, w + 2 * ow, h + 2 * ow, r + ow)
        draw(col, x, y, w, h, r)

    def shaded_ellipse(self, cx, cy, rx, ry, dark, base, light, lx=-0.35, ly=-0.5,
                       steps=16, outline=None, ow=0):
        """Объёмный «желейный» эллипс: тёмный край -> основной цвет -> светлое ядро,
        смещённое в сторону источника света."""
        if outline is not None and ow:
            self.ellipse(outline, cx, cy, rx + ow, ry + ow)
        for i in range(steps):
            t = i / float(steps - 1)
            sc = 1.0 - 0.86 * t
            ox, oy = lx * rx * 0.55 * t, ly * ry * 0.55 * t
            if t < 0.45:
                col = lerp_col(dark, base, t / 0.45)
            else:
                col = lerp_col(base, light, (t - 0.45) / 0.55)
            self.ellipse(col, cx + ox, cy + oy, rx * sc, ry * sc)

    def grad_rect(self, x, y, w, h, top, bottom):
        x0, y0 = self.P((x, y))
        x1, y1 = self.P((x + w, y + h))
        n = max(1, y1 - y0)
        for i in range(n):
            pygame.draw.line(self.s, lerp_col(top, bottom, i / float(max(1, n - 1))),
                             (x0, y0 + i), (x1 - 1, y0 + i))

    def filled_by(self, mask_fn, paint_fn):
        """Заливка «по маске»: mask_fn(canvas) рисует белую форму, paint_fn(surface)
        закрашивает произвольную картинку (в пикселях суперсэмпла); остаётся пересечение."""
        mk = self.blank()
        mask_fn(mk)
        g = pygame.Surface(self.s.get_size())
        paint_fn(g)
        layer = g.convert_alpha()
        layer.blit(mk.s, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        self.s.blit(layer, (0, 0))

    def grad_poly(self, pts, top, bottom, top_y, bottom_y):
        """Многоугольник с вертикальным градиентом (для холмов на заднем плане)."""
        y0 = self.P((0, top_y))[1]
        y1 = self.P((0, bottom_y))[1]

        def mask(c):
            pygame.draw.polygon(c.s, (255, 255, 255, 255), [c.P(p) for p in pts])

        def paint(g):
            for y in range(g.get_height()):
                t = (y - y0) / float(max(1, y1 - y0))
                pygame.draw.line(g, lerp_col(top, bottom, t), (0, y), (g.get_width(), y))
        self.filled_by(mask, paint)

    def glow(self, cx, cy, r, col, alpha=120, steps=26):
        """Мягкое радиальное свечение (альфа растёт к центру)."""
        c = self.P((cx, cy))
        for i in range(steps):
            t = i / float(steps - 1)
            pygame.draw.circle(self.s, (col[0], col[1], col[2], int(alpha * t * t)), c,
                               self.R(r * (1.0 - t * 0.96)))

    def put(self, surf, pos):
        """Наложить готовую картинку (в пикселях суперсэмпла)."""
        self.s.blit(surf, pos)

    def outline(self, col, th):
        """Внешний контур по силуэту всего рисунка (th — в пикселях итогового спрайта)."""
        r = max(1, int(round(th * self.ss)))
        sil = self.s.copy()
        sil.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)
        sil.fill((col[0], col[1], col[2], 0), special_flags=pygame.BLEND_RGBA_ADD)
        out = pygame.Surface(self.s.get_size(), pygame.SRCALPHA)
        n = 20
        for ring in (1.0, 0.55):
            for i in range(n):
                a = 2.0 * math.pi * i / n
                out.blit(sil, (int(round(math.cos(a) * r * ring)), int(round(math.sin(a) * r * ring))))
        out.blit(self.s, (0, 0))
        self.s = out

    def finish(self):
        return pygame.transform.smoothscale(self.s, (self.w, self.h))



# =============================================================================
#  ЗВУК — вся музыка и все эффекты синтезируются в коде (без файлов)
# =============================================================================
SR = 22050                       # частота дискретизации синтеза
BPM = 132
MUSIC_VOL = 0.40
SFX_VOL = 0.85

_note_cache = {}


def midi_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def tone(freq, dur, wave="sq", vol=0.3, duty=0.5, dec=6.0, att=0.004, rel=0.02, vib=0.0, f_end=None):
    """Одна нота: wave = sq (пульс) / tri / saw / sin. dec — скорость затухания,
    f_end — конечная частота (глиссандо), vib — глубина вибрато."""
    key = (round(freq, 3), round(dur, 4), wave, vol, duty, dec, att, rel, vib, f_end)
    got = _note_cache.get(key)
    if got is not None:
        return got
    n = max(2, int(dur * SR))
    out = [0.0] * n
    attn = max(1, int(att * SR))
    reln = max(1, int(rel * SR))
    decm = math.exp(-dec / SR)
    fm = 1.0 if f_end is None else (f_end / float(freq)) ** (1.0 / n)
    f, ph, e = float(freq), 0.0, 1.0
    vw = 2.0 * math.pi * 6.0 / SR
    sin = math.sin
    for i in range(n):
        ph += (f * (1.0 + vib * sin(i * vw)) if vib else f) / SR
        p = ph - int(ph)
        if wave == "sq":
            s = 1.0 if p < duty else -1.0
        elif wave == "tri":
            s = 4.0 * abs(p - 0.5) - 1.0
        elif wave == "saw":
            s = 2.0 * p - 1.0
        else:
            s = sin(6.283185307179586 * p)
        a = (i + 1) / attn
        r = (n - i) / reln
        out[i] = s * vol * e * (a if a < 1.0 else 1.0) * (r if r < 1.0 else 1.0)
        e *= decm
        f *= fm
    _note_cache[key] = out
    return out


def noise(dur, vol=0.3, dec=20.0, lp=1.0, hp=False, seed=7):
    """Шум с затуханием; lp<1 — глуше (низкочастотный фильтр), hp — «шипение»."""
    key = ("noise", dur, vol, dec, lp, hp, seed)
    got = _note_cache.get(key)
    if got is not None:
        return got
    rnd = random.Random(seed)
    n = max(2, int(dur * SR))
    out = [0.0] * n
    decm = math.exp(-dec / SR)
    e, y, prev = 1.0, 0.0, 0.0
    reln = max(1, int(0.004 * SR))
    for i in range(n):
        y += lp * ((rnd.random() * 2.0 - 1.0) - y)
        s = (y - prev) if hp else y
        prev = y
        r = (n - i) / reln
        out[i] = s * e * vol * (r if r < 1.0 else 1.0)
        e *= decm
    _note_cache[key] = out
    return out


def mix_into(dst, src, start=0, gain=1.0):
    """Подмешивает src в dst с позиции start; хвост, вылезший за конец, переносится в начало (петля)."""
    n, m = len(dst), len(src)
    start %= n
    end = start + m
    if end <= n:
        dst[start:end] = [a + b * gain for a, b in zip(dst[start:end], src)]
    else:
        k = n - start
        dst[start:n] = [a + b * gain for a, b in zip(dst[start:n], src[:k])]
        rest = src[k:]
        rest = rest[:n]
        dst[0:len(rest)] = [a + b * gain for a, b in zip(dst[0:len(rest)], rest)]


def layer(parts):
    """parts: [(samples, смещение_в_секундах), ...] -> один буфер."""
    total = max(int(off * SR) + len(s) for s, off in parts)
    buf = [0.0] * total
    for s, off in parts:
        mix_into(buf, s, int(off * SR))
    return buf


def resample(buf, f0, f1):
    if f0 == f1:
        return buf
    n = int(len(buf) * f1 / float(f0))
    ratio = f0 / float(f1)
    last = len(buf) - 1
    out = [0.0] * n
    for i in range(n):
        x = i * ratio
        j = int(x)
        fr = x - j
        a = buf[j]
        b = buf[j + 1] if j < last else buf[last]
        out[i] = a + (b - a) * fr
    return out


def to_pcm(buf, channels=1, fmt=-16, gain=0.95):
    """Мягкое ограничение (tanh) -> байты для pygame.mixer.Sound."""
    tanh = math.tanh
    if fmt == 32:
        vals = [tanh(v * gain) for v in buf]
        a = array("f")
    else:
        vals = [int(tanh(v * gain) * 30000) for v in buf]
        a = array("h")
    if channels == 1:
        a.extend(vals)
    else:
        st = [0] * (len(vals) * channels)
        for c in range(channels):
            st[c::channels] = vals
        a.extend(st)
    return a.tobytes()


# --- ударные -----------------------------------------------------------------
def _cached(key, fn):
    got = _note_cache.get(key)
    if got is None:
        got = fn()
        _note_cache[key] = got
    return got


def drum_kick():
    def make():
        n = int(0.17 * SR)
        out = [0.0] * n
        ph = 0.0
        for i in range(n):
            t = i / float(SR)
            ph += (46.0 + 130.0 * math.exp(-t * 34.0)) / SR
            out[i] = math.sin(6.283185307179586 * ph) * math.exp(-t * 19.0) * 0.78
        return out
    return _cached("kick", make)


def drum_snare():
    return _cached("snare", lambda: layer([(noise(0.14, 0.34, dec=26.0, lp=0.9, hp=True, seed=11), 0.0),
                                           (tone(190.0, 0.10, "tri", 0.26, dec=30.0, rel=0.01), 0.0)]))


def drum_hat(open_=False):
    return noise(0.14 if open_ else 0.045, 0.13 if open_ else 0.10, dec=32.0 if open_ else 90.0,
                 lp=1.0, hp=True, seed=5)


# --- музыка ------------------------------------------------------------------
CHORDS = [   # (нота баса, трезвучие) — Am F C G / Am F G G
    (45, (57, 60, 64)), (41, (53, 57, 60)), (48, (55, 60, 64)), (43, (55, 59, 62)),
    (45, (57, 60, 64)), (41, (53, 57, 60)), (43, (55, 59, 62)), (43, (55, 59, 62)),
]
LEAD = [     # мелодия: 8 восьмых на такт; '-' продлевает ноту, '.' — пауза
    "E5 - E5 D5 C5 - D5 E5",
    "F5 - F5 E5 C5 - A4 C5",
    "E5 - G5 E5 D5 - C5 D5",
    "D5 - D5 B4 G4 - A4 B4",
    "A5 - G5 E5 D5 - E5 G5",
    "A5 - F5 E5 C5 - E5 F5",
    "G5 - D5 B4 D5 - G5 D5",
    "B4 - D5 - G4 - . .",
]


def _parse_melody(bars):
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    notes = []
    for b, line in enumerate(bars):
        toks = line.split()
        i = 0
        while i < len(toks):
            t = toks[i]
            if t in (".", "-"):
                i += 1
                continue
            m = 12 * (int(t[-1]) + 1) + base[t[0]] + (1 if "#" in t else 0)
            ln = 1
            while i + ln < len(toks) and toks[i + ln] == "-":
                ln += 1
            notes.append((b * 16 + i * 2, ln * 2, m))
            i += ln
    return notes


def build_music(progress=None):
    """Возвращает {'game': буфер, 'menu': буфер} — две аранжировки одной петли (8 тактов)."""
    def rep(f):
        if progress:
            progress(f)
    s16 = 60.0 / BPM / 4.0
    total = int(round(16 * len(CHORDS) * s16 * SR))

    def pos(k):
        return int(round(k * s16 * SR))
    stems = dict((k, [0.0] * total) for k in ("drums", "bass", "arp", "lead"))
    for b, (root, tri) in enumerate(CHORDS):
        for j, off in enumerate((0, 0, 12, 0, 0, 0, 12, 7)):
            mix_into(stems["bass"], tone(midi_hz(root + off), s16 * 1.85, "tri", 0.36, dec=4.0),
                     pos(b * 16 + j * 2))
        seq = [tri[0], tri[1], tri[2], tri[1]] * 4
        for j, m in enumerate(seq):
            mix_into(stems["arp"], tone(midi_hz(m + 12), s16 * 1.6, "sq", 0.07, duty=0.25, dec=12.0, rel=0.01),
                     pos(b * 16 + j))
    rep(0.35)
    for st, ln, m in _parse_melody(LEAD):
        mix_into(stems["lead"], tone(midi_hz(m), s16 * ln * 0.92, "sq", 0.13, duty=0.5, dec=2.2,
                                     vib=0.004, rel=0.03), pos(st))
    rep(0.6)
    kick, snare, hat, ohat = drum_kick(), drum_snare(), drum_hat(False), drum_hat(True)
    for b in range(len(CHORDS)):
        base = b * 16
        for k in ((0, 8, 10) if b % 2 == 0 else (0, 8, 11, 14)):
            mix_into(stems["drums"], kick, pos(base + k))
        for k in ((4, 12) if b != 7 else (4, 12, 13, 14, 15)):
            mix_into(stems["drums"], snare, pos(base + k))
        for k in range(0, 16, 2):
            mix_into(stems["drums"], ohat if k % 8 == 6 else hat, pos(base + k), 1.0 if k % 4 == 2 else 0.6)
    rep(0.8)
    d, b_, a, l = stems["drums"], stems["bass"], stems["arp"], stems["lead"]
    game = [x * 0.85 + y + z + w for x, y, z, w in zip(d, b_, a, l)]
    menu = [y * 0.75 + z * 1.1 + w * 0.85 for y, z, w in zip(b_, a, l)]
    rep(1.0)
    return {"game": game, "menu": menu}


# --- звуковые эффекты --------------------------------------------------------
def sfx_jump():
    return layer([(tone(260, 0.17, "sq", 0.26, duty=0.25, dec=9.0, f_end=820), 0.0),
                  (tone(520, 0.17, "tri", 0.20, dec=9.0, f_end=1640), 0.0),
                  (noise(0.09, 0.10, dec=30.0, lp=0.4, seed=2), 0.0)])


def sfx_land():
    return layer([(tone(150, 0.15, "sin", 0.55, dec=20.0, f_end=52), 0.0),
                  (noise(0.06, 0.32, dec=70.0, lp=0.6, seed=4), 0.0)])


def sfx_pill(i):
    scale = (72, 74, 76, 79, 81, 84, 86, 88)      # пентатоника — комбо «поёт» вверх
    f = midi_hz(scale[min(i, len(scale) - 1)])
    return layer([(tone(f, 0.06, "sq", 0.20, duty=0.25, dec=8.0), 0.0),
                  (tone(f * 1.5, 0.16, "sq", 0.20, duty=0.25, dec=14.0), 0.055),
                  (tone(f * 2.0, 0.16, "tri", 0.10, dec=16.0), 0.055)])


def sfx_death():
    parts = [(noise(0.45, 0.55, dec=9.0, lp=0.30, seed=3), 0.0),
             (tone(190, 0.35, "sin", 0.6, dec=8.0, f_end=38), 0.0),
             (noise(0.06, 0.40, dec=60.0, lp=1.0, seed=5), 0.0)]
    t = 0.32
    seq = [(392.0, 0.20), (369.99, 0.20), (349.23, 0.20), (329.63, 0.60)]
    for i, (f, d) in enumerate(seq):
        last = i == len(seq) - 1
        parts.append((tone(f, d, "saw", 0.20, dec=2.5, vib=0.02 if last else 0.0,
                           f_end=f * 0.78 if last else None, rel=0.05), t))
        parts.append((tone(f * 0.5, d, "sq", 0.09, duty=0.5, dec=2.5, rel=0.05), t))
        t += d
    return layer(parts)


def sfx_click():
    return tone(880, 0.06, "tri", 0.26, dec=40.0)


def sfx_hover():
    return tone(660, 0.035, "tri", 0.12, dec=60.0)


def sfx_start():
    return layer([(tone(midi_hz(m), 0.10, "sq", 0.22, duty=0.25, dec=6.0), 0.07 * i)
                  for i, m in enumerate((72, 76, 79, 84))])


def sfx_record():
    notes = (72, 76, 79, 84, 79, 84, 88)
    parts = [(tone(midi_hz(m), 0.11 if i < 6 else 0.5, "sq", 0.22, duty=0.25, dec=4.0), 0.09 * i)
             for i, m in enumerate(notes)]
    return layer(parts)


def sfx_roll_loop():
    """Тихий рокот колёс: фильтрованный шум, склеенный в бесшовную петлю."""
    n = SR
    m = 1800
    x = noise((n + m) / float(SR) + 0.01, 0.9, dec=0.0, lp=0.10, seed=21)
    out = [0.0] * n
    for i in range(n):
        w = i / float(m) if i < m else 1.0
        out[i] = x[i] * w + x[i + n] * (1.0 - w) if i < m else x[i]
    return out


class Audio:
    """Обёртка над pygame.mixer: если звуковой карты нет — игра просто молчит."""

    def __init__(self, progress=None):
        self.ok = False
        self.enabled = True
        self.sfx_snd, self.music_snd = {}, {}
        self.music_ch = self.roll_ch = self.roll_snd = None
        self.cur = None
        self.freq, self.channels, self.fmt = SR, 1, -16
        try:
            self._init_mixer()
        except Exception:
            self.ok = False
        if self.ok:
            try:
                self._build(progress)
            except Exception:
                self.ok = False
        if progress:
            progress(1.0)

    def _init_mixer(self):
        # Устройства (особенно Android) не всегда поддерживают точный формат;
        # пробуем от строгого к самому терпимому, вместо того чтобы просто не запускать звук.
        if not pygame.mixer.get_init():
            attempts = [
                lambda: pygame.mixer.init(SR, -16, 1, 512, allowedchanges=0),
                lambda: pygame.mixer.init(SR, -16, 1, 512),
                lambda: pygame.mixer.init(44100, -16, 2, 1024),
                lambda: pygame.mixer.init(),
            ]
            for attempt in attempts:
                try:
                    attempt()
                    if pygame.mixer.get_init():
                        break
                except (TypeError, Exception):
                    try:
                        pygame.mixer.quit()
                    except Exception:
                        pass
                    continue
        info = pygame.mixer.get_init()
        if not info:
            return
        self.freq, self.fmt, self.channels = info
        if self.fmt not in (-16, 32) or self.channels not in (1, 2):
            return
        pygame.mixer.set_num_channels(16)
        pygame.mixer.set_reserved(2)
        self.music_ch = pygame.mixer.Channel(0)
        self.roll_ch = pygame.mixer.Channel(1)
        self.ok = True

    def _snd(self, buf, gain=0.95):
        if self.freq != SR:
            buf = resample(buf, SR, self.freq)
        return pygame.mixer.Sound(buffer=to_pcm(buf, self.channels, self.fmt, gain))

    def _wav(self, name):
        """Готовый WAV из папки audio/ (на телефоне это заметно быстрее синтеза)."""
        p = os.path.join(AUDIO_DIR, name + ".wav")
        if os.path.isfile(p):
            try:
                return pygame.mixer.Sound(p)
            except Exception:
                return None
        return None

    def _build(self, progress):
        def rep(a, b):
            return (lambda f: progress(a + (b - a) * f)) if progress else None
        music = {}
        for k in ("game", "menu"):
            snd = self._wav("music_" + k)
            if snd is None:
                music = {}
                break
            music[k] = snd
        if music:
            self.music_snd = music
            if progress:
                progress(0.8)
        else:
            raw = build_music(rep(0.0, 0.8))
            for k, v in raw.items():
                self.music_snd[k] = self._snd(v, 0.9)
        makers = {"jump": sfx_jump, "land": sfx_land, "death": sfx_death, "click": sfx_click,
                  "hover": sfx_hover, "start": sfx_start, "record": sfx_record}
        for i in range(8):
            makers["pill%d" % i] = (lambda i=i: sfx_pill(i))
        for k, fn in makers.items():
            self.sfx_snd[k] = self._wav(k) or self._snd(fn())
        self.roll_snd = self._wav("roll") or self._snd(sfx_roll_loop(), 0.9)

    # --- воспроизведение ---
    def sfx(self, name, vol=1.0):
        if not (self.ok and self.enabled):
            return
        s = self.sfx_snd.get(name)
        if s is not None:
            s.set_volume(clamp(vol, 0.0, 1.0) * SFX_VOL)
            s.play()

    def play_music(self, which, fade_ms=350):
        if self.cur == which and self.ok and self.music_ch.get_busy():
            return
        self.cur = which
        if not (self.ok and self.enabled):
            return
        s = self.music_snd.get(which)
        if s is not None:
            self.music_ch.set_volume(MUSIC_VOL)
            self.music_ch.play(s, loops=-1, fade_ms=fade_ms)

    def stop_music(self, fade_ms=0):
        self.cur = None
        if self.ok:
            if fade_ms:
                self.music_ch.fadeout(fade_ms)
            else:
                self.music_ch.stop()

    def set_roll(self, vol):
        """Громкость рокота колёс (0 — выключить)."""
        if not (self.ok and self.enabled):
            return
        if vol <= 0.01:
            if self.roll_ch.get_busy():
                self.roll_ch.stop()
            return
        if not self.roll_ch.get_busy():
            self.roll_ch.play(self.roll_snd, loops=-1)
        self.roll_ch.set_volume(clamp(vol, 0.0, 1.0))

    def toggle(self):
        self.enabled = not self.enabled
        if not self.ok:
            return
        if self.enabled:
            if self.cur:
                self.play_music(self.cur)
        else:
            pygame.mixer.stop()



# =============================================================================
#  ГРАФИКА, ЧАСТЬ 1: рыба-капля, скейт, таблетки
#  (всё рисуется в коде на холстах с суперсэмплингом — см. класс Canvas)
# =============================================================================
DECK_TOP = 38            # высота палубы над точкой касания земли, px
WHEEL_R = 11
WHEEL_DX = 38            # колёса стоят на ±WHEEL_DX от центра доски

SKIN_D = (190, 116, 124)
SKIN = (234, 178, 170)
SKIN_L = (255, 228, 216)
SKIN_OUT = (96, 48, 62)
LIP = (200, 104, 116)
LIP_D = (150, 66, 86)
CLOTH_BASE = (28, 32, 48)
CLOTH_A = (70, 118, 196)
CLOTH_B = (216, 170, 78)
CLOTH_OUT = (12, 12, 20)
BEANIE = (40, 38, 44)
BEANIE_L = (72, 70, 80)
SHOE = (238, 230, 208)
SHOE_L = (252, 246, 232)
SHOE_D = (206, 186, 146)
DECK = (236, 92, 62)
DECK_D = (150, 44, 40)
DECK_L = (255, 176, 124)


def cloth_pattern(w, h, ss):
    """Узорчатая ткань как на фото: тёмно-синий фон, синие медальоны и золотые точки."""
    surf = pygame.Surface((w * ss, h * ss))
    surf.fill(CLOTH_BASE)
    pitch = 13 * ss
    r = 4.6 * ss
    for row in range(-1, int(h * ss / (pitch * 0.86)) + 3):
        for col in range(-1, int(w * ss / pitch) + 3):
            cx = col * pitch + (pitch / 2 if row % 2 else 0)
            cy = row * pitch * 0.86
            pygame.draw.circle(surf, CLOTH_A, (int(cx), int(cy)), int(r), max(1, int(1.3 * ss)))
            pygame.draw.circle(surf, CLOTH_B, (int(cx), int(cy)), max(1, int(1.6 * ss)))
            for a in range(4):
                ang = a * math.pi / 2 + math.pi / 4
                pygame.draw.circle(surf, CLOTH_A, (int(cx + math.cos(ang) * r * 1.75),
                                                   int(cy + math.sin(ang) * r * 1.75)), max(1, int(1.1 * ss)))
    return surf


def make_character(pose="ride"):
    """Рыба-капля: голая розовая «желейная» голова с обвисшим носом, чёрная шапка,
    узорчатый костюм, ласты вместо рук и кроссовки с иглами, как у рыбы-ежа.
    pose: ride (едет) / air (в прыжке) / dead (сбит, глаза-крестики).
    Возвращает (surface, pivot) — pivot стоит на палубе между ногами."""
    CW, CH = 200, 200
    cv = Canvas(CW, CH, 3, (96, 178), 0.72)
    ss = cv.ss
    pat = cloth_pattern(CW, CH, ss)
    shade = pygame.Surface(cv.s.get_size())
    for y in range(shade.get_height()):                    # свет сверху, тень снизу
        v = int(255 - 95 * clamp((y / float(shade.get_height()) - 0.30) / 0.55, 0.0, 1.0))
        pygame.draw.line(shade, (v, v, v), (0, y), (shade.get_width(), y))
    pat.blit(shade, (0, 0), special_flags=pygame.BLEND_RGB_MULT)

    def cloth(shape_fn, dark=0):
        """Слой ткани: обводка + узор, обрезанный по форме shape_fn."""
        mk = cv.blank()
        shape_fn(mk, WHITE, 0.0)
        shape_fn(cv, CLOTH_OUT, 3.2)
        lay = pat.convert_alpha()
        lay.blit(mk.s, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        if dark:
            v = 255 - dark
            lay.fill((v, v, min(255, v + 10), 255), special_flags=pygame.BLEND_RGBA_MULT)
        cv.put(lay, (0, 0))

    # --- скелет позы ---
    hip = (-4, -80)
    if pose == "air":
        r_knee, r_ank, f_knee, f_ank = (-24, -62), (-44, -32), (26, -66), (32, -32)
        near = ((10, -122), (46, -132), (82, -158))
        far = ((-6, -122), (-30, -138), (-50, -158))
    elif pose == "dead":
        r_knee, r_ank, f_knee, f_ank = (-20, -58), (-44, -32), (30, -62), (32, -32)
        near = ((10, -122), (46, -126), (84, -142))
        far = ((-6, -122), (-34, -116), (-62, -104))
    else:
        r_knee, r_ank, f_knee, f_ank = (-20, -58), (-44, -32), (30, -62), (32, -32)
        near = ((10, -122), (38, -108), (60, -94))
        far = ((-6, -122), (-32, -108), (-56, -96))

    def arm_skin(sh, el, hd, is_near):
        skin = SKIN if is_near else SKIN_D
        cv.capsule(skin, sh, el, 15, outline=SKIN_OUT, ow=1.8)
        cv.capsule(skin, el, hd, 12.5, outline=SKIN_OUT, ow=1.8)
        ang = math.atan2(hd[1] - el[1], hd[0] - el[0])
        ca, sa = math.cos(ang), math.sin(ang)
        cv.poly(skin, oriented_ellipse(hd[0] + ca * 4, hd[1] + sa * 4, 13, 8.5, ang), outline=SKIN_OUT, ow=1.8)
        for da in (-0.6, 0.0, 0.6):                          # «пальцы» — лучи плавника
            a2 = ang + da
            p0 = (hd[0] + ca * 9, hd[1] + sa * 9)
            p1 = (p0[0] + math.cos(a2) * 11, p0[1] + math.sin(a2) * 11)
            cv.capsule(skin, p0, p1, 5.6, outline=SKIN_OUT, ow=1.5)
        cv.poly(lerp_col(skin, WHITE, 0.45), oriented_ellipse(hd[0] + ca * 2, hd[1] + sa * 2 - 2.5, 6, 2, ang))

    def sleeve(sh, el):
        d = (el[0] - sh[0], el[1] - sh[1])
        ln = math.hypot(*d) or 1.0
        end = (sh[0] + d[0] / ln * 15, sh[1] + d[1] / ln * 15)
        cloth(lambda c, col, g: c.capsule(col, sh, end, 25 + g), dark=0 if sh is near[0] else 45)

    def leg_fn(knee, ank):
        def fn(c, col, g):
            c.capsule(col, hip, knee, 33 + g)
            c.capsule(col, knee, ank, 28 + g)
            c.circle(col, knee, 16.5 + g / 2.0)
            c.ellipse(col, ank[0] + 3, ank[1] + 3, 16 + g / 2.0, 10 + g / 2.0)
        return fn

    def shoe(cx):
        by = 0
        cv.rrect((246, 244, 238), cx - 25, by - 11, 52, 11, 4.5, outline=INK, ow=2)
        cv.rrect(INK, cx - 23, by - 7, 48, 2.2, 1)
        up = [(cx - 23, by - 11), (cx - 25, by - 31), (cx - 17, by - 39), (cx - 3, by - 37),
              (cx + 8, by - 28), (cx + 22, by - 23), (cx + 29, by - 15), (cx + 27, by - 11)]
        cv.poly(SHOE, up, outline=INK, ow=2)
        cv.ellipse(SHOE_L, cx + 18, by - 16, 9, 6)
        c0 = (cx - 2, by - 22)
        for px, py in ((cx - 25, by - 26), (cx - 25, by - 18), (cx - 20, by - 36), (cx - 9, by - 39),
                       (cx + 2, by - 36), (cx + 12, by - 29), (cx + 21, by - 24), (cx + 29, by - 17),
                       (cx + 26, by - 12)):                   # иглы рыбы-ежа
            dx, dy = px - c0[0], py - c0[1]
            ln = math.hypot(dx, dy) or 1.0
            dx, dy = dx / ln, dy / ln
            nx, ny = -dy, dx
            cv.poly(SHOE_D, [(px + nx * 2.8 - dx * 1.5, py + ny * 2.8 - dy * 1.5),
                             (px + dx * 7.5, py + dy * 7.5),
                             (px - nx * 2.8 - dx * 1.5, py - ny * 2.8 - dy * 1.5)], outline=INK, ow=1.0)
        for sx, sy in ((-16, -16), (-8, -13), (4, -16), (12, -14), (-14, -28), (-6, -31), (6, -25), (18, -19)):
            cv.circle((166, 138, 100), (cx + sx, by + sy), 1.2)
        for a, b in (((1, -31), (7, -27)), ((5, -28), (12, -24)), ((10, -25), (17, -21))):
            cv.stroke(INK, [(cx + a[0], by + a[1]), (cx + b[0], by + b[1])], 1.7)
        ex, ey = cx - 7, by - 22                             # глаз на кроссовке
        cv.circle(SHOE_D, (ex, ey), 6.4, outline=INK, ow=1.2)
        cv.circle((34, 32, 42), (ex, ey), 3.8)
        cv.circle(WHITE, (ex - 1.1, ey - 1.3), 1.2)

    # ---------- порядок отрисовки: дальняя рука, ноги, торс, голова, ближняя рука ----------
    arm_skin(*far, is_near=False)
    sleeve(far[0], far[1])
    cloth(leg_fn(r_knee, r_ank), dark=60)
    shoe(-42)
    cloth(leg_fn(f_knee, f_ank))
    # карман-карго с чёрной бирочкой на бедре
    mx, my = (hip[0] + f_knee[0]) / 2.0 + 1, (hip[1] + f_knee[1]) / 2.0 + 1
    d = (f_knee[0] - hip[0], f_knee[1] - hip[1])
    dl = math.hypot(*d)
    d = (d[0] / dl, d[1] / dl)
    n = (-d[1], d[0])
    pk = [(mx + d[0] * sx + n[0] * sy, my + d[1] * sx + n[1] * sy) for sx, sy in ((-8, -7), (8, -7), (8, 7), (-8, 7))]
    cv.poly((46, 54, 78), pk, outline=CLOTH_OUT, ow=1.2)
    cv.stroke(CLOTH_OUT, [pk[0], pk[1]], 1.3)
    tag = [(mx + d[0] * sx + n[0] * sy, my + d[1] * sx + n[1] * sy) for sx, sy in ((3, -2), (8, -2), (8, 2), (3, 2))]
    cv.poly((12, 12, 16), tag)
    shoe(34)
    cloth(lambda c, col, g: c.capsule(col, (-2, -92), (4, -120), 58 + g))   # футболка-торс
    cv.stroke(CLOTH_OUT, [(-16, -86), (-9, -72)], 1.4)
    cv.stroke(CLOTH_OUT, [(14, -84), (18, -70)], 1.4)

    arm_skin(*near, is_near=True)
    sleeve(near[0], near[1])

    # ---------- голова ----------
    hx, hy = 12, -162
    cv.shaded_ellipse(hx + 14, hy + 26, 40, 20, SKIN_D, SKIN, SKIN_L, outline=SKIN_OUT, ow=2.2)   # обвисшие щёки
    cv.shaded_ellipse(hx, hy, 46, 37, SKIN_D, SKIN, SKIN_L, outline=SKIN_OUT, ow=2.2)
    cv.poly((255, 232, 222), oriented_ellipse(hx + 2, hy + 14, 10, 3.4, -0.3))
    if pose == "dead":                                       # рот открыт: «о-о-ох»
        cv.ellipse((104, 36, 58), hx + 30, hy + 31, 15, 8.5, outline=SKIN_OUT, ow=1.6)
        cv.ellipse((232, 110, 130), hx + 30, hy + 35, 8, 3.5)
    else:                                                    # широкий грустный рот с толстыми губами
        pts = []
        for i in range(11):
            t = i / 10.0
            pts.append((hx + 4 + t * 48, hy + 37 - 8 * (1 - (2 * t - 1) ** 2)))
        cv.stroke(LIP, [(x, y + 3.4) for x, y in pts[1:-1]], 5.0, outline=SKIN_OUT, ow=1.2)   # нижняя губа
        cv.stroke(LIP_D, pts, 5.4, outline=SKIN_OUT, ow=1.2)
        cv.stroke((226, 138, 146), [(x, y - 2.2) for x, y in pts[1:-1]], 1.8)                  # блик на губе
    cv.ellipse((170, 92, 106), hx + 40, hy + 32, 13, 3.4)                                       # тень под носом
    cv.shaded_ellipse(hx + 40, hy + 6, 16, 25, (196, 112, 122), (240, 162, 162), (255, 224, 216),
                      outline=SKIN_OUT, ow=2.2)                                                 # нос-капля
    cv.ellipse((255, 240, 232), hx + 35, hy - 4, 3.6, 8)
    cv.circle(WHITE, (hx + 33, hy - 9), 2.0)
    ex, ey = hx + 19, hy - 4
    if pose == "dead":
        cv.stroke(INK, [(ex - 5.5, ey - 5.5), (ex + 5.5, ey + 5.5)], 2.6)
        cv.stroke(INK, [(ex - 5.5, ey + 5.5), (ex + 5.5, ey - 5.5)], 2.6)
    else:
        cv.circle(SKIN_D, (ex, ey), 7.6, outline=SKIN_OUT, ow=1.2)
        cv.circle((20, 18, 26), (ex, ey), 4.7)
        cv.circle(WHITE, (ex - 1.5, ey - 1.7), 1.5)
        cv.stroke(SKIN_OUT, [(ex - 8, ey - 5.5), (ex + 8, ey - 3.5)], 2.2)              # усталое веко
    # шапка-бини
    cyc = hy - 17
    dome = [(hx - 1 + 47 * math.cos(math.pi + math.pi * i / 24.0), cyc - 3 + 34 * math.sin(math.pi + math.pi * i / 24.0))
            for i in range(25)]
    cv.poly(BEANIE, dome, outline=INK, ow=2)
    for i in range(-3, 4):
        x0 = hx - 1 + i * 12
        ytop = cyc - 3 - 34 * math.sqrt(max(0.0, 1 - (i * 12 / 47.0) ** 2))
        cv.stroke(BEANIE_L, [(x0, cyc - 6), (x0 * 0.9 + (hx - 1) * 0.1, ytop + 5)], 1.5)
    cv.capsule((56, 54, 62), (hx - 40, cyc + 1), (hx + 43, cyc - 3), 16, outline=INK, ow=2)
    for i in range(-6, 7):
        x = hx + 1 + i * 6
        cv.stroke((30, 28, 36), [(x, cyc - 6.5 - i * 0.05), (x + 0.8, cyc + 3.5)], 1.1)
    cv.rrect((94, 92, 104), hx + 20, cyc - 8.5, 19, 9.5, 2, outline=INK, ow=1.2)         # нашивка со скейтом
    cv.stroke(WHITE, [(hx + 23.5, cyc - 4), (hx + 35.5, cyc - 4.4)], 2.0)
    cv.circle(WHITE, (hx + 25, cyc - 1), 1.2)
    cv.circle(WHITE, (hx + 34, cyc - 1.4), 1.2)

    cv.outline(INK, 2.4)
    return cv.finish(), (96, 178)


def make_board():
    """Скейтборд без колёс (колёса вращаются отдельно). pivot — точка касания земли."""
    cv = Canvas(150, 60, 3, (75, 52), 1.0)
    for sx in (-WHEEL_DX, WHEEL_DX):
        cv.poly((178, 184, 196), [(sx - 10, -31), (sx + 10, -31), (sx + 7, -17), (sx - 7, -17)], outline=INK, ow=1.6)
        cv.rrect((122, 128, 142), sx - 12, -28, 24, 5, 2)
        cv.circle((92, 98, 110), (sx, -WHEEL_R), 3.2)
    pts = [(-60, -43), (-50, -34), (50, -34), (60, -43)]
    cv.stroke(DECK_D, pts, 9.5, outline=INK, ow=2)
    cv.stroke(DECK, [(x, y - 0.6) for x, y in pts], 6.6)
    cv.stroke(DECK_L, [(x, y - 2.2) for x, y in pts], 1.8)
    cv.stroke((46, 46, 54), [(x, y - 4.4) for x, y in pts], 3.0)      # шкурка
    for x, y in ((-60, -43), (60, -43)):
        cv.circle((250, 232, 200), (x, y), 3.0, outline=INK, ow=1.0)
    return cv.finish(), (75, 52)


def make_wheel():
    cv = Canvas(30, 30, 4, (15, 15), 1.0)
    cv.circle(INK, (0, 0), WHEEL_R + 1.2)
    cv.shaded_ellipse(0, 0, WHEEL_R, WHEEL_R, (206, 148, 30), (250, 208, 70), (255, 242, 170), lx=-0.3, ly=-0.4)
    cv.circle((72, 64, 62), (0, 0), 5.4)
    cv.circle((156, 156, 168), (0, 0), 3.2)
    cv.capsule(INK, (5.5, 0), (9.6, 0), 2.2)                            # метка вращения
    return cv.finish(), (15, 15)


def make_shadow():
    cv = Canvas(170, 40, 2, (85, 20), 1.0)
    for i in range(14):
        t = i / 13.0
        pygame.draw.ellipse(cv.s, (10, 20, 10, int(105 * t)),
                            cv._erect(0, 0, 80 * (1 - 0.8 * t), 15 * (1 - 0.8 * t)))
    return cv.finish()


PILL_COLORS = [((232, 56, 72), (250, 250, 255)),      # красно-белая
               ((52, 140, 246), (250, 250, 255)),     # сине-белая
               ((255, 208, 58), (255, 132, 40))]      # жёлто-оранжевая


def make_pill(ca, cb, size=(46, 26)):
    w, h = size
    cw, ch = w + 10, h + 10
    cv = Canvas(cw, ch, 4, (cw / 2.0, ch / 2.0), 1.0)
    r, L = h / 2.0, (w - h) / 2.0
    cv.capsule(INK, (-L, 0), (L, 0), h + 4.4)

    def mask(c):
        c.capsule(WHITE, (-L, 0), (L, 0), h)

    def paint(g):
        y0, y1 = cv.P((0, -r))[1], cv.P((0, r))[1]
        xm = cv.P((0, 0))[0]
        for y in range(g.get_height()):
            t = clamp((y - y0) / float(max(1, y1 - y0)), 0.0, 1.0)
            pygame.draw.line(g, lerp_col(lerp_col(ca, WHITE, 0.30), lerp_col(ca, (0, 0, 20), 0.34), t), (0, y), (xm, y))
            pygame.draw.line(g, lerp_col(lerp_col(cb, WHITE, 0.30), lerp_col(cb, (0, 0, 40), 0.30), t), (xm, y),
                             (g.get_width(), y))
    cv.filled_by(mask, paint)
    cv.stroke((70, 44, 56), [(0, -r + 0.5), (0, r - 0.5)], 1.3)
    cv.capsule(lerp_col(ca, WHITE, 0.72), (-L + 1.5, -r * 0.5), (-1.0, -r * 0.5), 2.8)
    cv.capsule(WHITE, (1.0, -r * 0.5), (L - 1.5, -r * 0.5), 2.8)
    return cv.finish()


def make_pill_frames():
    """Для каждого цвета — 24 заранее повёрнутых кадра (быстрее, чем вращать на лету)."""
    out = []
    for ca, cb in PILL_COLORS:
        base = make_pill(ca, cb)
        out.append([pygame.transform.rotozoom(base, deg, 1.0) for deg in range(0, 360, 15)])
    return out


def make_glow(size=96, col=(255, 240, 150), alpha=150):
    cv = Canvas(size, size, 2, (size / 2.0, size / 2.0), 1.0)
    cv.glow(0, 0, size / 2.0 - 1, col, alpha)
    return cv.finish()



# =============================================================================
#  ГРАФИКА, ЧАСТЬ 2: препятствия, декор, небо и холмы
# =============================================================================
TILE = 2560              # ширина бесшовного тайла фоновых слоёв


# --- препятствия (возвращают (картинка, pivot на земле)) ----------------------
def obs_cone():
    cv = Canvas(56, 76)
    cv.rrect((36, 36, 44), 4, 64, 48, 8, 3, outline=INK, ow=1.8)
    cv.rrect((78, 78, 90), 6, 65, 44, 2.5, 1.2)
    cv.poly((255, 128, 34), [(23, 8), (33, 8), (48, 64), (8, 64)], outline=INK, ow=1.8)
    cv.poly((222, 92, 22), [(30, 8), (33, 8), (48, 64), (33, 64)])
    cv.poly((255, 178, 100), [(24, 10), (27, 10), (15, 62), (11, 62)])

    def xl(y):
        return 23 - 15 * (y - 8) / 56.0

    def xr(y):
        return 33 + 15 * (y - 8) / 56.0
    for y1, y2 in ((22, 31), (42, 52)):
        cv.poly((250, 250, 246), [(xl(y1), y1), (xr(y1), y1), (xr(y2), y2), (xl(y2), y2)])
        cv.poly((212, 212, 212), [(xr(y1) - 6, y1), (xr(y1), y1), (xr(y2), y2), (xr(y2) - 8, y2)])
    cv.ellipse((196, 72, 20), 28, 8, 5.5, 2.4, outline=INK, ow=1.2)
    return cv.finish(), (28, 72)


def obs_crate(n=1):
    cw = 58 * n + 12
    cv = Canvas(cw, 72)
    for i in range(n):
        x0 = 6 + 58 * i
        cv.rrect((150, 98, 48), x0, 6, 58, 58, 4, outline=INK, ow=2)
        cv.rrect((208, 152, 84), x0 + 8, 14, 42, 42, 2)
        for yy in (25, 36, 47):
            cv.stroke((178, 122, 62), [(x0 + 9, yy), (x0 + 49, yy)], 1.6)
        cv.stroke((150, 98, 48), [(x0 + 10, 54), (x0 + 48, 16)], 8, outline=INK, ow=1.2)
        for nx, ny in ((x0 + 4, 10), (x0 + 54, 10), (x0 + 4, 60), (x0 + 54, 60)):
            cv.circle((84, 62, 50), (nx, ny), 2.0)
        cv.stroke((240, 196, 126), [(x0 + 7, 8.2), (x0 + 51, 8.2)], 1.8)
    return cv.finish(), (cw / 2.0, 64)


def obs_barrel():
    cv = Canvas(60, 80)
    x0, y0, bw, bh = 7, 10, 46, 62
    cv.rrect(INK, x0, y0, bw, bh, 9, outline=INK, ow=2)

    def mask(c):
        c.rrect(WHITE, x0, y0, bw, bh, 9)

    def paint(g):
        for i in range(g.get_width()):
            t = clamp((i / float(cv.ss) - x0) / bw, 0.0, 1.0)
            col = lerp_col((132, 26, 30), (240, 84, 74), clamp(1.0 - abs(t - 0.32) * 1.7, 0.0, 1.0))
            pygame.draw.line(g, col, (i, 0), (i, g.get_height()))
    cv.filled_by(mask, paint)
    for yy in (21, 39, 57):
        cv.rrect((104, 22, 28), x0 - 0.5, yy, bw + 1, 5, 2.4, outline=INK, ow=1.2)
        cv.rrect((190, 56, 56), x0 + 2, yy + 0.6, bw - 4, 1.6, 0.8)
    cv.rrect((255, 204, 0), x0 + 5, 28.5, bw - 10, 9, 1.5, outline=INK, ow=1.2)          # знак опасности
    for xx in (x0 + 6, x0 + 15, x0 + 24, x0 + 33):
        cv.poly(INK, [(xx, 29.5), (xx + 4.5, 29.5), (xx + 7.5, 36.5), (xx + 3, 36.5)])
    cv.ellipse((176, 60, 64), 30, 12, 21, 5.4, outline=INK, ow=1.6)
    cv.ellipse((214, 96, 90), 30, 11.6, 17, 3.4)
    return cv.finish(), (30, 72)


def obs_tires():
    cv = Canvas(90, 90)

    def tire(x, y, w, h):
        cv.rrect((44, 44, 50), x, y, w, h, h / 2.0, outline=INK, ow=1.8)
        cv.rrect((92, 92, 104), x + 6, y + 2.5, w - 12, 3.6, 1.8)
        for i in range(9):
            xx = x + 9 + i * (w - 18) / 8.0
            cv.stroke((26, 26, 32), [(xx, y + 8), (xx, y + h - 5)], 1.5)
        cv.stroke((200, 200, 210), [(x + 14, y + h / 2.0 + 1), (x + w - 14, y + h / 2.0 + 1)], 1.0)
    tire(12, 62, 66, 22)
    tire(8, 42, 66, 22)
    tire(15, 22, 58, 22)
    return cv.finish(), (45, 84)


def obs_rock():
    cv = Canvas(100, 74)
    cv.poly((148, 152, 164), [(8, 64), (5, 46), (14, 28), (32, 13), (56, 8), (78, 16), (91, 34), (95, 52), (89, 64)],
            outline=INK, ow=2)
    cv.poly((188, 192, 202), [(14, 28), (32, 13), (56, 8), (52, 26), (28, 38)])
    cv.poly((112, 116, 130), [(78, 16), (91, 34), (95, 52), (89, 64), (70, 62), (66, 36)])
    cv.stroke((84, 88, 100), [(40, 30), (48, 44), (44, 58)], 1.8)
    cv.stroke((84, 88, 100), [(70, 40), (62, 50)], 1.6)
    for mx, my, mr in ((36, 14, 4), (46, 12, 5), (58, 11, 4)):
        cv.ellipse((92, 152, 70), mx, my + 2, mr * 1.6, mr)
    return cv.finish(), (50, 66)


def obs_barrier():
    cv = Canvas(136, 84)
    for lx in (18, 110):
        cv.rrect((96, 100, 114), lx - 4, 44, 9, 32, 2, outline=INK, ow=1.6)
        cv.rrect((60, 64, 76), lx - 8, 72, 17, 5, 2, outline=INK, ow=1.4)
    bx, by, bw, bh = 6, 8, 124, 36
    cv.rrect(INK, bx, by, bw, bh, 5, outline=INK, ow=2)

    def mask(c):
        c.rrect(WHITE, bx, by, bw, bh, 5)

    def paint(g):
        g.fill((250, 250, 250))
        for i in range(-3, 12):
            x = bx + i * 22
            pygame.draw.polygon(g, (226, 58, 54), [cv.P((x, by)), cv.P((x + 11, by)),
                                                   cv.P((x - 3, by + bh)), cv.P((x - 14, by + bh))])
    cv.filled_by(mask, paint)
    cv.rrect((80, 84, 96), 62, 2, 12, 7, 2, outline=INK, ow=1.2)
    cv.circle((255, 196, 50), (68, 3), 5.0, outline=INK, ow=1.2)
    return cv.finish(), (68, 77)


def obs_spikes():
    cv = Canvas(110, 46)
    cv.rrect((74, 78, 90), 6, 32, 98, 9, 3, outline=INK, ow=1.8)
    cv.rrect((120, 126, 142), 8, 33, 94, 2.5, 1.2)
    for i in range(7):
        x = 12 + i * 14
        cv.poly((204, 210, 220), [(x, 33), (x + 13, 33), (x + 6.5, 5)], outline=INK, ow=1.4)
        cv.poly((150, 158, 172), [(x + 6.5, 5), (x + 13, 33), (x + 6.5, 33)])
        cv.stroke((246, 248, 252), [(x + 3, 30), (x + 6, 12)], 1.2)
    return cv.finish(), (55, 40)


def obs_tire():
    """Катящаяся покрышка: pivot — центр (вращается вокруг него)."""
    cv = Canvas(76, 76)
    c = (38, 38)
    cv.circle(INK, c, 34)
    cv.circle((44, 44, 50), c, 32)
    for i in range(22):
        a = 2 * math.pi * i / 22
        cv.stroke((80, 80, 92), [(38 + math.cos(a) * 25, 38 + math.sin(a) * 25),
                                 (38 + math.cos(a) * 30.5, 38 + math.sin(a) * 30.5)], 3.4)
    cv.circle((26, 26, 32), c, 24)
    cv.circle((58, 58, 66), c, 20.5)
    cv.shaded_ellipse(38, 38, 14, 14, (120, 126, 140), (178, 184, 196), (232, 236, 244), outline=INK, ow=1.4)
    for i in range(5):
        a = 2 * math.pi * i / 5 + 0.3
        cv.circle((60, 64, 74), (38 + math.cos(a) * 8.5, 38 + math.sin(a) * 8.5), 2.1)
    cv.circle((70, 74, 84), c, 3.5)
    cv.stroke((120, 120, 134), [(38 + math.cos(math.radians(a)) * 27.5, 38 + math.sin(math.radians(a)) * 27.5)
                                for a in range(205, 251, 9)], 2.2)
    return cv.finish(), (38, 38)


# --- декор -------------------------------------------------------------------
def decor_bush(variant):
    cv = Canvas(120, 76)
    for x, y, r in ((59, 40, 28), (30, 50, 21), (88, 50, 21), (44, 54, 19), (74, 54, 19)):
        cv.shaded_ellipse(x, y, r, r * 0.94, (38, 108, 52), (84, 172, 68), (152, 222, 104), outline=(22, 62, 34), ow=1.8)
    if variant == 1:
        for bx, by in ((40, 40), (66, 34), (82, 50), (52, 58)):
            cv.circle((226, 60, 74), (bx, by), 3.4, outline=(110, 24, 40), ow=1.0)
            cv.circle(WHITE, (bx - 1, by - 1), 0.9)
    else:
        for bx, by in ((36, 46), (60, 32), (84, 48), (70, 58)):
            for k in range(5):
                a = k * 2 * math.pi / 5
                cv.circle((255, 236, 245), (bx + math.cos(a) * 3.6, by + math.sin(a) * 3.6), 2.4)
            cv.circle((255, 200, 60), (bx, by), 2.0)
    return cv.finish(), (60, 70)


def decor_flowers(variant):
    petal, mid = [((255, 120, 170), (255, 220, 90)), ((255, 255, 255), (255, 200, 60)),
                  ((255, 204, 60), (200, 100, 30))][variant % 3]
    cv = Canvas(64, 46)
    for bx in range(6, 60, 5):
        h = 10 + hash01(bx, variant) * 12
        cv.stroke((70, 156, 50), [(bx, 44), (bx + (hash01(bx, 9) - 0.5) * 6, 44 - h)], 2.2)
    for fx, fy in ((16, 26), (32, 18), (48, 28)):
        cv.stroke((60, 140, 44), [(fx, 44), (fx, fy)], 1.8)
        for k in range(5):
            a = k * 2 * math.pi / 5
            cv.circle(petal, (fx + math.cos(a) * 4.6, fy + math.sin(a) * 4.6), 3.2, outline=(80, 40, 60), ow=0.8)
        cv.circle(mid, (fx, fy), 2.8)
    return cv.finish(), (32, 44)


def decor_stone():
    cv = Canvas(56, 36)
    cv.shaded_ellipse(20, 24, 17, 11, (92, 96, 108), (148, 152, 164), (200, 204, 214), outline=INK, ow=1.6)
    cv.shaded_ellipse(40, 27, 11, 7.5, (92, 96, 108), (140, 144, 156), (192, 196, 206), outline=INK, ow=1.4)
    return cv.finish(), (28, 33)


def decor_sign():
    cv = Canvas(100, 112)
    cv.rrect((122, 82, 46), 44, 54, 12, 56, 3, outline=INK, ow=1.8)
    cv.rrect((238, 204, 128), 8, 10, 84, 50, 7, outline=INK, ow=2)
    cv.rrect((252, 228, 166), 12, 14, 76, 11, 5)
    cv.poly((122, 70, 40), [(20, 34), (54, 34), (54, 24), (80, 38), (54, 52), (54, 42), (20, 42)])
    for nx, ny in ((14, 17), (86, 17), (14, 54), (86, 54)):
        cv.circle((110, 86, 60), (nx, ny), 1.8)
    return cv.finish(), (50, 108)


def decor_wall():
    """Бетонная стена со граффити — привет скейт-парку с фотографии."""
    cv = Canvas(240, 150)
    cv.rrect((158, 166, 184), 8, 32, 224, 114, 6, outline=INK, ow=2)
    cv.rrect((122, 130, 148), 2, 22, 236, 14, 5, outline=INK, ow=1.8)
    for x in (64, 120, 176):
        cv.stroke((132, 140, 158), [(x, 38), (x, 144)], 1.6)
    cv.rrect((134, 142, 160), 8, 128, 224, 18, 4)
    for x, y, rx, ry, col in ((44, 84, 30, 20, (255, 96, 150)), (196, 70, 26, 18, (70, 210, 240)),
                              (150, 118, 34, 14, (255, 214, 70))):
        cv.ellipse(col, x, y, rx, ry)
    txt = render_gradient_text("BLOB", 128, (255, 240, 100), (255, 84, 160), outline=(44, 20, 70), th=9)
    pos = cv.P((118, 84))
    cv.put(txt, (pos[0] - txt.get_width() // 2, pos[1] - txt.get_height() // 2))
    for x, y in ((66, 110), (98, 112), (150, 112), (176, 110)):                       # потёки краски
        cv.capsule((255, 84, 160), (x, y), (x, y + 10 + hash01(x) * 8), 2.4)
    cv.shaded_ellipse(206, 112, 15, 12, (196, 112, 122), (240, 162, 162), (255, 224, 216), outline=INK, ow=1.4)
    cv.circle(INK, (200, 108), 1.8)
    cv.stroke(INK, [(198, 118), (206, 116), (214, 119)], 1.6)
    return cv.finish(), (120, 146)


def decor_palm():
    cv = Canvas(170, 250)
    P0, P1, P2 = (78, 246), (64, 168), (102, 92)
    trunk = []
    for i in range(21):
        t = i / 20.0
        trunk.append(((1 - t) ** 2 * P0[0] + 2 * (1 - t) * t * P1[0] + t * t * P2[0],
                      (1 - t) ** 2 * P0[1] + 2 * (1 - t) * t * P1[1] + t * t * P2[1]))
    for i in range(20):
        cv._capsule(INK, trunk[i], trunk[i + 1], 15.5 - 6 * i / 20.0 + 3.2)
    for i in range(20):
        w = 15.5 - 6 * i / 20.0
        cv._capsule((156, 108, 64), trunk[i], trunk[i + 1], w)
        cv._capsule((188, 140, 88), (trunk[i][0] - w * 0.22, trunk[i][1]), (trunk[i + 1][0] - w * 0.22, trunk[i + 1][1]), w * 0.3)
    for i in range(2, 20, 2):
        (x0, y0), (x1, y1) = trunk[i], trunk[i + 1]
        ln = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx, ny = -(y1 - y0) / ln, (x1 - x0) / ln
        w = (15.5 - 6 * i / 20.0) / 2.0
        cv.stroke((110, 72, 40), [(x0 - nx * w, y0 - ny * w), (x0 + nx * w, y0 + ny * w + 1.5)], 1.6)
    C = P2
    for k, (ang, L) in enumerate(((-172, 78), (-146, 92), (-118, 80), (-96, 56), (-62, 80), (-34, 92), (-8, 78))):
        a = math.radians(ang)
        d = (math.cos(a), math.sin(a))
        p1 = (C[0] + d[0] * L * 0.55, C[1] + d[1] * L * 0.55 - L * 0.28)
        p2 = (C[0] + d[0] * L, C[1] + d[1] * L + L * 0.42)
        left, right, mid = [], [], []
        for i in range(15):
            t = i / 14.0
            bx = (1 - t) ** 2 * C[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            by = (1 - t) ** 2 * C[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            tx = 2 * (1 - t) * (p1[0] - C[0]) + 2 * t * (p2[0] - p1[0])
            ty = 2 * (1 - t) * (p1[1] - C[1]) + 2 * t * (p2[1] - p1[1])
            ln = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / ln, tx / ln
            wd = (1 + 0.34 * (-1) ** i) * 11 * math.sin(math.pi * min(1.0, t * 1.08)) ** 0.7 + 0.6
            left.append((bx + nx * wd, by + ny * wd))
            right.append((bx - nx * wd, by - ny * wd))
            mid.append((bx, by))
        col = (40, 132, 60) if k % 2 == 0 else (58, 164, 74)
        cv.poly(col, left + right[::-1], outline=(20, 76, 36), ow=1.4)
        cv.stroke((124, 212, 108), mid[1:-1], 1.6)
    for cx, cy in ((C[0] - 6, C[1] + 8), (C[0] + 6, C[1] + 9), (C[0], C[1] + 14)):
        cv.circle((124, 82, 46), (cx, cy), 6.5, outline=INK, ow=1.3)
        cv.circle((178, 132, 84), (cx - 1.8, cy - 1.8), 1.8)
    return cv.finish(), (78, 246)


# --- небо и облака -----------------------------------------------------------
def make_cloud(w, h, seed):
    rnd = random.Random(seed)
    cv = Canvas(w, h, 3)
    n = 5 + w // 70
    puffs = []
    for i in range(n):
        t = (i + 0.5) / n
        r = h * rnd.uniform(0.22, 0.30) + h * 0.22 * (1 - abs(t - 0.5) * 2)
        puffs.append((w * (0.12 + 0.76 * t), h * 0.66 - r * 0.55 - rnd.uniform(0, h * 0.06), r))
    puffs.sort(key=lambda p: -p[2])
    for x, y, r in puffs:
        cv.shaded_ellipse(x, y, r, r * 0.92, (196, 220, 246), (244, 250, 255), (255, 255, 255),
                          lx=-0.2, ly=-0.7, outline=(176, 204, 236), ow=1.0)
    cv.ellipse((226, 240, 252), w / 2.0, h * 0.80, w * 0.44, h * 0.13)
    return cv.finish()


def make_sky():
    surf = pygame.Surface((W, H))
    stops = [(0.0, (48, 128, 222)), (0.55, (112, 192, 246)), (1.0, (214, 238, 252))]
    for y in range(H):
        t = y / float(H - 1)
        for (t0, c0), (t1, c1) in zip(stops[:-1], stops[1:]):
            if t0 <= t <= t1:
                col = lerp_col(c0, c1, (t - t0) / (t1 - t0))
                break
        pygame.draw.line(surf, col, (0, y), (W, y))
    sun = Canvas(320, 320, 2, (160, 160), 1.0)
    sun.glow(0, 0, 158, (255, 244, 190), 150, 30)
    sun.circle((255, 250, 214), (0, 0), 48)
    sun.circle((255, 240, 170), (0, 0), 40)
    sun.circle((255, 252, 230), (-6, -6), 26)
    surf.blit(sun.finish(), (int(W * 0.80) - 160, 130 - 160))
    return surf.convert()


# --- параллаксные слои холмов ---------------------------------------------------
class Layer:
    def __init__(self, img, fx, fy, y0, bottom):
        self.img, self.fx, self.fy, self.y0, self.bottom = img, fx, fy, y0, bottom


def _ridge(seed, base, amps, ks):
    rnd = random.Random(seed)
    ph = [rnd.uniform(0, 6.283) for _ in amps]
    pts = []
    for x in range(0, TILE + 1, 8):
        y = base
        for a, k, p in zip(amps, ks, ph):
            y += a * math.sin(2 * math.pi * k * x / TILE + p)
        pts.append((x, y))
    return pts


def _ridge_y(pts, x):
    return pts[int(clamp(x, 0, TILE) / 8)][1]


def _pine(cv, x, y, s):
    cv.capsule((104, 70, 40), (x, y), (x, y - 12 * s), 4.2 * s)
    for i, col in enumerate(((34, 108, 64), (44, 128, 74), (58, 150, 88))):
        yy = y - (6 + 11 * i) * s
        hw = (17 - 3 * i) * s
        cv.poly(col, [(x - hw, yy), (x + hw, yy), (x, yy - 22 * s)], outline=(22, 78, 46), ow=0.8)


def _round_tree(cv, x, y, s):
    cv.capsule((110, 74, 44), (x, y), (x, y - 16 * s), 4.6 * s)
    cv.shaded_ellipse(x, y - 26 * s, 15 * s, 14 * s, (44, 124, 64), (84, 174, 84), (150, 222, 116),
                      outline=(24, 78, 42), ow=0.9)


def make_layer(seed, strip_h, base, amps, ks, top, bottom, fx, fy, y0, deco=None):
    pts = _ridge(seed, base, amps, ks)
    cv = Canvas(TILE, strip_h, 2)
    cv.grad_poly(pts + [(TILE, strip_h), (0, strip_h)], top, bottom, min(y for _, y in pts) - 6, strip_h)
    if deco:
        deco(cv, pts, random.Random(seed + 99))
    return Layer(cv.finish(), fx, fy, y0, bottom)


def deco_mid(cv, pts, rnd):
    n = 46
    for i in range(n):
        x = 60 + i * (TILE - 120) / float(n) + rnd.uniform(-20, 20)
        y = _ridge_y(pts, x) + 6
        s = rnd.uniform(0.8, 1.25)
        (_pine if rnd.random() < 0.55 else _round_tree)(cv, x, y, s)


def make_deco_near(palm, palm_pivot):
    def deco(cv, pts, rnd):
        for i in range(18):
            x = 90 + i * (TILE - 180) / 18.0 + rnd.uniform(-40, 40)
            y = _ridge_y(pts, x) + 4
            if rnd.random() < 0.42:
                sc = rnd.uniform(0.42, 0.62)
                sp = pygame.transform.smoothscale(palm, (int(palm.get_width() * sc * cv.ss),
                                                         int(palm.get_height() * sc * cv.ss)))
                cv.put(sp, (int(x * cv.ss - palm_pivot[0] * sc * cv.ss), int(y * cv.ss - palm_pivot[1] * sc * cv.ss)))
            else:
                s = rnd.uniform(0.7, 1.2)
                for dx, r in ((-14, 12), (0, 16), (14, 12)):
                    cv.shaded_ellipse(x + dx * s, y - 6 * s, r * s, r * 0.82 * s, (40, 118, 58), (78, 166, 76),
                                      (140, 214, 104), outline=(22, 70, 38), ow=0.9)
    return deco


# --- сборщик всех картинок ---------------------------------------------------------
class Assets:
    def __init__(self, progress=None):
        def rep(f):
            if progress:
                progress(f)
        self.rot_cache = {}
        self.char = dict((p, make_character(p)) for p in ("ride", "air", "dead"))
        rep(0.25)
        self.board, self.wheel = make_board(), make_wheel()
        bd, bp = self.board
        wh, wp = self.wheel
        full = bd.copy()
        for dx in (-WHEEL_DX, WHEEL_DX):
            full.blit(wh, (bp[0] + dx - wp[0], bp[1] - WHEEL_R - wp[1]))
        self.board_full = (full, bp)
        self.shadow = make_shadow()
        self.pills = make_pill_frames()
        self.glow = make_glow()
        rep(0.35)
        self.obst = {"cone": obs_cone(), "crate": obs_crate(1), "crate2": obs_crate(2), "barrel": obs_barrel(),
                     "tires": obs_tires(), "rock": obs_rock(), "barrier": obs_barrier(), "spikes": obs_spikes(),
                     "tire": obs_tire()}
        rep(0.5)
        self.decor = {"bush0": decor_bush(0), "bush1": decor_bush(1), "flowers0": decor_flowers(0),
                      "flowers1": decor_flowers(1), "flowers2": decor_flowers(2), "stone": decor_stone(),
                      "sign": decor_sign(), "wall": decor_wall(), "palm": decor_palm()}
        rep(0.6)
        self.clouds = [make_cloud(260, 110, 1), make_cloud(340, 130, 2), make_cloud(200, 90, 3), make_cloud(300, 120, 4)]
        self.sky = make_sky()
        rep(0.7)
        palm, ppv = self.decor["palm"]
        self.layers = [
            make_layer(11, 300, 170, [56, 30, 14, 7], [3, 7, 17, 41], (170, 198, 232), (196, 220, 240), 0.06, 0.03, 150),
            make_layer(23, 300, 150, [38, 20, 9], [5, 11, 23], (118, 190, 128), (150, 208, 140), 0.16, 0.08, 250, deco_mid),
            make_layer(37, 380, 215, [30, 16, 7], [7, 13, 29], (74, 156, 86), (96, 176, 90), 0.34, 0.16, 245,
                       make_deco_near(palm, ppv)),
        ]
        rep(1.0)

    def obstacle_img(self, kind, ang):
        """Картинка препятствия, наклонённая по склону (кэшируется по углу)."""
        b = int(round(ang / 0.035))
        got = self.rot_cache.get((kind, b))
        if got is None:
            img, pv = self.obst[kind]
            a = b * 0.035
            rot = pygame.transform.rotozoom(img, -math.degrees(a), 1.0)
            w, h = img.get_size()
            vx, vy = pv[0] - w / 2.0, pv[1] - h / 2.0
            ca, sa = math.cos(a), math.sin(a)
            off = (rot.get_width() / 2.0 + vx * ca - vy * sa, rot.get_height() / 2.0 + vx * sa + vy * ca)
            got = (rot, off)
            self.rot_cache[(kind, b)] = got
        return got



# =============================================================================
#  МИР: холмы, препятствия, таблетки
# =============================================================================
class Terrain:
    """Холмы как в Hill Climb Racing: случайные контрольные точки, между ними —
    косинусная интерполяция (на вершинах и в ложбинах земля пологая).
    С расстоянием холмы становятся выше и круче."""

    def __init__(self, seed, demo=False):
        self.rng = random.Random(seed * 7919 + 13)
        self.demo = demo
        self.xs = [-2500.0, 0.0, 1300.0]
        self.ys = [0.0, 0.0, 0.0]
        self.decor = []        # (x, вид, масштаб, отражение) — кусты, пальмы, стены...
        self.pebbles = []      # (x, глубина, размер, оттенок) — камешки в земле
        self._dx = -600.0
        self._px = -600.0
        self.ensure(4200)

    def dif(self, x):
        return 0.22 if self.demo else clamp(x / PX_PER_M / 3000.0, 0.0, 1.0)

    def ensure(self, x):
        while self.xs[-1] < x:
            self._add()

    def _add(self):
        rng = self.rng
        x0, y0 = self.xs[-1], self.ys[-1]
        d = self.dif(x0)
        length = rng.uniform(430, 860) * lerp(0.95, 1.25, d)
        dy_max = lerp(0.32, 0.72, d) * 2.0 * length / math.pi     # ограничение крутизны склона
        target = rng.uniform(-1.0, 1.0) * lerp(120, 300, d)
        dy = clamp(target - y0, -dy_max, dy_max)
        if rng.random() < 0.16:
            dy *= 0.15                                             # иногда — почти ровный участок
        self.xs.append(x0 + length)
        self.ys.append(y0 + dy)
        self._decorate(x0 + length)

    def _decorate(self, x1):
        rng = self.rng
        while self._dx < x1:
            r = rng.random()
            kind = None
            if r < 0.24:
                kind = "bush%d" % rng.randint(0, 1)
            elif r < 0.40:
                kind = "palm"
            elif r < 0.60:
                kind = "flowers%d" % rng.randint(0, 2)
            elif r < 0.72:
                kind = "stone"
            elif r < 0.78:
                kind = "sign"
            elif r < 0.84:
                kind = "wall"
            if kind:
                self.decor.append((self._dx, kind, rng.uniform(0.85, 1.15), rng.random() < 0.5))
            self._dx += rng.uniform(110, 300)
        while self._px < x1:
            self.pebbles.append((self._px, rng.uniform(46, 620), rng.uniform(4, 11), rng.random()))
            self._px += rng.uniform(26, 80)

    def _seg(self, x):
        i = bisect.bisect_right(self.xs, x) - 1
        return 0 if i < 0 else min(i, len(self.xs) - 2)

    def h(self, x):
        """Высота земли (y вниз) в точке x."""
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return ys[0]
        i = self._seg(x)
        t = (x - xs[i]) / (xs[i + 1] - xs[i])
        if t >= 1.0:
            return ys[i + 1]
        return ys[i] + (ys[i + 1] - ys[i]) * (1.0 - math.cos(math.pi * t)) * 0.5

    def slope(self, x):
        """dy/dx: > 0 — спуск вправо, < 0 — подъём."""
        xs, ys = self.xs, self.ys
        if x <= xs[0]:
            return 0.0
        i = self._seg(x)
        ln = xs[i + 1] - xs[i]
        t = (x - xs[i]) / ln
        if t >= 1.0:
            return 0.0
        return (ys[i + 1] - ys[i]) * (math.pi * 0.5) * math.sin(math.pi * t) / ln

    def curv(self, x):
        """Кривизна: > 0 на вершине холма (там рыба может «взлететь»)."""
        return (self.h(x + 12.0) - 2.0 * self.h(x) + self.h(x - 12.0)) / 144.0


OBST_SPEC = {   # вид: (ширина хитбокса, высота хитбокса, вес выпадения, с какой сложности)
    "cone": (30, 56, 1.0, 0.00),
    "crate": (50, 52, 1.0, 0.00),
    "barrel": (38, 58, 0.9, 0.00),
    "rock": (70, 48, 0.8, 0.00),
    "tires": (56, 56, 0.8, 0.06),
    "spikes": (80, 28, 0.7, 0.10),
    "barrier": (98, 66, 0.6, 0.16),
    "crate2": (100, 52, 0.6, 0.22),
}
DEATH_TEXT = {
    "cone": "Конус оказался твёрже",
    "crate": "Ящик победил",
    "crate2": "Ящики победили",
    "barrel": "Бочка не уступила дорогу",
    "rock": "Камень не подвинулся",
    "tires": "Покрышки отпружинили",
    "spikes": "Ой, шипы!",
    "barrier": "Заграждение было серьёзным",
    "tire": "Сбит катящейся покрышкой",
    "landing": "Неудачное приземление",
    "ground": "Носом в землю",
}


class Obstacle:
    __slots__ = ("kind", "x", "gy", "w", "h", "ang", "vx", "rot", "r")

    def __init__(self, kind, x, gy, ang, w, h):
        self.kind, self.x, self.gy, self.ang, self.w, self.h = kind, x, gy, ang, w, h
        self.vx = 0.0
        self.rot = 0.0
        self.r = 27.0

    def hit(self, cx, cy, cr):
        if self.kind == "tire":
            dx, dy = cx - self.x, cy - (self.gy - self.r - 2.0)
            rr = cr + self.r
            return dx * dx + dy * dy < rr * rr
        x0, x1 = self.x - self.w * 0.5, self.x + self.w * 0.5
        y0, y1 = self.gy - self.h, self.gy + 4.0
        nx = x0 if cx < x0 else x1 if cx > x1 else cx
        ny = y0 if cy < y0 else y1 if cy > y1 else cy
        dx, dy = cx - nx, cy - ny
        return dx * dx + dy * dy < cr * cr


class Pill:
    __slots__ = ("x", "y", "ph", "var")

    def __init__(self, x, y, ph, var):
        self.x, self.y, self.ph, self.var = x, y, ph, var


def base_speed_at_x(x):
    d = max(0.0, x - START_X) / PX_PER_M
    return lerp(SPEED_MIN, SPEED_MAX, min(1.0, d / SPEED_RAMP_M))


class World:
    """Содержимое трассы: препятствия и таблетки создаются впереди игрока «порциями»."""

    def __init__(self, seed, demo=False):
        self.rng = random.Random(seed ^ 0x5F3759DF)
        self.terrain = Terrain(seed, demo)
        self.demo = demo
        self.obs = []
        self.pills = []
        self.next_x = 1500.0

    # --- порядок появления ---
    def spawn_upto(self, limit_x):
        if self.demo:
            return
        while self.next_x < limit_x:
            self._pattern()

    def _pattern(self):
        rng, T = self.rng, self.terrain
        x = self.next_x
        T.ensure(x + 2600)
        dif = clamp(x / PX_PER_M / 2500.0, 0.0, 1.0)
        r = rng.random()
        if r < 0.26:
            end = self._pill_row(x, False)
        elif r < 0.33 and dif > 0.04:
            end = self._pill_row(x, True)
        elif r < 0.72 or dif < 0.05:
            end = self._single(x, dif)
        elif r < 0.87:
            end = self._pair(x, dif)
        elif dif > 0.12:
            end = self._rolling(x)
        else:
            end = self._single(x, dif)
        gap = base_speed_at_x(x) * rng.uniform(0.95, 1.5 - 0.35 * dif) + 130.0
        self.next_x = end + gap

    def _find_flat(self, x, w):
        """Ищем ровное место (не вершину холма) для препятствия шириной w."""
        T = self.terrain
        best, best_score = None, 1e9
        for k in range(27):
            xc = x + k * 22.0
            sl = max(abs(T.slope(xc - w * 0.5)), abs(T.slope(xc)), abs(T.slope(xc + w * 0.5)))
            score = sl + max(0.0, T.curv(xc)) * 400.0 + k * 0.004
            if score < best_score:
                best, best_score = xc, score
        return best if best_score < 0.30 else None

    def _pick(self, dif, only=None):
        items = [(k, v[2]) for k, v in OBST_SPEC.items() if v[3] <= dif and (only is None or k in only)]
        tot = sum(w for _, w in items)
        r = self.rng.random() * tot
        for k, w in items:
            r -= w
            if r <= 0:
                return k
        return items[-1][0]

    def _place(self, kind, xc):
        T = self.terrain
        w, h = OBST_SPEC[kind][:2]
        o = Obstacle(kind, xc, T.h(xc), math.atan(T.slope(xc)), w, h)
        self.obs.append(o)
        return o

    def _arc(self, xc, n=7):
        """Дуга из таблеток над препятствием: собрать все можно только высоким прыжком."""
        T, rng = self.terrain, self.rng
        var = rng.randint(0, len(PILL_COLORS) - 1)
        for i in range(n):
            u = (i - (n - 1) / 2.0) / ((n - 1) / 2.0 + 0.3)
            px = xc + (i - (n - 1) / 2.0) * 46.0
            self.pills.append(Pill(px, T.h(px) - (108.0 + 150.0 * (1.0 - u * u)), rng.random() * 6.283, var))

    def _pill_row(self, x, high):
        T, rng = self.terrain, self.rng
        n = rng.randint(4, 6) if high else rng.randint(5, 9)
        var = rng.randint(0, len(PILL_COLORS) - 1)
        x0 = x + 60.0
        for i in range(n):
            px = x0 + i * 48.0
            hgt = (245.0 if high else 80.0 + 14.0 * math.sin(i * 0.7))
            self.pills.append(Pill(px, T.h(px) - hgt, rng.random() * 6.283, var))
        return x0 + n * 48.0

    def _single(self, x, dif):
        kind = self._pick(dif)
        w, h = OBST_SPEC[kind][:2]
        xc = self._find_flat(x + 60.0, w)
        if xc is None:
            return x + 260.0
        self._place(kind, xc)
        if self.rng.random() < 0.62:
            self._arc(xc)
        return xc + w * 0.5 + 120.0

    def _pair(self, x, dif):
        ka, kb = self._pick(dif, ("cone", "crate", "barrel")), self._pick(dif, ("cone", "crate", "barrel"))
        wa, wb = OBST_SPEC[ka][0], OBST_SPEC[kb][0]
        gap = self.rng.uniform(70.0, 110.0)
        total = wa + gap + wb
        xc = self._find_flat(x + 60.0, total)
        if xc is None:
            return x + 260.0
        self._place(ka, xc - total / 2.0 + wa / 2.0)
        self._place(kb, xc + total / 2.0 - wb / 2.0)
        self._arc(xc)
        return xc + total / 2.0 + 120.0

    def _rolling(self, x):
        T, rng = self.terrain, self.rng
        xt = x + 640.0
        o = Obstacle("tire", xt, T.h(xt), 0.0, 54, 54)
        o.vx = -rng.uniform(190.0, 260.0)
        self.obs.append(o)
        return x + 520.0

    # --- обновление ---
    def update(self, dt):
        T = self.terrain
        for o in self.obs:
            if o.vx:
                o.x += o.vx * dt
                o.gy = T.h(o.x)
                o.rot += o.vx * dt / o.r

    def prune(self, cam_x):
        self.obs = [o for o in self.obs if o.x > cam_x - 500.0]
        self.pills = [p for p in self.pills if p.x > cam_x - 200.0]


# =============================================================================
#  ИГРОК: физика скейтера
# =============================================================================
# круги столкновений в системе доски: (u — вдоль доски, v — вверх от точки касания земли, радиус)
HIT_CIRCLES = [(-35, 11, 9), (35, 11, 9), (-52, 33, 6), (52, 33, 6),          # колёса и носы доски
               (-30, 50, 11), (25, 50, 11), (-14, 80, 12), (22, 83, 12),      # кроссовки и колени
               (1, 114, 21), (9, 155, 24), (37, 150, 10)]                      # торс, голова, нос
COLLECT_CIRCLES = [(1, 114, 56), (0, 72, 46), (9, 155, 42)]                   # где рыба «ловит» таблетки


class Skater:
    def __init__(self, terrain, x=0.0):
        self.T = terrain
        self.x = x
        self.y = terrain.h(x)
        self.vx, self.vy = 260.0, 0.0
        self.grounded = True
        self.theta = math.atan(terrain.slope(x))
        self.body_ang = self.theta
        self.omega = 0.0
        self.jump_buf = self.coyote = self.jump_age = 0.0
        self.jumping = False
        self.since_land = 1.0
        self.squash = self.stretch = 0.0
        self.wheel = 0.0
        self.air_time = 0.0
        self.speed = 260.0
        self.events = []
        self.alive = True
        self.crash_reason = None

    def base_speed(self):
        return base_speed_at_x(self.x)

    def press_jump(self):
        self.jump_buf = JUMP_BUFFER

    def _start_jump(self, vx, vy):
        self.vx, self.vy = vx, -JUMP_SPEED + 0.5 * vy
        self.grounded = False
        self.jump_buf = self.coyote = 0.0
        self.jumping = True
        self.jump_age = 0.0
        self.stretch = 1.0
        self.air_time = 0.0
        self.events.append(("jump", 1.0))

    def step(self, dt, right=False, left=False, jump_held=False, cruise=None):
        T = self.T
        base = self.base_speed() if cruise is None else cruise
        target = base * (BOOST_K if right else BRAKE_K if left else 1.0)
        self.jump_buf = max(0.0, self.jump_buf - dt)
        self.coyote = max(0.0, self.coyote - dt)
        self.since_land += dt
        self.squash = max(0.0, self.squash - dt * 3.5)
        self.stretch = max(0.0, self.stretch - dt * 4.0)

        if self.grounded:
            a = math.atan(T.slope(self.x))
            ca, sa = math.cos(a), math.sin(a)
            s = self.vx * ca + self.vy * sa
            s += (SLOPE_G * sa - RELAX * (s - target)) * dt        # склон разгоняет/тормозит, «круиз» подтягивает
            smin = MIN_SPEED if left else max(MIN_SPEED, SPEED_FLOOR * base)   # на подъёме рыба «дожимает» ногой
            if s < smin:
                s = smin
            self.speed = s
            self.coyote = COYOTE
            if self.jump_buf > 0.0:
                self._start_jump(s * ca, s * sa)
            else:
                vx, vy = s * ca, s * sa
                nx = self.x + vx * dt
                gny = T.h(nx)
                ball = self.y + vy * dt + 0.5 * GRAVITY * dt * dt   # где была бы рыба в свободном полёте
                if self.since_land > 0.05 and ball < gny - TAKEOFF_EPS:
                    self.grounded = False                          # земля «ушла вниз» быстрее падения — взлёт
                    self.x, self.y = nx, ball
                    self.vx, self.vy = vx, vy + GRAVITY * dt
                    self.air_time = 0.0
                    self.jumping = False
                    self.events.append(("takeoff", 0.0))
                else:
                    self.x, self.y = nx, gny
                    a2 = math.atan(T.slope(nx))
                    self.vx, self.vy = s * math.cos(a2), s * math.sin(a2)
                    chord = math.atan2(T.h(nx + WHEEL_DX) - T.h(nx - WHEEL_DX), 2.0 * WHEEL_DX)
                    self.theta += wrap_pi(chord - self.theta) * min(1.0, 18.0 * dt)
                    self.wheel += s / float(WHEEL_R) * dt
                    self.body_ang += wrap_pi(self.theta - self.body_ang) * min(1.0, 9.0 * dt)
                    return
        self._air(dt, right, left, jump_held)

    def _air(self, dt, right, left, jump_held):
        T = self.T
        if self.jump_buf > 0.0 and self.coyote > 0.0 and not self.jumping:      # прыжок «в последний момент»
            self._start_jump(self.vx, min(0.0, self.vy))
        self.jump_age += dt
        if self.jumping and not jump_held and self.jump_age > 0.06 and self.vy < -260.0:
            self.vy *= 0.45                                                      # отпустил — прыжок ниже
            self.jumping = False
        self.vy += GRAVITY * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.air_time += dt
        ahead = self.x + self.vx * 0.22
        target = math.atan(T.slope(ahead)) * 0.85 + math.atan2(self.vy, max(self.vx, 1.0)) * 0.15
        tilt = (1.0 if right else 0.0) - (1.0 if left else 0.0)
        self.omega += (AIR_K * wrap_pi(target - self.theta) - AIR_D * self.omega + tilt * TILT_ACC) * dt
        self.omega = clamp(self.omega, -8.0, 8.0)
        self.theta += self.omega * dt
        self.body_ang += wrap_pi(self.theta - self.body_ang) * min(1.0, 9.0 * dt)
        self.wheel += self.vx / float(WHEEL_R) * dt * 0.35
        gy = T.h(self.x)
        if self.y >= gy:                                                         # приземление
            a = math.atan(T.slope(self.x))
            ca, sa = math.cos(a), math.sin(a)
            if abs(wrap_pi(self.theta - a)) > CRASH_ANGLE:
                self.alive = False
                self.crash_reason = "landing"
                self.events.append(("crash", 0.0))
                return
            vin = self.vy * ca - self.vx * sa                                    # скорость «в землю»
            s = max(self.vx * ca + self.vy * sa, MIN_SPEED)
            self.y = gy
            self.vx, self.vy = s * ca, s * sa
            self.grounded = True
            self.jumping = False
            self.omega = 0.0
            self.since_land = 0.0
            self.squash = clamp(vin / 900.0, 0.2, 1.0)
            self.events.append(("land", vin))

    def circles(self, table):
        ca, sa = math.cos(self.theta), math.sin(self.theta)
        return [(self.x + ca * u + sa * v, self.y + sa * u - ca * v, r) for u, v, r in table]


# =============================================================================
#  ЧАСТИЦЫ, ОБЛОМКИ, ВСПЛЫВАЮЩИЙ ТЕКСТ
# =============================================================================
class Particles:
    """Пыль из-под колёс, искры от таблеток, щепки и звёздочки."""

    def __init__(self):
        self.p = []
        self.puffs = []
        for r in (7, 11, 16, 22, 30):
            cv = Canvas(r * 2 + 4, r * 2 + 4, 2, (r + 2, r + 2), 1.0)
            for i in range(10):
                t = i / 9.0
                pygame.draw.circle(cv.s, (236, 226, 204, int(230 * t * t)), cv.P((0, 0)), cv.R(r * (1 - 0.9 * t)))
            self.puffs.append(cv.finish())

    def dust(self, x, y, n, vx=0.0, power=1.0):
        for _ in range(n):
            life = random.uniform(0.35, 0.7)
            self.p.append([x + random.uniform(-10, 10), y - 2, vx * 0.15 + random.uniform(-70, 70) * power,
                           -random.uniform(20, 110) * power, life, life, random.randint(0, 3), 0, None])

    def burst(self, x, y, n, col, speed=260.0):
        for _ in range(n):
            a = random.uniform(0, 6.283)
            sp = random.uniform(0.3, 1.0) * speed
            life = random.uniform(0.35, 0.7)
            self.p.append([x, y, math.cos(a) * sp, math.sin(a) * sp - 40, life, life, random.uniform(3, 6), 1, col])

    def chips(self, x, y, n, col):
        for _ in range(n):
            life = random.uniform(0.6, 1.1)
            self.p.append([x + random.uniform(-14, 14), y + random.uniform(-14, 14), random.uniform(-260, 380),
                           -random.uniform(160, 560), life, life, random.uniform(3, 7), 2, col])

    def update(self, dt):
        alive = []
        for q in self.p:
            q[4] -= dt
            if q[4] <= 0:
                continue
            q[0] += q[2] * dt
            q[1] += q[3] * dt
            if q[7] == 0:
                q[3] -= 60 * dt
                q[2] *= (1 - 1.5 * dt)
            else:
                q[3] += 900 * dt
            alive.append(q)
        self.p = alive[-260:]

    def draw(self, surf, cx, cy):
        for q in self.p:
            x, y = q[0] - cx, q[1] - cy
            if x < -40 or x > W + 40 or y < -40 or y > H + 40:
                continue
            t = q[4] / q[5]                                   # 1 -> 0 по мере затухания
            if q[7] == 0:
                img = self.puffs[min(len(self.puffs) - 1, q[6] + int((1 - t) * 2))]
                img.set_alpha(int(190 * t))
                surf.blit(img, (int(x - img.get_width() / 2), int(y - img.get_height() / 2)))
            elif q[7] == 1:
                r = q[6] * (0.4 + 0.6 * t)
                pygame.draw.polygon(surf, q[8], [(x, y - r * 1.6), (x + r * 0.5, y - r * 0.5), (x + r * 1.6, y),
                                                 (x + r * 0.5, y + r * 0.5), (x, y + r * 1.6), (x - r * 0.5, y + r * 0.5),
                                                 (x - r * 1.6, y), (x - r * 0.5, y - r * 0.5)])
            else:
                r = q[6]
                pygame.draw.rect(surf, INK, (int(x - r - 1), int(y - r - 1), int(r * 2 + 2), int(r * 2 + 2)))
                pygame.draw.rect(surf, q[8], (int(x - r), int(y - r), int(r * 2), int(r * 2)))


class Debris:
    """Кусок разбившегося скейтера (рыба, доска, шапка) — отскакивает от холмов."""

    def __init__(self, img, pivot, x, y, vx, vy, ang, av, r, bounce=0.42):
        self.img, self.pivot = img, pivot
        self.x, self.y, self.vx, self.vy, self.ang, self.av = x, y, vx, vy, ang, av
        self.r, self.bounce = r, bounce
        self.rest = False

    def update(self, dt, T):
        if self.rest:
            return
        self.vy += GRAVITY * dt
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.ang += self.av * dt
        gy = T.h(self.x)
        if self.y + self.r > gy:
            a = math.atan(T.slope(self.x))
            self.y = gy - self.r
            if self.vy > 0:
                self.vy = -self.vy * self.bounce
                if abs(self.vy) < 110:
                    self.vy = 0.0
            self.vx = self.vx * (1 - 2.2 * dt) + SLOPE_G * math.sin(a) * dt
            self.av *= (1 - 2.5 * dt)
            if abs(self.vy) < 1 and abs(self.vx) < 18 and abs(self.av) < 0.4:
                self.rest = True

    def draw(self, surf, cx, cy):
        blit_rot(surf, self.img, (self.x - cx, self.y - cy), self.pivot, self.ang)



# =============================================================================
#  РЕНДЕР: камера, фон, земля, декор, персонаж, интерфейс
#  (функции здесь без состояния — всё состояние хранит App в 07_app.py)
# =============================================================================
GRASS_HI = (168, 224, 96)
GRASS = (104, 182, 58)
GRASS_D = (62, 140, 42)
DIRT_HI = (146, 100, 56)
DIRT = (110, 74, 42)
DIRT_D = (76, 50, 30)
DIRT_DEEP = (48, 33, 22)
GROUND_STEP = 5


def rot_pt(p, ang):
    ca, sa = math.cos(ang), math.sin(ang)
    return (p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca)


class Camera:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.shake = 0.0
        self.init = False

    def snap(self, tx, ty):
        self.x, self.y = tx - W * 0.34, ty - H * 0.66
        self.init = True

    def update(self, dt, tx, ty, lookahead=0.0):
        want_x = tx - W * 0.34 + lookahead
        want_y = clamp(ty - H * 0.66, -900.0, 900.0)
        if not self.init:
            self.snap(tx, ty)
        k = min(1.0, 7.0 * dt)
        ky = min(1.0, 5.0 * dt)
        self.x += (want_x - self.x) * k
        self.y += (want_y - self.y) * ky
        self.shake = max(0.0, self.shake - dt * 2.6)

    def kick(self, amt):
        self.shake = min(1.0, self.shake + amt)

    def offset(self):
        if self.shake > 0.003:
            s = self.shake * self.shake
            return (self.x + random.uniform(-1, 1) * 14 * s,
                    self.y + random.uniform(-1, 1) * 14 * s)
        return self.x, self.y


# --- фон: небо, облака, дальние холмы ----------------------------------------
class CloudField:
    def __init__(self, assets, n=8, seed=99):
        rnd = random.Random(seed)
        self.items = []
        for _ in range(n):
            idx = rnd.randrange(len(assets.clouds))
            sc = rnd.uniform(0.55, 1.05)
            img = assets.clouds[idx]
            if abs(sc - 1.0) > 0.02:
                img = pygame.transform.smoothscale(img, (max(1, int(img.get_width() * sc)),
                                                          max(1, int(img.get_height() * sc))))
            img = img.copy()
            img.set_alpha(rnd.randint(200, 255))
            self.items.append([rnd.uniform(0, W + 600), rnd.uniform(20, 250), img, rnd.uniform(5.0, 16.0)])

    def update(self, dt):
        for it in self.items:
            it[0] += it[3] * dt

    def draw(self, surf, camx):
        par = 0.045
        span = W + 620
        for x, y, img, spd in self.items:
            sx = (x - camx * par) % span - 310
            surf.blit(img, (int(sx), int(y)))


_haze = None


def haze_overlay():
    global _haze
    if _haze is None:
        s = pygame.Surface((W, 260), pygame.SRCALPHA)
        for y in range(260):
            a = int(70 * (1.0 - y / 260.0) ** 1.6)
            pygame.draw.line(s, (214, 232, 246, a), (0, y), (W, y))
        _haze = s
    return _haze


_topshade = None


def top_shade():
    global _topshade
    if _topshade is None:
        s = pygame.Surface((W, 120), pygame.SRCALPHA)
        for y in range(120):
            a = int(120 * (1.0 - y / 120.0))
            pygame.draw.line(s, (10, 10, 18, a), (0, y), (W, y))
        _topshade = s
    return _topshade


def draw_background(surf, assets, clouds, camx, camy):
    surf.blit(assets.sky, (0, 0))
    clouds.draw(surf, camx)
    horizon = min(L.y0 for L in assets.layers) - camy * assets.layers[0].fy
    if horizon > 0:
        surf.blit(haze_overlay(), (0, max(0, int(horizon) - 40)))
    for L in assets.layers:
        y = int(L.y0 - camy * L.fy)
        surf.fill(L.bottom, (0, min(H, y + L.img.get_height()), W, max(0, H - (y + L.img.get_height()))))
        off = -(camx * L.fx) % TILE
        x = off - TILE
        while x < W:
            surf.blit(L.img, (int(x), y))
            x += TILE


# --- земля --------------------------------------------------------------------
def _tuft(surf, x, y, ang, h, col):
    ca, sa = math.cos(ang), math.sin(ang)
    tip = (x - sa * h, y - ca * h)
    pygame.draw.polygon(surf, col, [(x - 3, y), (x + 3, y), tip])


def draw_ground(surf, terrain, camx, camy):
    x0w, x1w = camx - 20, camx + W + 20
    pts = []
    for sx in range(-GROUND_STEP, W + GROUND_STEP * 2, GROUND_STEP):
        wx = camx + sx
        gy = terrain.h(wx) - camy
        pts.append((sx, wx, gy))
    poly = [(sx, gy) for sx, wx, gy in pts] + [(pts[-1][0], H + 4), (pts[0][0], H + 4)]
    pygame.draw.polygon(surf, DIRT_D, poly)
    for sx, wx, gy in pts:
        if gy > H + 2 or gy < -140:
            continue
        n = int(hash01(int(wx) // GROUND_STEP) * 5) - 2
        lw = GROUND_STEP + 2
        pygame.draw.line(surf, lerp_col(GRASS_D, GRASS_HI, 0.5 + 0.06 * n), (sx, gy), (sx, gy + 3), lw)
        pygame.draw.line(surf, GRASS, (sx, gy + 3), (sx, gy + 13), lw)
        pygame.draw.line(surf, lerp_col(DIRT_HI, DIRT, 0.5 + 0.08 * n), (sx, gy + 13), (sx, gy + 26), lw)
        pygame.draw.line(surf, DIRT, (sx, gy + 26), (sx, min(H, gy + 120)), lw)
        if gy + 120 < H:
            pygame.draw.line(surf, DIRT_D, (sx, gy + 120), (sx, H), lw)
    lo = bisect.bisect_left(terrain.pebbles, (x0w,))
    hi = bisect.bisect_right(terrain.pebbles, (x1w, 1e18, 1e18, 1e18))
    for wx, depth, size, shade in terrain.pebbles[lo:hi]:
        gy = terrain.h(wx) - camy
        y = gy + 18 + depth * 0.30
        if 0.0 <= y <= H and depth < 340:
            col = lerp_col(DIRT_D, DIRT_HI, shade)
            pygame.draw.ellipse(surf, col, (int(wx - camx - size / 2), int(y - size / 3), int(size), int(size * 0.66)))
    rnd_edge = 0
    for i in range(0, len(pts) - 1, 2):
        sx, wx, gy = pts[i]
        if gy > H or gy < -60:
            continue
        h01 = hash01(int(wx) // (GROUND_STEP * 2))
        if h01 < 0.22:
            ang = math.atan(terrain.slope(wx))
            _tuft(surf, sx, gy, ang, 7 + h01 * 30, GRASS_HI if h01 < 0.11 else GRASS)


def draw_decor(surf, terrain, assets, camx, camy):
    x0w, x1w = camx - 260, camx + W + 260
    lo = bisect.bisect_left(terrain.decor, (x0w,))
    hi = bisect.bisect_right(terrain.decor, (x1w, "\uffff", 9e18, 2))
    for wx, kind, scale, flip in terrain.decor[lo:hi]:
        img, pv = assets.decor[kind]
        gy = terrain.h(wx)
        sink = 9.0 if kind not in ("wall", "sign") else 3.0
        w, h = img.get_width() * scale, img.get_height() * scale
        im = img
        if flip and kind == "wall":
            flip = False                                     # на стене надпись — не отражаем текст
        if flip:
            im = pygame.transform.flip(im, True, False)
            pvx = w - pv[0] * scale
        else:
            pvx = pv[0] * scale
        if abs(scale - 1.0) > 0.02:
            im = pygame.transform.smoothscale(im, (max(1, int(w)), max(1, int(h))))
        surf.blit(im, (wx - camx - pvx, gy - camy + sink - pv[1] * scale))


def draw_obstacles(surf, world, assets, camx, camy):
    for o in world.obs:
        sx = o.x - camx
        if -140 < sx < W + 140:
            img, off = assets.obstacle_img(o.kind, o.ang if o.kind != "tire" else o.rot)
            surf.blit(img, (sx - off[0], o.gy - camy - off[1]))


def draw_pills(surf, world, assets, camx, camy, t):
    for p in world.pills:
        sx = p.x - camx
        if -60 < sx < W + 60:
            bob = math.sin(t * 3.1 + p.ph) * 7.0
            spin = int(((t * 1.6 + p.ph) * 24)) % 24
            img = assets.pills[p.var][spin]
            gimg = assets.glow
            gs = 46
            gimg2 = pygame.transform.smoothscale(gimg, (gs, gs)) if gimg.get_width() != gs else gimg
            surf.blit(gimg2, (sx - gs / 2, p.y - camy + bob - gs / 2))
            surf.blit(img, (sx - img.get_width() / 2, p.y - camy + bob - img.get_height() / 2))


def draw_skater(surf, assets, sk, camx, camy, t):
    gy = sk.T.h(sk.x)
    height = max(0.0, gy - sk.y)
    shsc = clamp(1.0 - height / 320.0, 0.22, 1.0)
    sh = assets.shadow
    sw, shh = sh.get_width() * shsc, sh.get_height() * shsc
    img = pygame.transform.smoothscale(sh, (max(1, int(sw)), max(1, int(shh))))
    img.set_alpha(int(150 * shsc))
    surf.blit(img, (sk.x - camx - sw / 2, gy - camy - shh * 0.45))

    sx, sy = sk.x - camx, sk.y - camy
    scale = 1.0 + 0.11 * sk.squash - 0.07 * sk.stretch
    b_img, b_pv = assets.board
    blit_rot(surf, b_img, (sx, sy), b_pv, sk.theta, scale)
    w_img, w_pv = assets.wheel
    for dxw in (-WHEEL_DX, WHEEL_DX):
        off = rot_pt((dxw, -WHEEL_R), sk.theta)
        blit_rot(surf, w_img, (sx + off[0] * scale, sy + off[1] * scale), w_pv, sk.theta + sk.wheel, scale)
    bob = math.sin(sk.wheel * 2.0) * 1.3 if sk.grounded else 0.0
    deck = rot_pt((0.0, -(DECK_TOP + bob)), sk.theta)
    pose = "dead" if not sk.alive else ("air" if not sk.grounded else "ride")
    c_img, c_pv = assets.char[pose]
    blit_rot(surf, c_img, (sx + deck[0] * scale, sy + deck[1] * scale), c_pv, sk.body_ang, scale)


# =============================================================================
#  ЧАСТИЦЫ, ВСПЛЫВАЮЩИЙ ТЕКСТ
# =============================================================================
class FloatingText:
    __slots__ = ("x", "y", "text", "life", "life0", "size", "color", "vy")

    def __init__(self, x, y, text, color=GOLD, size=30, life=0.9, vy=-92.0):
        self.x, self.y, self.text = x, y, text
        self.life = self.life0 = life
        self.size, self.color, self.vy = size, color, vy

    def update(self, dt):
        self.life -= dt
        self.y += self.vy * dt
        self.vy *= (1 - 1.6 * dt)
        return self.life > 0

    def draw(self, surf, camx, camy):
        t = clamp(self.life / self.life0, 0.0, 1.0)
        a = int(255 * smoothstep(t * 3.0 if t < 0.34 else 1.0))
        sc = lerp(1.28, 1.0, smoothstep(1 - (1 - t) * 5.0)) if t > 0.8 else 1.0
        s = render_text(self.text, int(self.size * sc), self.color, INK, 3)
        s = s.copy()
        s.set_alpha(a)
        surf.blit(s, s.get_rect(center=(self.x - camx, self.y - camy)))


# =============================================================================
#  UI: кнопки, панели
# =============================================================================
class Button:
    def __init__(self, rect, label, sub=None, accent=GOLD, size=32, kind="head"):
        self.rect = pygame.Rect(rect)
        self.label, self.sub, self.accent, self.size, self.kind = label, sub, accent, size, kind
        self.hover = 0.0
        self.enabled = True

    def update(self, dt, mouse):
        want = 1.0 if (self.enabled and self.rect.collidepoint(mouse)) else 0.0
        self.hover += (want - self.hover) * min(1.0, dt * 13.0)

    def hit(self, pos):
        return self.enabled and self.rect.collidepoint(pos)

    def draw(self, surf):
        infl = int(7 * self.hover)
        r = self.rect.inflate(infl, infl)
        press = 0
        draw_rrect(surf, (16, 12, 18), r.move(0, 7 - press), r.height // 2)
        top = lerp_col(self.accent, WHITE, 0.30 + 0.20 * self.hover)
        bot = lerp_col(self.accent, (10, 6, 6), 0.32)
        if not self.enabled:
            top, bot = (150, 150, 156), (90, 90, 96)
        grad = pygame.Surface(r.size)
        rh = max(1, r.height)
        for i in range(r.height):
            pygame.draw.line(grad, lerp_col(top, bot, i / float(rh - 1) if rh > 1 else 0), (0, i), (r.width, i))
        mask = pygame.Surface(r.size, pygame.SRCALPHA)
        draw_rrect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, r.width, r.height), r.height // 2)
        grad = grad.convert_alpha()
        grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        surf.blit(grad, r.topleft)
        pygame.draw.rect(surf, INK, r, width=4, border_radius=r.height // 2)
        hl = pygame.Surface((r.width - 14, max(2, int(r.height * 0.32))), pygame.SRCALPHA)
        hl.fill((255, 255, 255, 60))
        surf.blit(hl, (r.x + 7, r.y + 5))
        blit_text(surf, self.label, self.size, r.center, "center", WHITE, INK, 3)
        if self.sub:
            blit_text(surf, self.sub, int(self.size * 0.52), (r.centerx, r.bottom + 15), "center", WHITE, INK, 2, kind="body")


class IconButton:
    def __init__(self, rect, icon, accent=(255, 255, 255)):
        self.rect = pygame.Rect(rect)
        self.icon, self.accent = icon, accent
        self.hover = 0.0

    def update(self, dt, mouse):
        want = 1.0 if self.rect.collidepoint(mouse) else 0.0
        self.hover += (want - self.hover) * min(1.0, dt * 13.0)

    def hit(self, pos):
        return self.rect.collidepoint(pos)

    def draw(self, surf, active=True):
        r = self.rect
        draw_rrect(surf, (18, 16, 22, 170) if False else (24, 20, 28), r, 14)
        col = lerp_col((40, 36, 46), (60, 54, 66), self.hover)
        draw_rrect(surf, col, r.inflate(-4, -4), 12)
        pygame.draw.rect(surf, INK, r, width=3, border_radius=14)
        cx, cy = r.center
        ic = self.icon
        c = WHITE if active else (150, 150, 158)
        if ic == "pause":
            for dx in (-7, 3):
                pygame.draw.rect(surf, c, (cx + dx, cy - 11, 6, 22), border_radius=2)
        elif ic == "play":
            pygame.draw.polygon(surf, c, [(cx - 8, cy - 12), (cx - 8, cy + 12), (cx + 12, cy)])
        elif ic == "restart":
            pygame.draw.arc(surf, c, (cx - 11, cy - 11, 22, 22), 0.7, 5.6, 4)
            pygame.draw.polygon(surf, c, [(cx + 8, cy - 12), (cx + 15, cy - 4), (cx + 3, cy - 3)])
        elif ic == "home":
            pygame.draw.polygon(surf, c, [(cx, cy - 12), (cx + 12, cy - 1), (cx + 12, cy + 11), (cx - 12, cy + 11), (cx - 12, cy - 1)])
            pygame.draw.rect(surf, col, (cx - 4, cy + 1, 8, 10))
        elif ic in ("mute_on", "mute_off"):
            pygame.draw.polygon(surf, c, [(cx - 12, cy - 5), (cx - 5, cy - 5), (cx + 4, cy - 13), (cx + 4, cy + 13), (cx - 5, cy + 5), (cx - 12, cy + 5)])
            if ic == "mute_on":
                pygame.draw.arc(surf, c, (cx + 2, cy - 10, 16, 20), -0.9, 0.9, 3)
                pygame.draw.arc(surf, c, (cx + 4, cy - 15, 24, 30), -0.8, 0.8, 3)
            else:
                pygame.draw.line(surf, RED, (cx + 6, cy - 10), (cx + 17, cy + 10), 4)
                pygame.draw.line(surf, RED, (cx + 17, cy - 10), (cx + 6, cy + 10), 4)


def panel(surf, rect, alpha=210, radius=28, col=(20, 16, 26)):
    s = pygame.Surface(rect.size, pygame.SRCALPHA)
    draw_rrect(s, (col[0], col[1], col[2], alpha), pygame.Rect(0, 0, rect.width, rect.height), radius)
    pygame.draw.rect(s, (255, 255, 255, 30), (0, 0, rect.width, rect.height), width=1, border_radius=radius)
    surf.blit(s, rect.topleft)
    pygame.draw.rect(surf, INK, rect, width=4, border_radius=radius)


def dim_screen(surf, alpha=140):
    s = pygame.Surface((W, H), pygame.SRCALPHA)
    s.fill((8, 6, 14, alpha))
    surf.blit(s, (0, 0))


def draw_pedal(surf, rect, kind, pressed, alpha=150):
    """Экранная педаль газа/тормоза для сенсорного управления (kind: 'boost' | 'brake')."""
    r = pygame.Rect(rect)
    cx, cy, rad = r.centerx, r.centery, r.width // 2
    base = ORANGE if kind == "boost" else (110, 150, 220)
    a = min(255, alpha + (85 if pressed else 0))
    s = pygame.Surface((r.width, r.height), pygame.SRCALPHA)
    col = lerp_col(base, WHITE, 0.28) if pressed else base
    pygame.draw.circle(s, (col[0], col[1], col[2], a), (rad, rad), rad)
    pygame.draw.circle(s, (255, 255, 255, 70 if pressed else 30), (rad, rad), rad, width=5)
    surf.blit(s, r.topleft)
    n = 3
    for i in range(n):
        t = (i - (n - 1) / 2.0) * 17
        if kind == "boost":
            pts = [(cx - 9 + t, cy - 16), (cx + 13 + t, cy), (cx - 9 + t, cy + 16)]
        else:
            pts = [(cx + 9 - t, cy - 16), (cx - 13 - t, cy), (cx + 9 - t, cy + 16)]
        pygame.draw.polygon(surf, WHITE, pts)


def badge(surf, pos, icon_img, text, accent=GOLD, anchor="topleft", w=None):
    tw = render_text(text, 34, WHITE, INK, 3)
    pad = 14
    iw = icon_img.get_width() if icon_img else 0
    bw = w or (iw + tw.get_width() + pad * 2 + (8 if icon_img else 0))
    bh = 52
    r = pygame.Rect(0, 0, bw, bh)
    setattr(r, anchor, pos)
    s = pygame.Surface((bw, bh), pygame.SRCALPHA)
    draw_rrect(s, (18, 14, 22, 195), pygame.Rect(0, 0, bw, bh), bh // 2)
    surf.blit(s, r.topleft)
    pygame.draw.rect(surf, INK, r, width=3, border_radius=bh // 2)
    x = r.x + pad
    if icon_img:
        surf.blit(icon_img, (x, r.centery - icon_img.get_height() // 2))
        x += iw + 8
    surf.blit(tw, (x, r.centery - tw.get_height() // 2))
    return r



# =============================================================================
#  СОХРАНЕНИЕ РЕКОРДОВ
# =============================================================================
try:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BASE_DIR = os.getcwd()
SAVE_PATH = os.path.join(_BASE_DIR, SAVE_NAME)


def load_save():
    try:
        with open(SAVE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {"best_dist": float(d.get("best_dist", 0.0)), "best_pills": int(d.get("best_pills", 0)),
                "muted": bool(d.get("muted", False))}
    except Exception:
        return {"best_dist": 0.0, "best_pills": 0, "muted": False}


def save_save(d):
    try:
        with open(SAVE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


def fmt_num(n):
    return "{:,}".format(int(n)).replace(",", " ")


COMBO_WINDOW = 0.55
DUST_DT = 0.045
MENU_CRUISE = 400.0


# =============================================================================
#  ПРИЛОЖЕНИЕ
# =============================================================================
class App:
    def __init__(self):
        pygame.mixer.pre_init(SR, -16, 1, 512)
        pygame.init()
        try:
            pygame.event.set_blocked(None)
            pygame.event.set_allowed([QUIT, KEYDOWN, KEYUP, MOUSEMOTION, MOUSEBUTTONDOWN, MOUSEBUTTONUP,
                                      FINGERDOWN, FINGERUP, WINDOWFOCUSLOST, _APP_BG, _APP_FG])
        except Exception:
            pass
        self.fullscreen = IS_ANDROID
        self._set_video_mode()
        pygame.display.set_caption(TITLE)
        try:
            pygame.key.set_repeat()
        except Exception:
            pass
        try:
            pygame.mouse.set_visible(not IS_ANDROID)
        except Exception:
            pass

        self.touch_mode = TOUCH_DEFAULT
        self.touches = {}           # finger_id -> "jump" / "boost" / "brake" / "ui"
        self.touch_hint = 0.0

        self._loading_screen(0.0, "Запуск...")
        self.assets = Assets(lambda f: self._loading_screen(f * 0.55, "Рисуем рыбу..."))
        self.audio = Audio(lambda f: self._loading_screen(0.55 + f * 0.45, "Настраиваем музыку..."))

        self.save = load_save()
        self.audio.enabled = not self.save.get("muted", False)

        self.clock = pygame.time.Clock()
        self.acc = 0.0
        self.t = 0.0
        self.state = "menu"
        self.next_state = None
        self.mouse = (W // 2, H // 2)
        self.mouse_down = False
        self.mouse_click = False

        self.clouds = CloudField(self.assets)
        self._make_menu_world()

        self.buttons = {}
        self._make_buttons()

        self.world = None
        self.sk = None
        self.cam = Camera()
        self.particles = Particles()
        self.debris = []
        self.floats = []
        self.run_pills = 0
        self.combo = 0
        self.combo_timer = 0.0
        self.dust_timer = 0.0
        self.dead_timer = 0.0
        self.hitstop = 0.0
        self.record_live = False
        self.saved_this_run = True
        self.crash_reason = None
        self.paused_from = "play"
        self.flash = 0.0
        self.seed_ctr = random.randint(1, 999999)

    # --- инициализация окна/экрана загрузки ---
    def _set_video_mode(self):
        fs = self.fullscreen or IS_ANDROID
        flags = pygame.SCALED | (pygame.FULLSCREEN if fs else pygame.RESIZABLE)
        try:
            self.screen = pygame.display.set_mode((W, H), flags)
        except Exception:
            try:
                self.screen = pygame.display.set_mode((W, H))
            except Exception:
                self.screen = pygame.display.set_mode((W, H), 0)

    def toggle_fullscreen(self):
        if IS_ANDROID:
            return
        self.fullscreen = not self.fullscreen
        self._set_video_mode()

    def _loading_screen(self, frac, label):
        s = self.screen
        s.fill((92, 168, 232))
        blit_text(s, "БЛОБ-СКЕЙТ", 64, (W // 2, H // 2 - 90), "center", GOLD, INK, 5)
        bw, bh = 460, 26
        bx, by = W // 2 - bw // 2, H // 2
        draw_rrect(s, INK, (bx - 4, by - 4, bw + 8, bh + 8), 14)
        draw_rrect(s, (30, 26, 34), (bx, by, bw, bh), 12)
        draw_rrect(s, GOLD, (bx, by, max(10, int(bw * clamp(frac, 0.0, 1.0))), bh), 12)
        blit_text(s, label, 22, (W // 2, by + bh + 30), "center", WHITE, INK, 2, kind="body")
        pygame.display.flip()
        for e in pygame.event.get():
            if e.type == QUIT:
                sys.exit(0)

    def _make_menu_world(self):
        self.mw = World(random.randint(1, 999999), demo=True)
        self.msk = Skater(self.mw.terrain, x=0.0)
        self.mcam = Camera()
        self.mcam.snap(self.msk.x, self.msk.y)

    def _make_buttons(self):
        cw, ch = 300, 84
        play_sub = "нажмите на экран" if self.touch_mode else "космическая / стрелка вверх"
        self.buttons["play"] = Button((W // 2 - cw // 2, 452, cw, ch), "ИГРАТЬ", play_sub, GOLD, 40)
        self.buttons["mute_menu"] = IconButton((W - 84, 24, 60, 60), "mute_off")

        self.buttons["pause"] = IconButton((W - 84, 24, 60, 60), "pause")
        self.buttons["mute_play"] = IconButton((W - 156, 24, 60, 60), "mute_off")

        pw, ph = 260, 76
        cx = W // 2
        self.buttons["resume"] = Button((cx - pw // 2, 300, pw, ph), "ПРОДОЛЖИТЬ", None, GREEN, 30)
        self.buttons["restart_p"] = Button((cx - pw // 2, 392, pw, ph), "РЕСТАРТ", None, GOLD, 30)
        self.buttons["menu_p"] = Button((cx - pw // 2, 484, pw, ph), "В МЕНЮ", None, (150, 150, 160), 30)
        self.buttons["mute_pause"] = IconButton((cx + pw // 2 - 60, 576, 60, 60), "mute_off")

        rw, rh = 300, 84
        restart_sub = None if self.touch_mode else "R / пробел"
        self.buttons["restart_d"] = Button((cx - rw // 2, 470, rw, rh), "ЕЩЁ РАЗ", restart_sub, GOLD, 38)
        self.buttons["menu_d"] = Button((cx - rw // 2, 570, rw, rh), "В МЕНЮ", None, (150, 150, 160), 28)

        ps = 126
        self.brake_rect = pygame.Rect(22, H - 22 - ps, ps, ps)
        self.boost_rect = pygame.Rect(W - 22 - ps, H - 22 - ps, ps, ps)

    # --- управление музыкой при смене состояний ---
    def _enter_state(self, st):
        self.state = st
        self.touches.clear()
        if st == "menu":
            self.audio.play_music("menu")
        elif st == "play":
            self.audio.play_music("game")
            if self.audio.ok:
                self.audio.music_ch.set_volume(MUSIC_VOL)
        elif st == "pause":
            if self.audio.ok:
                self.audio.music_ch.set_volume(MUSIC_VOL * 0.35)
        elif st == "dead":
            if self.audio.ok:
                self.audio.music_ch.set_volume(MUSIC_VOL * 0.6)

    # --- новая игра ---
    def new_game(self):
        self.seed_ctr = (self.seed_ctr * 1103515245 + 12345) & 0x7FFFFFFF
        self.world = World(self.seed_ctr)
        self.sk = Skater(self.world.terrain, x=0.0)
        self.cam = Camera()
        self.cam.snap(self.sk.x, self.sk.y)
        self.particles = Particles()
        self.debris = []
        self.floats = []
        self.run_pills = 0
        self.combo = 0
        self.combo_timer = 0.0
        self.dust_timer = 0.0
        self.dead_timer = 0.0
        self.hitstop = 0.0
        self.record_live = False
        self.saved_this_run = False
        self.crash_reason = None
        self.flash = 0.0
        self.touch_hint = 2.6 if self.touch_mode else 0.0
        self._enter_state("play")
        self.audio.sfx("start")

    def to_menu(self):
        self._enter_state("menu")

    def toggle_mute(self):
        self.audio.toggle()
        self.save["muted"] = not self.audio.enabled
        save_save(self.save)

    # --- касания ---
    def _touch_pos(self, e):
        return (clamp(e.x, 0.0, 1.0) * W, clamp(e.y, 0.0, 1.0) * H)

    def _touch_role(self, pos):
        if self.state == "play":
            if self.buttons["pause"].hit(pos) or self.buttons["mute_play"].hit(pos):
                return "ui"
            if self.brake_rect.collidepoint(pos):
                return "brake"
            if self.boost_rect.collidepoint(pos):
                return "boost"
            return "jump"
        return "ui"

    # --- события ---
    def handle_event(self, e):
        if e.type == QUIT:
            self.quit()
        elif e.type == KEYDOWN:
            self._key(e.key)
        elif e.type == FINGERDOWN:
            self.touch_mode = True
            pos = self._touch_pos(e)
            self.mouse = pos
            role = self._touch_role(pos)
            self.touches[e.finger_id] = role
            if role == "jump" and self.sk is not None:
                self.sk.press_jump()
                self.touch_hint = 0.0
        elif e.type == FINGERUP:
            pos = self._touch_pos(e)
            role = self.touches.pop(e.finger_id, None)
            if role == "ui":
                self._click(pos)
        elif self.touch_mode:
            pass  # на телефоне мышь не используем — её события (если синтезированы) игнорируем
        elif e.type == MOUSEMOTION:
            self.mouse = e.pos
        elif e.type == MOUSEBUTTONDOWN and e.button == 1:
            self.mouse_down = True
            self.mouse = e.pos
        elif e.type == MOUSEBUTTONUP and e.button == 1:
            self.mouse_down = False
            self.mouse = e.pos
            self._click(e.pos)
        if e.type == WINDOWFOCUSLOST or e.type == _APP_BG:
            if self.state == "play":
                self.paused_from = "play"
                self._enter_state("pause")

    def quit(self):
        save_save(self.save)
        pygame.quit()
        sys.exit(0)

    def _key(self, k):
        st = self.state
        if k == K_F11:
            self.toggle_fullscreen()
            return
        if k == K_m:
            self.toggle_mute()
            return
        if k == K_AC_BACK:                       # аппаратная/жестовая кнопка «назад» на Android
            if st == "play":
                self.paused_from = "play"
                self._enter_state("pause")
            elif st == "pause":
                self._enter_state("play")
            elif st == "dead" and self.dead_timer > 0.35:
                self.to_menu()
            elif st == "menu":
                self.quit()
            return
        if st == "menu":
            if k in (K_SPACE, K_RETURN, K_KP_ENTER, K_UP, K_w):
                self.new_game()
        elif st == "play":
            if k in (K_SPACE, K_UP, K_w):
                self.sk.press_jump()
            elif k in (K_p, K_ESCAPE):
                self.paused_from = "play"
                self._enter_state("pause")
        elif st == "pause":
            if k in (K_p, K_ESCAPE):
                self._enter_state("play")
            elif k == K_r:
                self.new_game()
        elif st == "dead":
            if self.dead_timer > 0.35:
                if k in (K_r, K_SPACE, K_RETURN, K_KP_ENTER):
                    self.new_game()
                elif k == K_ESCAPE:
                    self.to_menu()

    def _click(self, pos):
        st = self.state
        B = self.buttons
        if st == "menu":
            if B["play"].hit(pos):
                self.audio.sfx("click")
                self.new_game()
            elif B["mute_menu"].hit(pos):
                self.audio.sfx("click")
                self.toggle_mute()
        elif st == "play":
            if B["pause"].hit(pos):
                self.audio.sfx("click")
                self.paused_from = "play"
                self._enter_state("pause")
            elif B["mute_play"].hit(pos):
                self.toggle_mute()
        elif st == "pause":
            if B["resume"].hit(pos):
                self.audio.sfx("click")
                self._enter_state("play")
            elif B["restart_p"].hit(pos):
                self.audio.sfx("click")
                self.new_game()
            elif B["menu_p"].hit(pos):
                self.audio.sfx("click")
                self.to_menu()
            elif B["mute_pause"].hit(pos):
                self.toggle_mute()
        elif st == "dead" and self.dead_timer > 0.35:
            if B["restart_d"].hit(pos):
                self.audio.sfx("click")
                self.new_game()
            elif B["menu_d"].hit(pos):
                self.audio.sfx("click")
                self.to_menu()

    # --- игровая логика (фиксированный шаг) ---
    def _crash(self, reason):
        sk = self.sk
        sk.alive = False
        self.crash_reason = reason
        A = self.assets
        vx0, vy0 = sk.vx, sk.vy
        bx, by = sk.x, sk.y - DECK_TOP * 0.6
        ch_img, ch_pv = A.char["dead"]
        bd_img, bd_pv = A.board
        wh_img, wh_pv = A.wheel

        def deb(img, pv, vx, vy, av, r, bounce=0.42):
            self.debris.append(Debris(img, pv, bx, by, vx, vy, sk.theta, av, r, bounce))
        deb(ch_img, ch_pv, vx0 * 0.30 + random.uniform(-90, 90), vy0 * 0.5 - 300, random.uniform(-7, 7), 46)
        deb(bd_img, bd_pv, vx0 * 0.55 + random.uniform(-70, 70), vy0 * 0.3 - 140, random.uniform(-11, 11), 40, 0.5)
        for sgn in (-1, 1):
            deb(wh_img, wh_pv, vx0 * 0.2 + sgn * 230 + random.uniform(-40, 40), -320 + random.uniform(-90, 0),
                sgn * random.uniform(14, 24), 12, 0.55)
        self.particles.chips(bx, by, 16, ORANGE)
        self.particles.burst(bx, by, 26, GOLD, speed=360)
        self.particles.dust(sk.x, sk.T.h(sk.x), 20, sk.vx, 1.4)
        self.cam.kick(1.0)
        self.hitstop = 0.10
        self.flash = 0.5
        self.audio.sfx("death")
        self.audio.set_roll(0.0)
        self._enter_state("dead")

    def _update_play(self, dt):
        w, sk = self.world, self.sk
        keys = pygame.key.get_pressed()
        roles = self.touches.values()
        right = keys[K_RIGHT] or keys[K_d] or ("boost" in roles)
        left = keys[K_LEFT] or keys[K_a] or ("brake" in roles)
        jump_held = keys[K_SPACE] or keys[K_UP] or keys[K_w] or ("jump" in roles)
        if self.touch_mode:
            self.touch_hint = max(0.0, self.touch_hint - dt)

        w.spawn_upto(sk.x + 2600)
        w.update(dt)
        sk.step(dt, right, left, jump_held)
        for kind, val in sk.events:
            if kind == "jump":
                self.audio.sfx("jump")
                self.particles.dust(sk.x, sk.T.h(sk.x), 10, sk.vx, 1.0)
            elif kind == "land":
                if val > 240:
                    self.audio.sfx("land", clamp(val / 900.0, 0.3, 1.0))
                    self.cam.kick(clamp(val / 2400.0, 0.0, 0.5))
                self.particles.dust(sk.x, sk.T.h(sk.x), int(clamp(val / 90.0, 3, 16)), sk.vx, 1.0)
        sk.events.clear()

        if sk.grounded and sk.speed > 40:
            self.dust_timer -= dt
            if self.dust_timer <= 0:
                self.dust_timer = DUST_DT
                self.particles.dust(sk.x - math.cos(sk.theta) * 34, sk.T.h(sk.x - 34) - 4, 1, sk.vx, 0.5)
        self.audio.set_roll(0.55 * clamp(sk.speed / SPEED_MAX, 0.0, 1.0) if sk.grounded else 0.0)

        if not sk.alive:
            self._crash(sk.crash_reason or "landing")
            return

        for o in w.obs:
            if abs(o.x - sk.x) < 260:
                for cx, cy, r in sk.circles(HIT_CIRCLES):
                    if o.hit(cx, cy, r):
                        self._crash(o.kind)
                        return
        hx, hy, hr = sk.circles(HIT_CIRCLES)[9]
        if sk.grounded and hy + hr > w.terrain.h(hx) + 2.0:
            self._crash("ground")
            return

        for (cx, cy, r) in sk.circles(COLLECT_CIRCLES):
            for p in w.pills:
                if p.x is None:
                    continue
                if (p.x - cx) ** 2 + (p.y - cy) ** 2 < (r + 13) ** 2:
                    px, py = p.x, p.y            # запомнить до обнуления (маркер удаления)
                    p.x = None
                    self.run_pills += 1
                    self.combo += 1
                    self.combo_timer = COMBO_WINDOW
                    idx = min(self.combo - 1, 7)
                    self.audio.sfx("pill%d" % idx, 0.9)
                    col = PILL_COLORS[p.var][0]
                    txt = "+1" if self.combo < 3 else "+1  x%d" % self.combo
                    self.floats.append(FloatingText(px, py - 30, txt, lerp_col(col, WHITE, 0.5), 26 if self.combo < 3 else 32))
                    self.particles.burst(px, py, 10, col, 200)
        if any(p.x is None for p in w.pills):
            w.pills = [p for p in w.pills if p.x is not None]

        if self.combo_timer > 0:
            self.combo_timer -= dt
            if self.combo_timer <= 0:
                self.combo_timer = 0.0
                self.combo = 0

        if not self.record_live and self.save["best_dist"] > 1.0 and sk.x / PX_PER_M > self.save["best_dist"]:
            self.record_live = True
            self.floats.append(FloatingText(sk.x, sk.y - 170, "НОВЫЙ РЕКОРД!", GOLD, 34, 1.3, -60))
            self.audio.sfx("record")

        w.prune(sk.x - 900)
        lookahead = clamp(sk.vx, -SPEED_MAX, SPEED_MAX) * 0.16
        self.cam.update(dt, sk.x, sk.y, lookahead)

    def _update_menu_world(self, dt):
        self.mw.terrain.ensure(self.msk.x + 3000)
        self.msk.step(dt, False, False, False, cruise=MENU_CRUISE)
        self.msk.events.clear()
        if not self.msk.alive or self.msk.x > 900000:
            self._make_menu_world()
        self.mcam.update(dt, self.msk.x, self.msk.y, MENU_CRUISE * 0.16)

    def _step(self, dt):
        self.t += dt
        self.clouds.update(dt)
        if self.state == "menu":
            self._update_menu_world(dt)
        elif self.state == "play":
            if self.hitstop > 0:
                self.hitstop -= dt
            else:
                self._update_play(dt)
            self.particles.update(dt)
            self.floats = [f for f in self.floats if f.update(dt)]
        elif self.state == "pause":
            pass
        elif self.state == "dead":
            self.dead_timer += dt
            for d in self.debris:
                d.update(dt, self.world.terrain)
            self.particles.update(dt)
            self.floats = [f for f in self.floats if f.update(dt)]
            self.flash = max(0.0, self.flash - dt * 2.2)
            if not self.saved_this_run and self.dead_timer > 0.05:
                self.saved_this_run = True
                dist = self.sk.x / PX_PER_M
                improved = False
                if dist > self.save["best_dist"]:
                    self.save["best_dist"] = dist
                    improved = True
                if self.run_pills > self.save["best_pills"]:
                    self.save["best_pills"] = self.run_pills
                    improved = True
                if improved:
                    save_save(self.save)

    def update(self, dt_real):
        self.acc += min(dt_real, 0.2)
        n = 0
        while self.acc >= PHYS_DT and n < 12:
            self._step(PHYS_DT)
            self.acc -= PHYS_DT
            n += 1
        mouse = self.mouse
        for b in self.buttons.values():
            b.update(dt_real, mouse)

    # --- отрисовка ---
    def _hud_play(self):
        s = self.screen
        sk = self.sk
        dist = int(sk.x / PX_PER_M)
        pill_icon = pygame.transform.smoothscale(self.assets.pills[0][0], (30, 17))
        badge(s, (24, 24), None, "%s м" % fmt_num(dist), GOLD, "topleft", w=190)
        badge(s, (24, 84), pill_icon, "%d" % self.run_pills, (90, 190, 255), "topleft", w=120)
        if self.combo >= 3:
            blit_text(s, "КОМБО x%d" % self.combo, 26, (24, 150), "topleft", ORANGE, INK, 2)
        self.buttons["pause"].draw(s)
        self.buttons["mute_play"].draw(s, self.audio.enabled)
        if self.touch_mode:
            roles = self.touches.values()
            draw_pedal(s, self.brake_rect, "brake", "brake" in roles)
            draw_pedal(s, self.boost_rect, "boost", "boost" in roles)
            if self.touch_hint > 0.01:
                a = int(255 * clamp(self.touch_hint / 0.5, 0.0, 1.0) if self.touch_hint < 0.5 else 255)
                blit_text(s, "Держите экран — прыжок выше", 26, (W // 2, 110), "center", WHITE, INK, 3, alpha=a)

    def _draw_world_layer(self, world, terrain, camx, camy, clouds):
        s = self.screen
        draw_background(s, self.assets, clouds, camx, camy)
        draw_decor(s, terrain, self.assets, camx, camy)
        draw_ground(s, terrain, camx, camy)
        draw_obstacles(s, world, self.assets, camx, camy)
        draw_pills(s, world, self.assets, camx, camy, self.t)

    def draw(self):
        s = self.screen
        if self.state == "menu":
            camx, camy = self.mcam.offset()
            self._draw_world_layer(self.mw, self.mw.terrain, camx, camy, self.clouds)
            self.particles.draw(s, camx, camy)
            draw_skater(s, self.assets, self.msk, camx, camy, self.t)
            s.blit(top_shade(), (0, 0))
            title = render_gradient_text("БЛОБ-СКЕЙТ", 96, (255, 244, 150), (255, 130, 40), th=8)
            s.blit(title, title.get_rect(center=(W // 2, 150)))
            sub = render_text("рыба-капля против гравитации", 24, WHITE, INK, 2, kind="body")
            s.blit(sub, sub.get_rect(center=(W // 2, 212)))
            self.buttons["play"].draw(s)
            if self.save["best_dist"] > 0:
                badge(s, (W // 2, 560), None, "рекорд: %s м  •  %d таб." % (fmt_num(self.save["best_dist"]), self.save["best_pills"]),
                      GOLD, "midtop", w=520)
            hint_txt = ("Нажимайте по экрану — прыжок,   педали внизу — газ/тормоз" if self.touch_mode else
                        "← / → — скорость,   ПРОБЕЛ — прыжок,   P — пауза,   F11 — экран")
            hint = render_text(hint_txt, 18, WHITE, INK, 2, kind="body")
            s.blit(hint, hint.get_rect(midbottom=(W // 2, H - 20)))
            self.buttons["mute_menu"].draw(s, self.audio.enabled)

        elif self.state in ("play", "pause"):
            camx, camy = self.cam.offset()
            self._draw_world_layer(self.world, self.world.terrain, camx, camy, self.clouds)
            self.particles.draw(s, camx, camy)
            if self.sk.alive:
                draw_skater(s, self.assets, self.sk, camx, camy, self.t)
            for f in self.floats:
                f.draw(s, camx, camy)
            self._hud_play()
            if self.state == "pause":
                dim_screen(s, 150)
                r = pygame.Rect(0, 0, 420, 580)
                r.center = (W // 2, H // 2)
                panel(s, r)
                blit_text(s, "ПАУЗА", 48, (W // 2, r.y + 66), "center", WHITE, INK, 4)
                for k in ("resume", "restart_p", "menu_p"):
                    self.buttons[k].draw(s)
                self.buttons["mute_pause"].draw(s, self.audio.enabled)

        elif self.state == "dead":
            camx, camy = self.cam.offset()
            self._draw_world_layer(self.world, self.world.terrain, camx, camy, self.clouds)
            for d in self.debris:
                d.draw(s, camx, camy)
            self.particles.draw(s, camx, camy)
            for f in self.floats:
                f.draw(s, camx, camy)
            if self.flash > 0.001:
                fl = pygame.Surface((W, H))
                fl.fill(WHITE)
                fl.set_alpha(int(200 * self.flash))
                s.blit(fl, (0, 0))
            if self.dead_timer > 0.35:
                dim_screen(s, 160)
                r = pygame.Rect(0, 0, 560, 660)
                r.center = (W // 2, H // 2 + 10)
                panel(s, r)
                msg = DEATH_TEXT.get(self.crash_reason, "Авария!")
                blit_text(s, msg, 34, (W // 2, r.y + 56), "center", RED, INK, 3)
                dist = int(self.sk.x / PX_PER_M)
                is_record = dist >= int(self.save["best_dist"]) and dist > 0
                blit_text(s, "%s м" % fmt_num(dist), 70, (W // 2, r.y + 150), "center", GOLD, INK, 5)
                if is_record:
                    blit_text(s, "★ НОВЫЙ РЕКОРД ★", 22, (W // 2, r.y + 196), "center", (255, 224, 120), INK, 2, kind="body")
                pill_icon = pygame.transform.smoothscale(self.assets.pills[0][0], (30, 17))
                badge(s, (W // 2, r.y + 246), pill_icon, "собрано: %d" % self.run_pills, (90, 190, 255), "midtop", w=260)
                badge(s, (W // 2, r.y + 312), None, "рекорд дистанции: %s м" % fmt_num(self.save["best_dist"]), GOLD, "midtop", w=380)
                badge(s, (W // 2, r.y + 368), None, "рекорд таблеток: %d" % self.save["best_pills"], (90, 190, 255), "midtop", w=380)
                self.buttons["restart_d"].draw(s)
                self.buttons["menu_d"].draw(s)

    def run(self):
        self._enter_state("menu")
        while True:
            dt = self.clock.tick(FPS) / 1000.0
            for e in pygame.event.get():
                self.handle_event(e)
            self.update(min(dt, 0.1))
            self.draw()
            pygame.display.flip()


def main():
    App().run()


if __name__ == "__main__":
    main()
