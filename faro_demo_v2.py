#!/usr/bin/env python3
"""
faro_demo_v2.py  -  outputs/faro_demo_v2.mp4
60s · 1920×1080 · 30fps · sin audio
Estilo: Bloomberg terminal meets satellite mission control
"""

import os, math, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import VideoClip, concatenate_videoclips

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════
W, H  = 1920, 1080
FPS   = 30
ROOT  = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(ROOT, "outputs", "faro_demo_v2.mp4")

BG     = (10,  10,  26)
GOLD   = (201, 168, 76)
GOLD2  = (226, 201, 126)
GOLD3  = (140, 110, 45)
GREEN  = (0,   255, 65)
GREEN2 = (0,   200, 80)
WHITE  = (242, 237, 228)
DIM    = (50,  46,  32)
ORANGE = (255, 140, 50)
BLUE   = (80,  150, 255)
PURPLE = (180, 100, 255)
TEAL   = (0,   200, 160)

_F = "C:/Windows/Fonts/"
def _fnt(n, s):
    try: return ImageFont.truetype(_F + n, s)
    except: return ImageFont.load_default()

# Tipografías
FGB100 = _fnt("georgiab.ttf", 100)  # Serif bold grande — títulos hero
FGB72  = _fnt("georgiab.ttf",  72)
FG60   = _fnt("georgia.ttf",   60)
FG48   = _fnt("georgia.ttf",   48)
FG36   = _fnt("georgia.ttf",   36)
FG28   = _fnt("georgia.ttf",   28)
FM44   = _fnt("consola.ttf",   44)  # Mono — datos, scores
FM32   = _fnt("consola.ttf",   32)
FM24   = _fnt("consola.ttf",   24)
FM18   = _fnt("consola.ttf",   18)
FM14   = _fnt("consola.ttf",   14)
FM12   = _fnt("consola.ttf",   12)
FA28   = _fnt("arial.ttf",     28)
FA22   = _fnt("arial.ttf",     22)

# ═══════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════
def ease(t):
    t = max(0., min(1., t))
    return t*t*(3-2*t)

def ease_out(t):
    t = max(0., min(1., t))
    return 1-(1-t)**3

def lerp(a, b, t): return a + (b-a)*max(0.,min(1.,t))

def bg():
    return Image.new("RGBA", (W, H), (*BG, 255))

def npf(img):
    return np.array(img.convert("RGB"))

def tw(d, txt, fnt):
    b = d.textbbox((0,0), txt, font=fnt); return b[2]-b[0]

def th(d, txt, fnt):
    b = d.textbbox((0,0), txt, font=fnt); return b[3]-b[1]

