#!/usr/bin/env python3
"""
faro_demo_v2.py — Genera outputs/faro_demo_v2.mp4
90 segundos · 1920×1080 · 24fps · sin audio
moviepy 2.x API
"""

import os
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import VideoClip, concatenate_videoclips
from moviepy import vfx

# ─────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────
W, H   = 1920, 1080
FPS    = 24
ROOT   = os.path.dirname(os.path.abspath(__file__))
OUT    = os.path.join(ROOT, "outputs", "faro_demo_v2.mp4")

# ─────────────────────────────────────────────────────────────
# Paleta
# ─────────────────────────────────────────────────────────────
BG    = (10, 10, 26)
GOLD  = (201, 168, 76)
GOLD2 = (226, 201, 126)
GREEN = (0, 255, 65)
WHITE = (242, 237, 228)
DIM   = (80, 74, 55)
RED   = (176, 48, 48)
BLUE  = (100, 160, 255)
ORANGE= (255, 140, 50)
PURPLE= (180, 100, 255)
TEAL  = (0, 220, 160)

# ─────────────────────────────────────────────────────────────
# Fuentes
# ─────────────────────────────────────────────────────────────
_F = "C:/Windows/Fonts/"

def _fnt(name, size):
    try:
        return ImageFont.truetype(_F + name, size)
    except Exception:
        return ImageFont.load_default()

FG80 = _fnt("georgia.ttf",  80)
FG60 = _fnt("georgia.ttf",  60)
FG48 = _fnt("georgia.ttf",  48)
FG40 = _fnt("georgia.ttf",  40)
FG32 = _fnt("georgia.ttf",  32)
FM40 = _fnt("consola.ttf",  40)
FM32 = _fnt("consola.ttf",  32)
FM24 = _fnt("consola.ttf",  24)
FM18 = _fnt("consola.ttf",  18)
FM14 = _fnt("consola.ttf",  14)
FA32 = _fnt("arial.ttf",    32)
FA26 = _fnt("arial.ttf",    26)
FA22 = _fnt("arial.ttf",    22)

# ─────────────────────────────────────────────────────────────
# Utilidades
# ─────────────────────────────────────────────────────────────
def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3

def lerp(a, b, t):
    return a + (b - a) * max(0, min(1, t))

def bg_img():
    return Image.new("RGBA", (W, H), (*BG, 255))

def add_grid(img, alpha=0.035):
    draw = ImageDraw.Draw(img, "RGBA")
    c = (*GOLD, int(255 * alpha))
    for x in range(0, W + 1, 120):
        draw.line([(x, 0), (x, H)], fill=c, width=1)
    for y in range(0, H + 1, 120):
        draw.line([(0, y), (W, y)], fill=c, width=1)

def text_w(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]

def text_h(draw, text, font):
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]