def cx_text(d, txt, y, fnt, col, a=255):
    w = tw(d, txt, fnt)
    d.text(((W-w)//2, y), txt, font=fnt, fill=(*col[:3], a))

def hline(d, y, x1=80, x2=None, col=GOLD, a=0.35, w=1):
    if x2 is None: x2 = W-80
    d.line([(x1,y),(x2,y)], fill=(*col, int(255*a)), width=w)

def vline(d, x, y1=0, y2=None, col=GOLD, a=0.25, w=1):
    if y2 is None: y2 = H
    d.line([(x,y1),(x,y2)], fill=(*col, int(255*a)), width=w)

def ring(d, cx, cy, r, col, a, w=1):
    d.ellipse([(cx-r,cy-r),(cx+r,cy+r)], outline=(*col, int(255*a)), width=w)

def dot(d, cx, cy, r, col, a):
    d.ellipse([(cx-r,cy-r),(cx+r,cy+r)], fill=(*col, int(255*a)))

def crosshair(d, cx, cy, size, col, a):
    """Cruz de targeting tipo HUD"""
    s, g = size, size//3
    d.line([(cx-s,cy),(cx-g,cy)], fill=(*col,int(255*a)), width=1)
    d.line([(cx+g,cy),(cx+s,cy)], fill=(*col,int(255*a)), width=1)
    d.line([(cx,cy-s),(cx,cy-g)], fill=(*col,int(255*a)), width=1)
    d.line([(cx,cy+g),(cx,cy+s)], fill=(*col,int(255*a)), width=1)

def corner_brackets(d, x1, y1, x2, y2, col, a, sz=20):
    """Brackets de esquina tipo targeting"""
    c = (*col, int(255*a))
    w = 1
    # TL
    d.line([(x1,y1),(x1+sz,y1)], fill=c, width=w)
    d.line([(x1,y1),(x1,y1+sz)], fill=c, width=w)
    # TR
    d.line([(x2,y1),(x2-sz,y1)], fill=c, width=w)
    d.line([(x2,y1),(x2,y1+sz)], fill=c, width=w)
    # BL
    d.line([(x1,y2),(x1+sz,y2)], fill=c, width=w)
    d.line([(x1,y2),(x1,y2-sz)], fill=c, width=w)
    # BR
    d.line([(x2,y2),(x2-sz,y2)], fill=c, width=w)
    d.line([(x2,y2),(x2,y2-sz)], fill=c, width=w)

def scanline_overlay(d, a=0.025):
    """Líneas horizontales finas — efecto CRT/monitor"""
    c = (*GREEN, int(255*a))
    for y in range(0, H, 4):
        d.line([(0,y),(W,y)], fill=c, width=1)

# ═══════════════════════════════════════════════════════════════
# DATOS
# ═══════════════════════════════════════════════════════════════
SHA256 = "99fa9be2368cc7c9265b917d5f1b9148e3e86d6e97ba461d5b795a5fcb28c9f9"
TIMESTAMP = "2026-04-04  08:32:17 UTC"

# Continentes — polylines (lon, lat) simplificadas Natural Earth 110m
CONTINENTS = [
    # Norteamérica
    [(-168,71),(-165,64),(-162,60),(-153,58),(-148,61),(-141,60),
     (-131,55),(-127,50),(-124,47),(-124,37),(-118,33),(-112,28),
     (-106,24),(-97,20),(-90,16),(-85,11),(-79,9),
     (-84,9),(-87,16),(-90,21),(-97,26),(-90,30),(-82,30),(-81,31),
     (-75,35),(-74,41),(-70,42),(-66,44),(-60,47),(-53,47),
     (-55,50),(-60,47),(-65,57),(-68,62),(-73,68),(-80,73),
     (-90,73),(-100,73),(-110,72),(-125,72),(-140,70),(-157,70),(-168,71)],
    # Groenlandia
    [(-65,76),(-55,75),(-45,76),(-38,78),(-25,76),(-20,72),
     (-23,68),(-28,65),(-37,63),(-43,63),(-52,65),(-57,66),(-62,67),(-65,71),(-65,76)],
    # Sudamérica
    [(-79,9),(-77,10),(-74,12),(-62,11),(-60,8),(-52,5),
     (-49,0),(-35,-5),(-35,-10),(-37,-14),(-40,-20),(-43,-23),
     (-48,-25),(-52,-32),(-52,-33),(-57,-38),(-62,-43),
     (-65,-47),(-68,-53),(-72,-55),(-68,-56),(-65,-55),
     (-57,-51),(-53,-33),(-49,-28),(-40,-20),(-35,-8),
     (-35,-5),(-50,2),(-59,5),(-60,8),(-62,11),(-72,12),(-79,9)],
    # Europa occidental
    [(-9,36),(-5,36),(0,39),(3,41),(4,44),(7,44),(8,47),(10,47),
     (13,46),(14,41),(15,41),(18,40),(20,38),(23,38),(26,39),
     (28,41),(30,43),(29,46),(28,55),(24,57),(20,59),(18,60),
     (15,58),(12,56),(10,57),(8,55),(5,52),(2,51),(0,50),
     (-2,49),(-5,48),(-8,46),(-9,44),(-9,40),(-9,36)],
    # Escandinavia
    [(5,58),(10,57),(12,56),(18,56),(18,57),(20,59),(22,60),
     (26,64),(28,68),(28,71),(24,70),(20,70),(18,70),(16,69),
     (14,68),(15,65),(12,60),(10,58),(5,58)],
    # Africa
    [(-17,15),(-17,21),(-14,24),(-8,30),(-2,32),(0,33),(5,33),
     (10,33),(14,32),(20,31),(25,30),(30,28),(32,25),(37,22),
     (42,12),(44,12),(44,11),(43,7),(42,2),(41,-1),
     (40,-10),(38,-18),(36,-22),(35,-27),(32,-30),(29,-32),
     (27,-34),(25,-34),(20,-35),(18,-34),(17,-32),(14,-28),
     (11,-18),(10,-8),(9,-1),(8,2),(3,4),(1,6),(-2,5),
     (-5,5),(-8,5),(-12,7),(-15,12),(-17,15)],
    # Asia principal
    [(32,31),(35,30),(37,22),(45,12),(50,12),(56,22),(57,22),
     (60,25),(63,27),(66,25),(70,21),(72,20),(74,20),(76,8),
     (80,8),(80,10),(84,5),(88,5),(92,8),(95,10),(98,20),
     (100,2),(104,2),(107,10),(110,12),(116,22),(120,24),
     (122,30),(124,38),(128,38),(130,42),(132,44),(140,42),
     (142,48),(140,54),(138,56),(135,58),(142,60),(140,62),
     (142,67),(140,70),(120,72),(110,73),(100,73),(90,73),
     (80,73),(70,73),(60,73),(50,73),(45,72),(40,72),
     (35,70),(30,70),(28,68),(29,64),(32,58),(32,52),
     (35,48),(36,42),(34,37),(32,34),(35,32),(35,30),(32,31)],
    # Australia
    [(114,-22),(115,-30),(118,-35),(122,-34),(126,-34),(130,-33),
     (135,-35),(138,-36),(141,-38),(148,-38),(151,-34),(153,-28),
     (153,-22),(148,-18),(145,-18),(142,-18),(138,-14),(136,-12),
     (130,-12),(128,-14),(124,-16),(118,-20),(114,-22)],
    # Nueva Zelanda
    [(173,-35),(175,-38),(174,-42),(172,-44),(170,-46)],
    [(172,-43),(174,-41),(176,-37),(175,-36),(173,-35)],
    # Antártida (arco)
    [(-180,-70),(-150,-72),(-120,-72),(-90,-70),(-60,-72),
     (-30,-72),(0,-70),(30,-72),(60,-72),(90,-70),(120,-72),
     (150,-72),(180,-70)],
]

# Proyección
MAP_MX, MAP_MY = 110, 80
def ll2xy(lon, lat):
    x = int(MAP_MX + (lon+180)/360*(W-2*MAP_MX))
    y = int(MAP_MY + (90-lat)/180*(H-2*MAP_MY))
    return x, y

# Pre-calcular segmentos
_CONT_SEGS = []
for poly in CONTINENTS:
    pts = [ll2xy(lo,la) for lo,la in poly]
    for i in range(len(pts)-1):
        if abs(pts[i][0]-pts[i+1][0]) < W//3:
            _CONT_SEGS.append((pts[i], pts[i+1]))

# Zonas
ZONES = [
    ("CÓRDOBA",      "Agro",          -31.4,  -64.2,  GOLD,   70, "⚠", False),
    ("BALCARCE",     "Agro",          -37.9,  -58.3,  GOLD,   70, "✓", True),
    ("VACA MUERTA",  "Energy/O&G",    -38.8,  -68.9,  ORANGE, 42, "✓", False),
    ("ROTTERDAM",    "Maritime",       51.9,    4.5,  BLUE,   38, "✓", False),
    ("PERMIAN",      "Oil & Gas",      31.8, -102.4,  ORANGE, 52, "✓", False),
    ("PILBARA",      "Mining",        -22.0,  118.0,  PURPLE, 41, "✓", False),
    ("AMAZONAS",     "ESG/Deforest.",  -3.5,  -60.0,  TEAL,   66, "✓", False),
    ("INDIANA",      "Agro",           40.3,  -86.1,  GOLD,   38, "⚠", False),
    ("MALACCA",      "Shipping",        2.2,  102.2,  BLUE,   42, "✓", False),
]
_ZXY = [ll2xy(lo,la) for _,_,la,lo,_,_,_,_ in ZONES]

# Scan: fracción del mapa en X para cada zona
_SCAN_FRAC = [(x-MAP_MX)/(W-2*MAP_MX) for x,_ in _ZXY]

# Posición del título (pre-cálculo de chars)
_tmp = Image.new("RGBA",(10,10)); _td = ImageDraw.Draw(_tmp)
_TITLE = "FARO  PROTOCOL"
_TW = tw(_td, _TITLE, FGB100)
_TX0 = (W-_TW)//2
_CHAR_X = []
_cx = 0
for ch in _TITLE:
    _CHAR_X.append(_TX0 + _cx)
    _cx += tw(_td, ch, FGB100)
del _tmp, _td

# ═══════════════════════════════════════════════════════════════
# ESCENA 1 — Intro (0–4s)
# Punto de luz → streak horizontal → letras una por una
# ═══════════════════════════════════════════════════════════════
def make_intro(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    cy  = H//2

    # ── Punto de luz central (0-1.0s) ──────────────────────────
    if t < 1.4:
        pt = ease(min(1., t/0.6))
        # Expansión radial desde el centro
        for r in [60, 40, 20, 8, 3]:
            a_r = pt * (0.04 if r > 20 else (0.12 if r > 8 else 0.5))
            d.ellipse([(W//2-r, cy-r), (W//2+r, cy+r)],
                      fill=(*GOLD, int(255*a_r)))

        # Halo exterior pulsante
        hr = int(lerp(0, 120, ease(min(1., t/0.8))))
        if hr > 0:
            d.ellipse([(W//2-hr, cy-hr), (W//2+hr, cy+hr)],
                      outline=(*GOLD, int(60*pt)), width=1)

    # ── Streak horizontal (0.5-1.5s) ───────────────────────────
    st = t - 0.5
    if st > 0:
        sa = ease(min(1., st/0.3))
        fe = ease(max(0., min(1., 1.0 - (t-1.5)/0.4)))  # fade out del streak
        streak_a = sa * fe

        streak_w = int(lerp(0, W, ease(min(1., st/0.5))))
        x1 = W//2 - streak_w//2
        x2 = W//2 + streak_w//2

        for dy, wa in [(0, 0.9), (-1, 0.4), (1, 0.4), (-2, 0.15), (2, 0.15)]:
            d.line([(x1, cy+dy), (x2, cy+dy)],
                   fill=(*GOLD, int(255*streak_a*wa)), width=1)

    # ── Letras una por una (1.2–3.0s) ──────────────────────────
    title_t = t - 1.2
    chars_per_sec = len(_TITLE) / 1.4  # revela en 1.4s
    ty = cy - 55

    for i, (ch, cx2) in enumerate(zip(_TITLE, _CHAR_X)):
        appear = title_t - i/chars_per_sec
        if appear < 0: continue
        ca = ease(min(1., appear/0.12))
        # Flash inicial + settling
        flash = max(0., 1.0 - appear/0.08)
        col = (int(lerp(GOLD2[0],GOLD[0],min(1.,appear/0.3))),
               int(lerp(GOLD2[1],GOLD[1],min(1.,appear/0.3))),
               int(lerp(GOLD2[2],GOLD[2],min(1.,appear/0.3))))
        d.text((cx2, ty), ch, font=FGB100, fill=(*col, int(255*ca)))
        if flash > 0.01:
            d.text((cx2, ty), ch, font=FGB100, fill=(*WHITE, int(255*flash*0.6)))

    # ── Subtítulo (2.8-4.0s) ────────────────────────────────────
    sub_t = t - 2.8
    if sub_t > 0:
        sub_a = ease(min(1., sub_t/0.4))
        sub   = "Physical Truth from Orbit"
        cx_text(d, sub, cy+65, FM32, WHITE, int(220*sub_a))
        hline(d, cy+115, W//2-220, W//2+220, GOLD, 0.3*sub_a)

    # ── Fade in del fondo (0-0.4s) ─────────────────────────────
    if t < 0.4:
        fade = 1.0 - ease(t/0.4)
        black = Image.new("RGBA", (W, H), (0, 0, 0, int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S1 = VideoClip(make_intro, duration=4)

# ═══════════════════════════════════════════════════════════════
# ESCENA 2 — Mapa global (4–18s → 14s)
# Continentes dorados · radar scan · sonar pings · HUD
# ═══════════════════════════════════════════════════════════════
SCAN_START = 0.5
SCAN_DUR   = 4.5   # el scan dura 4.5s

def make_map(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")

    # Fade in escena
    a_scene = ease(min(1., t/0.3))

    # ── Continentes ───────────────────────────────────────────
    a_cont = ease(min(1., t/0.5)) * a_scene
    # Brillo base de los continentes (dorado tenue)
    for (x1,y1),(x2,y2) in _CONT_SEGS:
        d.line([(x1,y1),(x2,y2)], fill=(*GOLD3, int(55*a_cont)), width=1)

    # Versión más brillante de los continentes ya escaneados
    scan_prog = min(1., max(0., (t-SCAN_START)/SCAN_DUR))
    x_scan    = int(MAP_MX + scan_prog*(W-2*MAP_MX))

    for (x1,y1),(x2,y2) in _CONT_SEGS:
        if x1 < x_scan or x2 < x_scan:
            xmax = max(x1,x2)
            # Brillo proporcional a cuánto fue escaneado
            reveal = min(1., max(0., (x_scan - xmax)/60))
            if reveal > 0:
                d.line([(x1,y1),(x2,y2)],
                       fill=(*GOLD, int(45*reveal*a_cont)), width=1)

    # ── Radar scan line ───────────────────────────────────────
    if SCAN_START < t < SCAN_START + SCAN_DUR + 0.5:
        sa = min(1., max(0., 1.0-(t-SCAN_START-SCAN_DUR)/0.5))
        # Estela (trail) verde a la izquierda del scan
        for dx in range(80, 0, -2):
            trail_x = x_scan - dx
            if trail_x < MAP_MX: continue
            ta = int(28 * (1-dx/80) * sa)
            d.line([(trail_x, MAP_MY), (trail_x, H-MAP_MY)],
                   fill=(*GREEN, ta), width=1)
        # Línea principal
        pulse_s = 0.6 + 0.4*math.sin(t*40)
        d.line([(x_scan, MAP_MY),(x_scan, H-MAP_MY)],
               fill=(*GREEN, int(220*sa*pulse_s)), width=2)
        # Brillo lateral
        for dx in (1,2,3,5,8):
            ga = int(70*(1-dx/9)*sa)
            for sx in (x_scan-dx, x_scan+dx):
                if MAP_MX < sx < W-MAP_MX:
                    d.line([(sx, MAP_MY),(sx, H-MAP_MY)],
                           fill=(*GREEN, ga), width=1)

    # ── Zonas ─────────────────────────────────────────────────
    for i, (name, sector, lat, lon, color, score, status, _) in enumerate(ZONES):
        zx, zy = _ZXY[i]
        frac   = _SCAN_FRAC[i]
        age    = (scan_prog - frac) * SCAN_DUR

        if scan_prog < frac - 0.03: continue
        a_zone = ease(min(1., age/0.3))

        # Sonar rings — 2 rings por zona en diferentes fases
        for ring_idx in range(2):
            phase    = ring_idx * 0.9 + i * 0.4
            ring_cyc = (t - SCAN_START + phase) % 2.2
            rp       = ring_cyc / 2.2
            rr       = int(rp * 52)
            ra       = int(200*(1-rp)*a_zone*0.55)
            if ra > 0 and rr > 2:
                d.ellipse([(zx-rr,zy-rr),(zx+rr,zy+rr)],
                          outline=(*color, ra), width=1)

        # Crosshair (aparece solo al inicio)
        ch_age = min(1., age/0.6)
        if ch_age < 1:
            crosshair(d, zx, zy, 22, color, (1-ch_age)*0.8*a_zone)

        # Dot central
        dot(d, zx, zy, 5, color, a_zone*0.9)
        dot(d, zx, zy, 2, WHITE, a_zone*0.85)

        # Label
        if age > 0.25:
            la = ease(min(1., (age-0.25)/0.25))
            lw = tw(d, name, FM14)
            lx = zx - lw//2

            # Posicionar arriba/abajo según hemisferio
            offset_y = -28 if zy < H//2 else 16

            bg_box = (*BG, int(200*la))
            d.rectangle([(lx-4,zy+offset_y-2),(lx+lw+4,zy+offset_y+18)], fill=bg_box)
            d.text((lx, zy+offset_y), name, font=FM14,
                   fill=(*color, int(255*la)))

            if age > 0.6:
                sa2   = ease(min(1., (age-0.6)/0.3))
                s_col = GREEN if "✓" in status else ORANGE
                stxt  = f"Score {score}"
                sw    = tw(d, stxt, FM14)
                sx2   = zx - sw//2
                oy2   = offset_y + (18 if offset_y < 0 else -18)
                d.rectangle([(sx2-4,zy+oy2-2),(sx2+sw+4,zy+oy2+16)],
                             fill=(*BG, int(180*sa2)))
                d.text((sx2, zy+oy2), stxt, font=FM14,
                       fill=(*s_col, int(220*sa2)))

    # ── HUD frame ─────────────────────────────────────────────
    hline(d, 68, 80, W-80, GOLD, 0.2*a_scene)
    hline(d, H-68, 80, W-80, GOLD, 0.2*a_scene)

    d.text((85, 38), "FARO PROTOCOL  /  GLOBAL ASSET MONITORING",
           font=FM18, fill=(*GOLD, int(160*a_scene)))

    n_vis = sum(1 for i in range(len(ZONES))
                if (scan_prog - _SCAN_FRAC[i]) > 0)
    d.text((W-85-tw(d,f"{n_vis}/{len(ZONES)} ACTIVE",FM18), 38),
           f"{n_vis}/{len(ZONES)} ACTIVE",
           font=FM18, fill=(*GREEN, int(160*a_scene)))

    # Coordenadas de zona en esquina
    ts_txt = "SAR + NDVI  /  SENTINEL-1A  /  SENTINEL-2"
    d.text((85, H-55), ts_txt, font=FM14, fill=(*GOLD, int(120*a_scene)))

    # Fade in al inicio
    if t < 0.3:
        fade = 1.0 - ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S2 = VideoClip(make_map, duration=14)

# ═══════════════════════════════════════════════════════════════
# ESCENA 3 — Reporte Balcarce (18–30s → 12s)
# Imagen real + contadores animados
# ═══════════════════════════════════════════════════════════════
_bal_raw = Image.open(os.path.join(ROOT, "faro_reporte_fusion_balcarce.png")).convert("RGB")
# Escalar al 55% del ancho, centrado a la derecha
_RW  = int(W*0.55); _RH2 = H-110
_rsc = min(_RW/_bal_raw.width, _RH2/_bal_raw.height)
_RIW = int(_bal_raw.width*_rsc); _RIH = int(_bal_raw.height*_rsc)
_bal = _bal_raw.resize((_RIW, _RIH), Image.LANCZOS)
_RX  = W - _RIW - 20
_RY  = (H - _RIH)//2

BSTATS = [
    ("NDVI",       0.531, "{:.3f}", TEAL),
    ("SAR",       -13.1,  "{:.1f} dB", BLUE),
    ("Score",      70.0,  "{:.0f}",   GREEN),
    ("Rinde est.", 3.32,  "{:.2f} t/ha", GREEN),
]

def count_up(target, fmt, t, dur=1.8, start_delay=0.):
    lt = t - start_delay
    if lt < 0: return fmt.format(0 if target >= 0 else -0.0)
    p = ease(min(1., lt/dur))
    v = target * p
    return fmt.format(v)

def make_balcarce(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    scanline_overlay(d, 0.02)

    # Imagen desliza desde derecha
    slide = ease(min(1., t/0.35))
    ix    = int(lerp(W+60, _RX, slide))

    # Oscurecer imagen ligeramente para que los datos resalten
    bal_dimmed = _bal.copy()
    overlay_dim = Image.new("RGBA", _bal.size, (0,0,0,40))
    bal_dimmed.paste(overlay_dim, mask=overlay_dim)

    rep = Image.new("RGBA", (W, H), (0,0,0,0))
    rep.paste(bal_dimmed, (ix, _RY))
    img = Image.alpha_composite(img, rep)
    d   = ImageDraw.Draw(img, "RGBA")

    # Corner brackets alrededor de la imagen
    br_a = ease(min(1., (t-0.2)/0.3))
    corner_brackets(d, ix, _RY, ix+_RIW, _RY+_RIH, GOLD, 0.6*br_a, 30)

    PX = 55; PW = _RX - 75

    # Header
    hline(d, 95, PX, PX+PW, GOLD, 0.35)
    d.text((PX, 40), "FARO PROTOCOL  /  CERTIFIED FIELD REPORT",
           font=FM18, fill=(*GOLD, 170))

    # Nombre zona — letras por letra
    zone_t = t - 0.1
    zone_name = "BALCARCE"
    chars_per_sec2 = len(zone_name)/0.5
    for i, ch in enumerate(zone_name):
        ap = zone_t - i/chars_per_sec2
        if ap < 0: continue
        ca = ease(min(1., ap/0.1))
        # calc x (simple, letra por letra)
        prefix_w = tw(d, zone_name[:i], FGB72)
        d.text((PX + prefix_w, 112), ch,
               font=FGB72, fill=(*GOLD, int(255*ca)))

    na = ease(min(1., (t-0.3)/0.25))
    d.text((PX, 200), "Marcos Juarez  /  Cordoba  /  Argentina",
           font=FA22, fill=(*WHITE, int(180*na)))
    d.text((PX, 230), "VERTICAL: AGRICULTURE  /  SENTINEL-1A + 2",
           font=FM18, fill=(*TEAL, int(160*na)))

    hline(d, 265, PX, PX+PW, GOLD, 0.25*na)

    # Stats con contador
    SY = 285; ROW_H = (H-SY-90)//len(BSTATS)
    for i, (label, target, fmt, color) in enumerate(BSTATS):
        ta = ease(min(1., (t-0.4-i*0.2)/0.25))
        if ta <= 0: continue
        ry = SY + i*ROW_H

        # Barra de acento
        d.rectangle([(PX, ry), (PX+3, ry+ROW_H-12)],
                    fill=(*color, int(200*ta)))

        d.text((PX+16, ry+4), label.upper(),
               font=FM18, fill=(*WHITE, int(180*ta)))

        val_str = count_up(target, fmt, t, 1.6, 0.5+i*0.2)
        val_w   = tw(d, val_str, FM44)
        d.text((PX+16, ry+26), val_str,
               font=FM44, fill=(*color, int(255*ta)))

        # Unidad separada en gris si es numérico
        hline(d, ry+ROW_H-14, PX, PX+PW-10, GOLD, 0.12*ta)

    # SHA badge bottom
    sha_t = t - 3.5
    if sha_t > 0:
        sha_a = ease(min(1., sha_t/0.3))
        BY = H - 95
        d.rectangle([(PX, BY),(PX+PW,BY+55)], fill=(*BG, int(230*sha_a)))
        d.rectangle([(PX, BY),(PX+PW,BY+55)],
                    outline=(*GOLD, int(70*sha_a)), width=1)
        d.text((PX+12, BY+8), "SHA-256 SEALED  -  Faro Protocol 2026",
               font=FM14, fill=(*GOLD, int(255*sha_a*0.85)))
        d.text((PX+12, BY+30), "Proof of data integrity before market open",
               font=FM14, fill=(*WHITE, int(160*sha_a)))

    # Fade in
    if t < 0.3:
        fade = 1.0-ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S3 = VideoClip(make_balcarce, duration=12)

# ═══════════════════════════════════════════════════════════════
# ESCENA 4 — SHA-256 reveal (30–42s → 12s)
# Chars ciclando → bloqueos → timestamp → badge VERIFIED
# ═══════════════════════════════════════════════════════════════
_HEX_CHARS = "0123456789abcdef"
_RNG = random.Random(42)

# Pre-generar rain: 48 columnas de fondo
_RAIN_COLS = 48
_RAIN_CW   = W // _RAIN_COLS
_RAIN_DATA = []
for ci in range(_RAIN_COLS):
    speed  = _RNG.uniform(5, 14)   # chars/sec
    offset = _RNG.uniform(-4, 0)   # fase
    chars  = [_RNG.choice(_HEX_CHARS) for _ in range(40)]
    length = _RNG.randint(8, 20)
    _RAIN_DATA.append((speed, offset, chars, length))

def draw_rain(d, t, alpha_mult=1.0):
    """Rain de hex en el fondo — efecto muy sutil"""
    for ci, (speed, offset, chars, length) in enumerate(_RAIN_DATA):
        cx2 = ci * _RAIN_CW + 8
        # posición de la cabeza
        head_f = (offset + speed * t) % (H // 16 + length)
        head_y = int(head_f * 16)
        for j in range(length):
            cy2 = head_y - j*16
            if cy2 < 0 or cy2 > H: continue
            brightness = max(0., 1 - j/length)
            if j == 0:
                col_c = (*GOLD2, int(180*brightness*alpha_mult))
            else:
                col_c = (*GOLD3, int(90*brightness*alpha_mult))
            ch = chars[(int(head_f) + j) % len(chars)]
            d.text((cx2, cy2), ch, font=FM12, fill=col_c)

def sha_char_at(pos, t):
    """Char que aparece en la posición pos del SHA256"""
    lock_t = pos * 0.14   # cada char se bloquea 140ms después
    lt     = t - lock_t
    if lt < 0: return None, 0.
    if lt < 0.25:
        # Ciclando rápido antes de bloquearse
        cycle  = int(lt * 28) % 16
        return _HEX_CHARS[cycle], min(1., lt/0.08)
    else:
        return SHA256[pos], 1.0

def make_sha(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")

    # Rain progresivo: aparece en los primeros 1.5s, luego se desvanece al final
    if t < 1.5:
        rain_a = ease(t/1.5)
    elif t > 9.5:
        rain_a = ease(max(0.,(11.-t)/1.5))
    else:
        rain_a = 1.0
    draw_rain(d, t, rain_a * 0.55)

    scanline_overlay(d, 0.015)

    # SHA-256 reveal (empieza en t=0.8)
    sha_t = t - 0.8
    a_g   = ease(min(1., sha_t/0.3)) if sha_t > 0 else 0.

    if a_g > 0:
        # Header
        hline(d, 85, 200, W-200, GOLD, 0.3*a_g)
        cx_text(d, "CRYPTOGRAPHIC PROOF OF AUTHENTICITY", 46, FM18,
                GOLD, int(160*a_g))

        # Bloque SHA-256 centrado
        CHAR_W = 30   # ancho de cada char monospace a FM24
        LINE1 = SHA256[:32]; LINE2 = SHA256[32:]
        TOTAL_W1 = len(LINE1)*CHAR_W
        TOTAL_W2 = len(LINE2)*CHAR_W
        x1_start = (W - TOTAL_W1)//2
        x2_start = (W - TOTAL_W2)//2
        y_sha1   = H//2 - 85
        y_sha2   = y_sha1 + 60

        # Label SHA-256
        lbl = "SHA-256"
        lw  = tw(d, lbl, FM24)
        d.text(((W-lw)//2, y_sha1-38), lbl,
               font=FM24, fill=(*GREEN, int(200*a_g)))

        hline(d, y_sha1-10, W//2-250, W//2+250, GREEN, 0.3*a_g)

        # Dibujar cada caracter
        for pos in range(len(SHA256)):
            ch, ca = sha_char_at(pos, sha_t)
            if ch is None: continue

            row = 0 if pos < 32 else 1
            col = pos if pos < 32 else pos-32
            x   = (x1_start if row == 0 else x2_start) + col*CHAR_W
            y   = y_sha1 if row == 0 else y_sha2

            # Color: ciclando = gold tenue, bloqueado = green brillante
            locked = (ch == SHA256[pos] and ca >= 1.0)
            col_c  = GREEN if locked else GOLD2
            d.text((x, y), ch, font=FM24,
                   fill=(*col_c, int(255*ca*a_g)))

            # Flash en el momento de bloqueo
            lock_age = sha_t - pos*0.14 - 0.25
            if 0 < lock_age < 0.08:
                fa = (1 - lock_age/0.08)*0.7
                d.text((x, y), ch, font=FM24,
                       fill=(*WHITE, int(255*fa*a_g)))

        # Línea de separación entre las dos líneas
        hline(d, y_sha2+50, W//2-250, W//2+250, GREEN, 0.2*a_g)

        # Timestamp (aparece cuando el hash está completo ~64*0.14 = 9s)
        ts_appear = sha_t - (64*0.14 + 0.3)
        if ts_appear > 0:
            ta = ease(min(1., ts_appear/0.4))

            ts_w = tw(d, TIMESTAMP, FM32)
            d.text(((W-ts_w)//2, y_sha2+70), TIMESTAMP,
                   font=FM32, fill=(*GOLD2, int(220*ta)))

            # VERIFIED badge
            badge_t = ts_appear - 0.5
            if badge_t > 0:
                ba = ease(min(1., badge_t/0.35))
                bw, bh = 380, 54
                bx = (W-bw)//2; by = y_sha2 + 120

                d.rectangle([(bx,by),(bx+bw,by+bh)],
                             fill=(*BG, int(240*ba)))
                d.rectangle([(bx,by),(bx+bw,by+bh)],
                             outline=(*GREEN, int(180*ba)), width=1)
                badge_txt = "VERIFIED  /  BLOCKCHAIN ANCHORED"
                bw2 = tw(d, badge_txt, FM18)
                d.text(((W-bw2)//2, by+14), badge_txt,
                       font=FM18, fill=(*GREEN, int(255*ba)))

    # Fade in
    if t < 0.3:
        fade = 1.0-ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S4 = VideoClip(make_sha, duration=12)

# ═══════════════════════════════════════════════════════════════
# ESCENA 5 — Tres frases de impacto (42–52s → 10s)
# Con motion blur horizontal simulado
# ═══════════════════════════════════════════════════════════════
PHRASES = [
    ("48 hours before",   "official data.",   GOLD),
    ("Cryptographically", "verified.",         GREEN),
    ("Any asset.",        "Any location.\nAny sector.", BLUE),
]

def draw_blur_text(d, text, x, y, font, color, alpha, blur=12, copies=7):
    """Texto con motion blur horizontal simulado"""
    for i in range(copies):
        ox    = int(lerp(blur, 0, (i+1)/copies))
        alpha_i = int(alpha * (i+1)/copies * 0.7)
        d.text((x+ox, y), text, font=font, fill=(*color, alpha_i))
    d.text((x, y), text, font=font, fill=(*color, int(alpha)))

def make_phrases(t):
    pd    = 10.0 / len(PHRASES)
    pi    = min(int(t/pd), len(PHRASES)-1)
    lt    = t - pi*pd

    img   = bg()
    d     = ImageDraw.Draw(img, "RGBA")
    scanline_overlay(d, 0.018)

    line1, line2, color = PHRASES[pi]

    # Alpha de la frase
    if   lt < 0.25: a = ease(lt/0.25)
    elif lt > pd-0.25: a = ease((pd-lt)/0.25)
    else: a = 1.0

    # Motion blur decrece con el tiempo (el blur "se frena")
    blur_t = max(0., 0.4 - lt)
    blur_px = int(blur_t * 55)

    # Número de frase
    num_str = f"0{pi+1} / 0{len(PHRASES)}"
    d.text((85, 48), num_str, font=FM18,
           fill=(*GOLD, int(200*a*0.5)))

    cy_base = H//2 - 60

    # Línea 1 (grande)
    x1 = (W - tw(d, line1, FGB72))//2
    draw_blur_text(d, line1, x1, cy_base, FGB72, color,
                   int(255*a), blur_px, 8)

    # Línea 2 (grande)
    lines2 = line2.split("\n")
    y_off = th(d, line1, FGB72) + 18
    for i2, ln in enumerate(lines2):
        x2  = (W - tw(d, ln, FG60))//2
        a2  = ease(min(1., (lt-0.07-i2*0.06)/0.2))
        draw_blur_text(d, ln, x2, cy_base+y_off, FG60, color,
                       int(255*a*a2), blur_px//2, 6)
        y_off += th(d, ln, FG60) + 12

    # Línea decorativa
    line_a = ease(min(1., (lt-0.15)/0.2))
    hline(d, cy_base - 32, W//2-160, W//2+160, color, 0.35*a*line_a)
    hline(d, cy_base + y_off + 10, W//2-100, W//2+100, color, 0.2*a*line_a)

    # Progress dots
    for di in range(len(PHRASES)):
        dx2 = W//2 + (di-1)*30
        col = color if di == pi else DIM
        aa  = 0.85 if di == pi else 0.4
        d.ellipse([(dx2-5,H-52-5),(dx2+5,H-52+5)],
                  fill=(*col, int(255*aa)))

    # Fade in
    if t < 0.25:
        fade = 1.0-ease(t/0.25)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S5 = VideoClip(make_phrases, duration=10)

# ═══════════════════════════════════════════════════════════════
# ESCENA 6 — 6 sectores en grid con flash (52–58s → 6s)
# ═══════════════════════════════════════════════════════════════
SECTORS = [
    ("AGRICULTURE",    "Crops · Yield · Stress index",    GOLD,   "01"),
    ("ENERGY / O&G",   "Permian · Vaca Muerta",           ORANGE, "02"),
    ("MARITIME",       "Rotterdam · Port activity",        BLUE,   "03"),
    ("MINING",         "Pilbara · Iron ore · REE",         PURPLE, "04"),
    ("ESG / DEFOREST.","Amazonas · Carbon · Land use",    TEAL,   "05"),
    ("SHIPPING",       "Malacca · Strait · Freight",       BLUE,   "06"),
]

def make_sectors(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    scanline_overlay(d, 0.02)

    a_hdr = ease(min(1., t/0.2))
    cx_text(d, "SECTOR COVERAGE", 45, FG48, GOLD, int(255*a_hdr))
    hline(d, 108, 160, W-160, GOLD, 0.25*a_hdr)

    cols, rows = 3, 2
    CW = (W-120)//cols; CH = (H-165)//rows
    interval = 6.0/len(SECTORS)

    for i, (name, desc, color, num) in enumerate(SECTORS):
        col = i%cols; row = i//cols
        age = t - i*interval
        if age < 0: continue

        ta = ease(min(1., age/0.2))
        bx1 = 60 + col*CW + 18
        by1 = 128 + row*CH + 14
        bx2 = bx1 + CW - 36
        by2 = by1 + CH - 28

        # Flash inicial
        flash = max(0., 1.0 - age/0.12)

        # Box background
        d.rectangle([(bx1,by1),(bx2,by2)], fill=(*BG, int(250*ta)))

        # Flash overlay
        if flash > 0:
            d.rectangle([(bx1,by1),(bx2,by2)],
                        fill=(*color, int(80*flash)))

        # Borde
        d.rectangle([(bx1,by1),(bx2,by2)],
                    outline=(*color, int(65*ta)), width=1)
        # Acento izquierdo
        d.rectangle([(bx1,by1),(bx1+4,by2)],
                    fill=(*color, int(210*ta)))
        # Corner brackets
        corner_brackets(d, bx1, by1, bx2, by2, color, 0.6*ta, 16)

        # Número sector
        d.text((bx2-tw(d,num,FM18)-12, by1+10), num,
               font=FM18, fill=(*color, int(80*ta)))

        # Nombre
        nw  = tw(d, name, FG28)
        nx2 = bx1 + 18
        d.text((nx2, by1+16), name, font=FG28, fill=(*color, int(255*ta)))

        # Desc
        d.text((nx2, by1+58), desc, font=FM18, fill=(*WHITE, int(160*ta)))

        # Status dot verde
        da = ease(min(1., (age-0.15)/0.2))
        if da > 0:
            dot(d, bx2-22, by1+22, 5, GREEN, da*0.9)
            dot(d, bx2-22, by1+22, 2, WHITE, da*0.8)

    # Fade in
    if t < 0.2:
        fade = 1.0-ease(t/0.2)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S6 = VideoClip(make_sectors, duration=6)

# ═══════════════════════════════════════════════════════════════
# ESCENA 7 — Outro (58–60s → 2s)
# Logo + URL + fade negro
# ═══════════════════════════════════════════════════════════════
URL = "faro-protocol.netlify.app"

def make_outro(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    cy  = H//2

    if t < 0.4:   a = ease(t/0.4)
    elif t > 1.4: a = ease((2.-t)/0.6)
    else:         a = 1.0

    pulse = 0.5 + 0.5*math.sin(t*4)

    hline(d, cy-92, W//2-380, W//2+380, GOLD, 0.28*a)

    title = "FARO  PROTOCOL"
    tw2 = tw(d, title, FGB100)
    d.text(((W-tw2)//2, cy-78), title, font=FGB100, fill=(*GOLD, int(255*a)))

    sub = "Physical Truth from Orbit"
    cx_text(d, sub, cy+42, FG28, WHITE, int(220*a*0.85))

    hline(d, cy+92, W//2-280, W//2+280, GOLD, 0.25*a)

    url_a = ease(min(1., (t-0.3)/0.3))
    cx_text(d, URL, cy+118, FG28, GOLD2, int(255*a*url_a))

    tag = "SAR  /  NDVI  /  SHA-256  /  24h delivery  /  Any location"
    cx_text(d, tag, cy+165, FM18, WHITE, int(150*a*url_a*0.7))

    dot(d, W//2, cy+212, 4, GOLD, a*(0.35+0.3*pulse))

    # Fade a negro
    if t > 1.2:
        fade = ease((t-1.2)/0.8)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S7 = VideoClip(make_outro, duration=2)

# ═══════════════════════════════════════════════════════════════
# ENSAMBLADO
# ═══════════════════════════════════════════════════════════════
def main():
    os.makedirs(os.path.join(ROOT,"outputs"), exist_ok=True)

    clips  = [S1,S2,S3,S4,S5,S6,S7]
    durs   = [c.duration for c in clips]
    total  = sum(durs)
    labels = ["Intro","Mapa","Balcarce","SHA-256","Frases","Sectores","Outro"]

    print("Escenas:")
    t0 = 0
    for lbl, dur in zip(labels, durs):
        print(f"  {lbl:12s} {t0:5.1f}s  -{t0+dur:5.1f}s  ({dur:.0f}s)")
        t0 += dur
    print(f"Total: {total:.0f}s  /  {int(total*FPS)} frames")
    print(f"Exportando -> {OUT}")

    final = concatenate_videoclips(clips)
    final.write_videofile(
        OUT, fps=FPS, codec="libx264", audio=False,
        preset="medium",
        ffmpeg_params=["-crf","18","-pix_fmt","yuv420p"],
        logger="bar",
    )
    sz = os.path.getsize(OUT)/1e6
    print(f"[OK] {sz:.1f} MB  ->  {OUT}")

if __name__ == "__main__":
    main()