def draw_centered(draw, text, y, font, color):
    w = text_w(draw, text, font)
    draw.text((W // 2 - w // 2, y), text, font=font, fill=color)

def draw_centered_x(draw, text, x, y, font, color):
    w = text_w(draw, text, font)
    draw.text((x - w // 2, y), text, font=font, fill=color)

def draw_scan_lines(draw, y1, y2, alpha=0.04):
    c = (*GREEN, int(255 * alpha))
    for y in range(y1, y2, 4):
        draw.line([(0, y), (W, y)], fill=c, width=1)

def img_frame(img):
    return np.array(img.convert("RGB"))

def glow_dot(draw, cx, cy, r, color, alpha):
    """Dot with soft glow rings"""
    for ring in range(4, 0, -1):
        rr = r + ring * 5
        a_ring = int(255 * alpha * (0.12 / ring))
        draw.ellipse(
            [(cx - rr, cy - rr), (cx + rr, cy + rr)],
            fill=(*color, a_ring)
        )
    a_dot = int(255 * alpha)
    draw.ellipse(
        [(cx - r, cy - r), (cx + r, cy + r)],
        fill=(*color, a_dot)
    )

def horizontal_rule(draw, y, x1=None, x2=None, color=GOLD, alpha=0.35):
    x1 = x1 or 60
    x2 = x2 or W - 60
    draw.line([(x1, y), (x2, y)], fill=(*color, int(255 * alpha)), width=1)

# SHA-256 real de Rotterdam
SHA256 = "99fa9be2368cc7c9265b917d5f1b9148e3e86d6e97ba461d5b795a5fcb28c9f9"

# ─────────────────────────────────────────────────────────────
# Zonas del mundo — (nombre, sector, lat, lon, color, data)
# ─────────────────────────────────────────────────────────────
ZONES = [
    ("CÓRDOBA",      "Agro",          -31.4,  -64.2,  GOLD,   "Score 49  ⚠"),
    ("BALCARCE",     "Agro",          -37.9,  -58.3,  GOLD,   "Score 70  ✓"),
    ("VACA MUERTA",  "Energy / O&G",  -38.8,  -68.9,  ORANGE, "Score 42  ✓"),
    ("ROTTERDAM",    "Maritime",       51.9,    4.5,   BLUE,   "Score 38  ✓"),
    ("PERMIAN",      "Oil & Gas",      31.8, -102.4,  ORANGE, "Score 52  ✓"),
    ("PILBARA",      "Mining",        -22.0,  118.0,  PURPLE, "Score 41  ✓"),
    ("AMAZONAS",     "Deforestation",  -3.5,  -60.0,  TEAL,   "Score 66  ✓"),
    ("INDIANA",      "Agro",           40.3,  -86.1,  GOLD,   "Score 38  ⚠"),
    ("MALACCA",      "Shipping",        2.2,  102.2,  BLUE,   "Score 42  ✓"),
    ("PUNTA COLORADA","Min./Tierras R.",-40.8, -65.0, PURPLE, "Score 41  ✓"),
]

def latlon_to_xy(lat, lon, margin_x=140, margin_y=110):
    """Equirectangular projection"""
    x = int(margin_x + (lon + 180) / 360 * (W - 2 * margin_x))
    y = int(margin_y + (90 - lat) / 180 * (H - 2 * margin_y))
    return x, y

# ─────────────────────────────────────────────────────────────
# ESCENA 1 — Logo (0–5s)
# ─────────────────────────────────────────────────────────────
def make_logo(t):
    img = bg_img()
    add_grid(img)
    draw = ImageDraw.Draw(img, "RGBA")

    # Fade in 0→1.5s, hold, fade out 4→5s
    if t < 1.5:
        a = ease(t / 1.5)
    elif t > 4.2:
        a = ease((5.0 - t) / 0.8)
    else:
        a = 1.0

    cy = H // 2

    # Horizontal rule top
    ry = cy - 120
    draw.line([(W // 2 - 340, ry), (W // 2 + 340, ry)],
              fill=(*GOLD, int(255 * a * 0.3)), width=1)

    # Kicker
    kicker = "▸  SATELLITE INTELLIGENCE PROTOCOL  ◂"
    kw = text_w(draw, kicker, FM18)
    draw.text((W // 2 - kw // 2, cy - 95), kicker,
              font=FM18, fill=(*GOLD, int(255 * a * 0.55)))

    # Title: FARO PROTOCOL
    title = "FARO  PROTOCOL"
    tw = text_w(draw, title, FG80)
    draw.text((W // 2 - tw // 2, cy - 55), title,
              font=FG80, fill=(*GOLD, int(255 * a)))

    # Subtitle
    sub = "Physical Truth from Orbit"
    sw = text_w(draw, sub, FM32)
    draw.text((W // 2 - sw // 2, cy + 55), sub,
              font=FM32, fill=(*WHITE, int(255 * a * 0.85)))

    # Horizontal rule bottom
    draw.line([(W // 2 - 240, cy + 110), (W // 2 + 240, cy + 110)],
              fill=(*GOLD, int(255 * a * 0.3)), width=1)

    # Pulse dot
    pulse = 0.5 + 0.5 * math.sin(t * 3.0)
    glow_dot(draw, W // 2, cy + 145, 4, GOLD, a * (0.5 + 0.4 * pulse))

    return img_frame(img)

S1 = VideoClip(make_logo, duration=5)

# ─────────────────────────────────────────────────────────────
# ESCENA 2 — Mapa mundial con zonas (5–20s → duración 15s)
# ─────────────────────────────────────────────────────────────
def draw_world_grid(draw):
    """Lat/lon grid lines — estilo satélite"""
    c_minor = (*GOLD, 18)
    c_major = (*GOLD, 35)
    c_equator = (*TEAL, 55)

    # Meridianos cada 30°
    for lon in range(-180, 181, 30):
        x, _ = latlon_to_xy(0, lon)
        color = c_major if lon % 90 == 0 else c_minor
        draw.line([(x, 110), (x, H - 110)], fill=color, width=1)

    # Paralelos cada 30°
    for lat in range(-90, 91, 30):
        _, y = latlon_to_xy(lat, 0)
        if not (110 <= y <= H - 110):
            continue
        color = c_equator if lat == 0 else (c_major if lat % 60 == 0 else c_minor)
        draw.line([(140, y), (W - 140, y)], fill=color, width=1)

    # Label ecuador
    _, eq_y = latlon_to_xy(0, 0)
    draw.text((145, eq_y + 4), "EQ", font=FM14, fill=(*TEAL, 80))


def make_world_map(t):
    """15s duration — zones appear one by one ~every 1.3s"""
    img = bg_img()
    add_grid(img)
    draw = ImageDraw.Draw(img, "RGBA")
    draw_world_grid(draw)

    # Header
    draw.text((60, 38), "FARO PROTOCOL  ·  GLOBAL COVERAGE",
              font=FM18, fill=(*GOLD, 180))
    draw.text((W - 60 - text_w(draw, "10 ACTIVE ZONES", FM18), 38),
              "10 ACTIVE ZONES", font=FM18, fill=(*GOLD, 180))
    draw.line([(60, 80), (W - 60, 80)], fill=(*GOLD, 50), width=1)

    # Zones appear one per ~1.2s
    zone_interval = 15.0 / len(ZONES)

    for i, (name, sector, lat, lon, color, data) in enumerate(ZONES):
        appear_t = i * zone_interval
        local_t  = t - appear_t
        if local_t < 0:
            continue

        a = min(1.0, ease(local_t / 0.6))
        pulse = 0.5 + 0.5 * math.sin((t - appear_t) * 4.0)
        dot_a  = a * (0.7 + 0.3 * pulse)

        cx, cy = latlon_to_xy(lat, lon)

        # Dot radius pulsante
        r = int(lerp(14, 10, min(1, local_t - 0.3)))

        glow_dot(draw, cx, cy, r, color, dot_a)

        # Label (aparece medio segundo después)
        if local_t > 0.4:
            la = ease(min(1.0, (local_t - 0.4) / 0.5))
            label_y = cy - 36 if cy > H // 2 else cy + 18
            lw = text_w(draw, name, FM18)
            lx = cx - lw // 2

            # Fondo semitransparente para legibilidad
            pad = 5
            draw.rectangle(
                [(lx - pad, label_y - 2), (lx + lw + pad, label_y + 22)],
                fill=(*BG, int(200 * la))
            )
            draw.text((lx, label_y), name,
                      font=FM18, fill=(*color, int(255 * la)))

            # Sector y score debajo
            if local_t > 0.8:
                sa = ease(min(1.0, (local_t - 0.8) / 0.5))
                sec_y = label_y + 24
                sw2 = text_w(draw, sector, FM14)
                sx2 = cx - sw2 // 2
                draw.rectangle(
                    [(sx2 - pad, sec_y - 1), (sx2 + sw2 + pad, sec_y + 18)],
                    fill=(*BG, int(180 * sa))
                )
                draw.text((sx2, sec_y), sector,
                          font=FM14, fill=(*WHITE, int(180 * sa)))

    # Footer counter
    n_visible = sum(1 for i in range(len(ZONES)) if t >= i * zone_interval)
    counter = f"{n_visible} / {len(ZONES)}  zones online"
    draw.text((W // 2 - text_w(draw, counter, FM18) // 2, H - 65),
              counter, font=FM18, fill=(*GOLD, 140))
    draw.line([(60, H - 85), (W - 60, H - 85)], fill=(*GOLD, 45), width=1)

    return img_frame(img)

S2 = VideoClip(make_world_map, duration=15)

# ─────────────────────────────────────────────────────────────
# ESCENA 3 — Reporte Balcarce (20–35s → 15s)
# ─────────────────────────────────────────────────────────────
_balcarce_raw = Image.open(
    os.path.join(ROOT, "faro_reporte_fusion_balcarce.png")
).convert("RGB")

# Escalar para que quepa a la derecha (60% del ancho)
_rpanel_w = int(W * 0.60)
_rpanel_h = H - 140
_rscale = min(_rpanel_w / _balcarce_raw.width, _rpanel_h / _balcarce_raw.height)
_rw = int(_balcarce_raw.width  * _rscale)
_rh = int(_balcarce_raw.height * _rscale)
_balcarce = _balcarce_raw.resize((_rw, _rh), Image.LANCZOS)
_rimg_x = W - _rw - 30
_rimg_y = (H - _rh) // 2

BALCARCE_STATS = [
    ("NDVI",          "0.531",    TEAL),
    ("SAR",           "-13.1 dB", BLUE),
    ("Fusión",        "0.7115",   GOLD),
    ("Rinde est.",    "3.32 t/ha",GREEN),
    ("Score",         "70",       GREEN),
    ("Estado",        "✓ NORMAL", GREEN),
]

def make_balcarce(t):
    img = bg_img()
    add_grid(img)

    # Fade in global
    a_global = ease(min(1.0, t / 0.8))

    # Pegar imagen del reporte (aparece deslizando desde la derecha)
    slide = ease(min(1.0, t / 1.2))
    img_x = int(lerp(W + 50, _rimg_x, slide))

    # Compositar reporte
    report_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    report_overlay.paste(_balcarce, (img_x, _rimg_y))
    img = Image.alpha_composite(img, report_overlay)

    draw = ImageDraw.Draw(img, "RGBA")

    # Panel izquierdo
    panel_x = 55
    panel_w = W - _rw - 90

    # Header
    draw.text((panel_x, 45), "FARO PROTOCOL",
              font=FM18, fill=(*GOLD, int(255 * a_global * 0.7)))
    draw.text((panel_x, 70), "CERTIFIED FIELD REPORT",
              font=FM14, fill=(*WHITE, int(255 * a_global * 0.5)))
    draw.line([(panel_x, 105), (panel_x + panel_w - 20, 105)],
              fill=(*GOLD, int(255 * a_global * 0.4)), width=1)

    # Nombre de zona
    name_a = ease(min(1.0, (t - 0.3) / 0.7))
    draw.text((panel_x, 130), "BALCARCE",
              font=FG60, fill=(*GOLD, int(255 * name_a)))
    draw.text((panel_x, 210), "Región Pampeana · Argentina",
              font=FA22, fill=(*WHITE, int(255 * name_a * 0.7)))
    draw.text((panel_x, 245), "Vertical: AGRICULTURE",
              font=FM18, fill=(*TEAL, int(255 * name_a * 0.8)))

    # Stats uno por uno
    stats_start_y = 310
    row_h = 90
    for i, (label, value, color) in enumerate(BALCARCE_STATS):
        appear = (t - 0.8 - i * 0.25)
        if appear < 0:
            continue
        sa = ease(min(1.0, appear / 0.4))
        ry = stats_start_y + i * row_h
        # Fondo de fila
        draw.rectangle(
            [(panel_x, ry), (panel_x + panel_w - 30, ry + 72)],
            fill=(*BG, int(200 * sa))
        )
        draw.rectangle(
            [(panel_x, ry), (panel_x + 4, ry + 72)],
            fill=(*color, int(200 * sa))
        )
        draw.text((panel_x + 18, ry + 8), label,
                  font=FM18, fill=(*WHITE, int(200 * sa)))
        draw.text((panel_x + 18, ry + 32), value,
                  font=FG40, fill=(*color, int(255 * sa)))

    # SHA badge
    sha_t = t - 2.5
    if sha_t > 0:
        sha_a = ease(min(1.0, sha_t / 0.5))
        sha_y = H - 95
        draw.rectangle([(panel_x, sha_y), (panel_x + panel_w - 30, sha_y + 50)],
                       fill=(*BG, int(220 * sha_a)))
        draw.rectangle([(panel_x, sha_y), (panel_x + panel_w - 30, sha_y + 50)],
                       outline=(*GOLD, int(80 * sha_a)), width=1)
        draw.text((panel_x + 10, sha_y + 7),
                  "SHA-256 SEALED", font=FM14, fill=(*GOLD, int(255 * sha_a * 0.8)))
        sha_txt = "© 2026 Faro Protocol · Cryptographic proof"
        draw.text((panel_x + 10, sha_y + 28), sha_txt,
                  font=FM14, fill=(*WHITE, int(180 * sha_a)))

    return img_frame(img)

S3 = VideoClip(make_balcarce, duration=15)

# ─────────────────────────────────────────────────────────────
# ESCENA 4 — Rotterdam / SHA-256 typewriter (35–50s → 15s)
# ─────────────────────────────────────────────────────────────
_rotterdam_raw = Image.open(
    os.path.join(ROOT, "faro_reporte_fusion_rotterdam.png")
).convert("RGB")
_rotterdam = _rotterdam_raw.resize((_rw, _rh), Image.LANCZOS)

ROTTERDAM_STATS = [
    ("SECTOR",     "MARITIME",      BLUE),
    ("SAR",        "-14.7 dB",      BLUE),
    ("NDVI",       "0.112",         TEAL),
    ("Score",      "38",            GREEN),
    ("Estado",     "✓ NORMAL",      GREEN),
]

def make_rotterdam(t):
    img = bg_img()
    add_grid(img)

    a_global = ease(min(1.0, t / 0.8))

    # Reporte Rotterdam
    slide = ease(min(1.0, t / 1.2))
    img_x = int(lerp(W + 50, _rimg_x, slide))
    report_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    report_overlay.paste(_rotterdam, (img_x, _rimg_y))
    img = Image.alpha_composite(img, report_overlay)

    draw = ImageDraw.Draw(img, "RGBA")
    panel_x = 55
    panel_w = W - _rw - 90

    # Header
    draw.text((panel_x, 45), "FARO PROTOCOL",
              font=FM18, fill=(*GOLD, int(255 * a_global * 0.7)))
    draw.text((panel_x, 70), "CERTIFIED FIELD REPORT",
              font=FM14, fill=(*WHITE, int(255 * a_global * 0.5)))
    draw.line([(panel_x, 105), (panel_x + panel_w - 20, 105)],
              fill=(*GOLD, int(255 * a_global * 0.4)), width=1)

    name_a = ease(min(1.0, (t - 0.3) / 0.7))
    draw.text((panel_x, 130), "ROTTERDAM",
              font=FG60, fill=(*BLUE, int(255 * name_a)))
    draw.text((panel_x, 210), "Port of Rotterdam · Netherlands",
              font=FA22, fill=(*WHITE, int(255 * name_a * 0.7)))
    draw.text((panel_x, 245), "Vertical: MARITIME",
              font=FM18, fill=(*BLUE, int(255 * name_a * 0.8)))

    # Stats
    stats_start_y = 310
    row_h = 88
    for i, (label, value, color) in enumerate(ROTTERDAM_STATS):
        appear = t - 0.8 - i * 0.25
        if appear < 0:
            continue
        sa = ease(min(1.0, appear / 0.4))
        ry = stats_start_y + i * row_h
        draw.rectangle(
            [(panel_x, ry), (panel_x + panel_w - 30, ry + 72)],
            fill=(*BG, int(200 * sa))
        )
        draw.rectangle(
            [(panel_x, ry), (panel_x + 4, ry + 72)],
            fill=(*color, int(200 * sa))
        )
        draw.text((panel_x + 18, ry + 8), label,
                  font=FM18, fill=(*WHITE, int(200 * sa)))
        draw.text((panel_x + 18, ry + 32), value,
                  font=FG40, fill=(*color, int(255 * sa)))

    # SHA-256 typewriter
    sha_start = 3.0
    sha_t = t - sha_start
    if sha_t > 0:
        sha_a = ease(min(1.0, sha_t / 0.4))
        # Cuántos caracteres mostrar
        chars_per_sec = 18
        n_chars = min(len(SHA256), int(sha_t * chars_per_sec))
        sha_display = SHA256[:n_chars]
        if n_chars < len(SHA256):
            sha_display += "_"  # cursor

        sha_box_y = H - 155
        draw.rectangle(
            [(panel_x, sha_box_y), (panel_x + panel_w - 30, sha_box_y + 115)],
            fill=(*BG, int(230 * sha_a))
        )
        draw.rectangle(
            [(panel_x, sha_box_y), (panel_x + panel_w - 30, sha_box_y + 115)],
            outline=(*GREEN, int(100 * sha_a)), width=1
        )
        draw.text((panel_x + 12, sha_box_y + 8),
                  "SHA-256 FINGERPRINT", font=FM14,
                  fill=(*GREEN, int(255 * sha_a * 0.9)))
        draw.line(
            [(panel_x + 12, sha_box_y + 33),
             (panel_x + panel_w - 42, sha_box_y + 33)],
            fill=(*GREEN, int(80 * sha_a)), width=1
        )

        # Mostrar hash en 2 líneas de 32 chars
        h1 = sha_display[:32]
        h2 = sha_display[32:] if len(sha_display) > 32 else ""
        draw.text((panel_x + 12, sha_box_y + 42), h1,
                  font=FM18, fill=(*GREEN, int(255 * sha_a)))
        if h2:
            draw.text((panel_x + 12, sha_box_y + 68), h2,
                      font=FM18, fill=(*GREEN, int(255 * sha_a)))

        # Verified badge cuando el hash esté completo
        if n_chars >= len(SHA256):
            v_a = ease(min(1.0, (sha_t - len(SHA256) / chars_per_sec) / 0.5))
            draw.text((panel_x + 12, sha_box_y + 94),
                      "✓ BLOCKCHAIN TIMESTAMP VERIFIED",
                      font=FM14, fill=(*GREEN, int(255 * v_a)))

    return img_frame(img)

S4 = VideoClip(make_rotterdam, duration=15)

# ─────────────────────────────────────────────────────────────
# ESCENA 5 — Tres frases (50–70s → 20s)
# ─────────────────────────────────────────────────────────────
PHRASES = [
    ("48 hours before\nofficial data",
     "Satellite analysis delivered before any\ngovernment or exchange report.",
     GOLD),
    ("Cryptographically\nverified",
     "Every reading sealed with SHA-256.\nImmutable. Auditable. Legal-grade.",
     GREEN),
    ("Any asset.\nAny location.\nAny sector.",
     "Agriculture · Energy · Maritime\nMining · Shipping · ESG",
     BLUE),
]

def make_phrases(t):
    """20s — cada frase ~6s (2s appear, 2s hold, 2s fade out para siguiente)"""
    phrase_dur = 20.0 / len(PHRASES)   # ~6.67s per phrase

    # Frase activa
    phrase_idx = int(t / phrase_dur)
    phrase_idx = min(phrase_idx, len(PHRASES) - 1)
    local_t    = t - phrase_idx * phrase_dur

    img = bg_img()
    add_grid(img)
    draw = ImageDraw.Draw(img, "RGBA")

    # Scanlines ambientales
    draw_scan_lines(draw, 0, H, alpha=0.025)

    main_text, sub_text, color = PHRASES[phrase_idx]

    # Alpha entrada/salida
    if local_t < 1.2:
        a = ease(local_t / 1.2)
    elif local_t > phrase_dur - 1.0:
        a = ease((phrase_dur - local_t) / 1.0)
    else:
        a = 1.0

    # Número de frase
    num_str = f"{phrase_idx + 1}  /  {len(PHRASES)}"
    draw.text((W // 2 - text_w(draw, num_str, FM18) // 2, 50),
              num_str, font=FM18, fill=(*GOLD, int(255 * a * 0.4)))

    # Frase principal — líneas
    lines = main_text.split("\n")
    total_h_main = sum(text_h(draw, l, FG60) + 12 for l in lines)
    sub_lines = sub_text.split("\n")
    total_h_sub = sum(text_h(draw, l, FA26) + 8 for l in sub_lines)
    total_h = total_h_main + 50 + total_h_sub
    y = H // 2 - total_h // 2

    for i, line in enumerate(lines):
        # Slide in
        slide_a = ease(min(1.0, (local_t - i * 0.15) / 0.9))
        slide_x = int(lerp(60, 0, slide_a))
        lw = text_w(draw, line, FG60)
        lx = W // 2 - lw // 2 + slide_x
        draw.text((lx, y), line,
                  font=FG60, fill=(*color, int(255 * a * slide_a)))
        y += text_h(draw, line, FG60) + 12

    # Separador
    y += 20
    rule_a = ease(min(1.0, (local_t - 0.6) / 0.6))
    draw.line([(W // 2 - 200, y), (W // 2 + 200, y)],
              fill=(*color, int(255 * a * rule_a * 0.45)), width=1)
    y += 30

    # Sub-texto
    for i, line in enumerate(sub_lines):
        sub_appear = ease(min(1.0, (local_t - 0.9 - i * 0.1) / 0.6))
        lw = text_w(draw, line, FA26)
        draw.text((W // 2 - lw // 2, y), line,
                  font=FA26, fill=(*WHITE, int(200 * a * sub_appear)))
        y += text_h(draw, line, FA26) + 8

    # Contador de fondo (dots de progreso)
    dot_y = H - 60
    for di in range(len(PHRASES)):
        dot_x = W // 2 + (di - len(PHRASES) // 2) * 30
        dot_c = GOLD if di <= phrase_idx else DIM
        glow_dot(draw, dot_x, dot_y, 5 if di == phrase_idx else 3, dot_c, 0.8)

    return img_frame(img)

S5 = VideoClip(make_phrases, duration=20)

# ─────────────────────────────────────────────────────────────
# ESCENA 6 — Seis sectores (70–80s → 10s)
# ─────────────────────────────────────────────────────────────
SECTORS = [
    ("🌾", "Agriculture",     "Crops · Yield · Stress",       GOLD),
    ("⚡", "Energy / O&G",    "Permian · Vaca Muerta · DRC",  ORANGE),
    ("🚢", "Maritime",        "Rotterdam · Port activity",     BLUE),
    ("⛏",  "Mining",          "Pilbara · Iron · REE",         PURPLE),
    ("🌳", "ESG / Deforest.", "Amazonas · Carbon · Land use", TEAL),
    ("🛳", "Shipping",        "Malacca · Strait · Freight",   BLUE),
]

def make_sectors(t):
    """10s — sectores aparecen uno por uno cada ~1.4s"""
    img = bg_img()
    add_grid(img)
    draw = ImageDraw.Draw(img, "RGBA")

    # Header
    a_hdr = ease(min(1.0, t / 0.5))
    draw.text((W // 2 - text_w(draw, "COVERAGE  ·  6 SECTORS", FG48) // 2, 55),
              "COVERAGE  ·  6 SECTORS",
              font=FG48, fill=(*GOLD, int(255 * a_hdr)))
    draw.line([(200, 120), (W - 200, 120)],
              fill=(*GOLD, int(255 * a_hdr * 0.3)), width=1)

    # Grid 3 × 2
    cols, rows = 3, 2
    cell_w = (W - 120) // cols
    cell_h = (H - 200) // rows
    interval = 10.0 / len(SECTORS)

    for i, (icon, name, desc, color) in enumerate(SECTORS):
        col = i % cols
        row = i // cols
        appear = t - i * interval
        if appear < 0:
            continue
        a = ease(min(1.0, appear / 0.6))

        cx = 60 + col * cell_w + cell_w // 2
        cy = 160 + row * cell_h + cell_h // 2

        # Caja
        bx1 = 60 + col * cell_w + 20
        by1 = 160 + row * cell_h + 20
        bx2 = bx1 + cell_w - 40
        by2 = by1 + cell_h - 40

        # Fondo de caja
        draw.rectangle([(bx1, by1), (bx2, by2)],
                       fill=(*BG, int(240 * a)))
        draw.rectangle([(bx1, by1), (bx2, by2)],
                       outline=(*color, int(80 * a)), width=1)
        # Acento izquierdo
        draw.rectangle([(bx1, by1), (bx1 + 4, by2)],
                       fill=(*color, int(200 * a)))

        # Ícono
        draw.text((bx1 + 22, by1 + 18), icon,
                  font=FG40, fill=(*color, int(255 * a)))

        # Nombre
        draw.text((bx1 + 22, by1 + 70), name,
                  font=FG32, fill=(*color, int(255 * a)))

        # Descripción
        draw.text((bx1 + 22, by1 + 115), desc,
                  font=FM18, fill=(*WHITE, int(180 * a)))

        # Dot de estado
        dot_a = ease(min(1.0, (appear - 0.4) / 0.4))
        if dot_a > 0:
            glow_dot(draw, bx2 - 22, by1 + 28, 7, GREEN, dot_a * 0.9)

    return img_frame(img)

S6 = VideoClip(make_sectors, duration=10)

# ─────────────────────────────────────────────────────────────
# ESCENA 7 — URL + Logo + fade negro (80–90s → 10s)
# ─────────────────────────────────────────────────────────────
URL = "faro-protocol.netlify.app"

def make_outro(t):
    img = bg_img()
    add_grid(img)
    draw = ImageDraw.Draw(img, "RGBA")

    # Fade in 0→2s, hold 2→8s, fade out 8→10s
    if t < 2.0:
        a = ease(t / 2.0)
    elif t > 8.0:
        a = ease((10.0 - t) / 2.0)
    else:
        a = 1.0

    cy = H // 2

    # Glow central
    pulse = 0.5 + 0.5 * math.sin(t * 2.5)
    glow_dot(draw, W // 2, cy - 120, 30, GOLD, a * 0.12 * pulse)

    # Horizontal rule
    rl_a = ease(min(1.0, t / 1.0))
    draw.line([(W // 2 - 380, cy - 95), (W // 2 + 380, cy - 95)],
              fill=(*GOLD, int(255 * a * rl_a * 0.35)), width=1)

    # FARO PROTOCOL
    logo_a = ease(min(1.0, t / 1.4))
    title = "FARO  PROTOCOL"
    tw = text_w(draw, title, FG80)
    draw.text((W // 2 - tw // 2, cy - 80), title,
              font=FG80, fill=(*GOLD, int(255 * a * logo_a)))

    # Sub
    sub_a = ease(min(1.0, (t - 0.5) / 1.0))
    sub = "Physical Truth from Orbit"
    sw = text_w(draw, sub, FM32)
    draw.text((W // 2 - sw // 2, cy + 20), sub,
              font=FM32, fill=(*WHITE, int(255 * a * sub_a * 0.85)))

    draw.line([(W // 2 - 280, cy + 78), (W // 2 + 280, cy + 78)],
              fill=(*GOLD, int(255 * a * rl_a * 0.35)), width=1)

    # URL
    url_a = ease(min(1.0, (t - 1.2) / 1.0))
    uw = text_w(draw, URL, FM32)
    draw.text((W // 2 - uw // 2, cy + 105), URL,
              font=FM32, fill=(*GOLD2, int(255 * a * url_a * 0.9)))

    # Tagline
    tag_a = ease(min(1.0, (t - 2.0) / 0.8))
    tag = "SAR · NDVI · SHA-256 · 24h delivery · Any location"
    tw2 = text_w(draw, tag, FM18)
    draw.text((W // 2 - tw2 // 2, cy + 162), tag,
              font=FM18, fill=(*WHITE, int(200 * a * tag_a * 0.65)))

    # Dot inferior
    dot_a = ease(min(1.0, (t - 2.5) / 0.5))
    glow_dot(draw, W // 2, cy + 215, 4, GOLD, a * dot_a * 0.7)

    return img_frame(img)

S7 = VideoClip(make_outro, duration=10)

# ─────────────────────────────────────────────────────────────
# Ensamblar y exportar
# ─────────────────────────────────────────────────────────────
def main():
    os.makedirs(os.path.join(ROOT, "outputs"), exist_ok=True)

    print("Ensamblando escenas...")
    final = concatenate_videoclips([S1, S2, S3, S4, S5, S6, S7])

    print(f"Duración total: {final.duration:.1f}s  ({final.duration * FPS:.0f} frames)")
    print(f"Exportando -> {OUT}")
    print("Esto puede tardar 2-5 minutos...")

    final.write_videofile(
        OUT,
        fps=FPS,
        codec="libx264",
        audio=False,
        preset="medium",
        ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p"],
        logger="bar",
    )
    print(f"[OK] Video generado: {OUT}")

if __name__ == "__main__":
    main()
