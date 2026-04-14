#!/usr/bin/env python3
"""
faro_demo_v2.py  -  outputs/faro_demo_v2.mp4
60s - 1920x1080 - 30fps - no audio
Visual: AZ satellite intel + Bloomberg terminal
"""
import os, math, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import VideoClip, concatenate_videoclips

W, H  = 1920, 1080
FPS   = 30
ROOT  = os.path.dirname(os.path.abspath(__file__))
OUT   = os.path.join(ROOT, "outputs", "faro_demo_v2.mp4")

# Paleta — CYAN nuevo (az2/az3/az4 reference)
BG     = (10,  10,  26)
GOLD   = (201, 168, 76)
GOLD2  = (226, 201, 126)
GOLD3  = (140, 110, 45)
CYAN   = (0,   212, 255)
CYAN2  = (0,   140, 175)
GREEN  = (0,   255, 65)
GREEN2 = (45,  190, 80)
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

FGB100 = _fnt("georgiab.ttf", 100)
FGB72  = _fnt("georgiab.ttf",  72)
FG60   = _fnt("georgia.ttf",   60)
FG48   = _fnt("georgia.ttf",   48)
FG36   = _fnt("georgia.ttf",   36)
FG28   = _fnt("georgia.ttf",   28)
FM44   = _fnt("consola.ttf",   44)
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
    t = max(0., min(1., t)); return t*t*(3-2*t)

def ease_out(t):
    t = max(0., min(1., t)); return 1-(1-t)**3

def lerp(a, b, t):
    return a + (b-a)*max(0., min(1., t))

def bg():
    return Image.new("RGBA", (W, H), (*BG, 255))

def npf(img):
    return np.array(img.convert("RGB"))

def tw(d, txt, fnt):
    b = d.textbbox((0,0), txt, font=fnt); return b[2]-b[0]

def th(d, txt, fnt):
    b = d.textbbox((0,0), txt, font=fnt); return b[3]-b[1]

def cx_text(d, txt, y, fnt, col, a=255):
    w2 = tw(d, txt, fnt)
    d.text(((W-w2)//2, y), txt, font=fnt, fill=(*col[:3], a))

def hline(d, y, x1=80, x2=None, col=GOLD, a=0.35, w=1):
    if x2 is None: x2 = W-80
    d.line([(x1,y),(x2,y)], fill=(*col, int(255*a)), width=w)

def vline(d, x, y1=0, y2=None, col=GOLD, a=0.25, w=1):
    if y2 is None: y2 = H
    d.line([(x,y1),(x,y2)], fill=(*col, int(255*a)), width=w)

def ring_draw(d, cx, cy, r, col, a, w=1):
    d.ellipse([(cx-r,cy-r),(cx+r,cy+r)], outline=(*col, int(255*a)), width=w)

def dot(d, cx, cy, r, col, a):
    d.ellipse([(cx-r,cy-r),(cx+r,cy+r)], fill=(*col, int(255*a)))

def crosshair(d, cx, cy, size, col, a):
    s, g = size, size//3
    d.line([(cx-s,cy),(cx-g,cy)], fill=(*col,int(255*a)), width=1)
    d.line([(cx+g,cy),(cx+s,cy)], fill=(*col,int(255*a)), width=1)
    d.line([(cx,cy-s),(cx,cy-g)], fill=(*col,int(255*a)), width=1)
    d.line([(cx,cy+g),(cx,cy+s)], fill=(*col,int(255*a)), width=1)

def corner_brackets(d, x1, y1, x2, y2, col, a, sz=20):
    c = (*col, int(255*a)); lw = 1
    d.line([(x1,y1),(x1+sz,y1)], fill=c, width=lw)
    d.line([(x1,y1),(x1,y1+sz)], fill=c, width=lw)
    d.line([(x2,y1),(x2-sz,y1)], fill=c, width=lw)
    d.line([(x2,y1),(x2,y1+sz)], fill=c, width=lw)
    d.line([(x1,y2),(x1+sz,y2)], fill=c, width=lw)
    d.line([(x1,y2),(x1,y2-sz)], fill=c, width=lw)
    d.line([(x2,y2),(x2-sz,y2)], fill=c, width=lw)
    d.line([(x2,y2),(x2,y2-sz)], fill=c, width=lw)

def scanline_overlay(d, a=0.025):
    c = (*GREEN, int(255*a))
    for y in range(0, H, 4):
        d.line([(0,y),(W,y)], fill=c, width=1)

def draw_mini_chart(d, x, y, w2, h2, data_pts, color, a):
    """Mini sparkline chart - az4 style"""
    if len(data_pts) < 2: return
    mn, mx = min(data_pts), max(data_pts)
    rng = (mx-mn) if mx != mn else 1.0
    d.rectangle([(x,y),(x+w2,y+h2)], fill=(*BG, int(200*a)))
    d.rectangle([(x,y),(x+w2,y+h2)], outline=(*color, int(55*a)), width=1)
    step = w2 / (len(data_pts)-1)
    for i in range(len(data_pts)-1):
        x1b = int(x + i*step)
        x2b = int(x + (i+1)*step)
        y1b = int(y + h2 - (data_pts[i]-mn)/rng*(h2-6) - 3)
        y2b = int(y + h2 - (data_pts[i+1]-mn)/rng*(h2-6) - 3)
        d.line([(x1b,y1b),(x2b,y2b)], fill=(*color, int(200*a)), width=1)

# ═══════════════════════════════════════════════════════════════
# NUEVAS FUNCIONES — AZ REFERENCE STYLE
# ═══════════════════════════════════════════════════════════════

def draw_orbital_arcs(d, cx, cy, t, a):
    """Arcos orbitales girando alrededor del centro — az1 style"""
    for rx, ry, spd, phase, delay in [
        (390, 28,  0.30,  0.0, 0.00),
        (310, 22, -0.22,  2.1, 0.15),
        (240, 16,  0.45,  4.3, 0.30),
    ]:
        arc_a = ease(min(1., (t-delay)/0.5)) * a
        if arc_a <= 0.01: continue
        angle = t * spd * math.pi + phase
        pts = []
        for deg in range(0, 361, 3):
            r = math.radians(deg)
            ex = rx * math.cos(r)
            ey = ry * math.sin(r)
            # Tilt + spin
            tilt = 0.26
            ex2 = ex * math.cos(angle+tilt) - ey * math.sin(angle+tilt)
            ey2 = ex * math.sin(angle+tilt) + ey * math.cos(angle+tilt)
            pts.append((int(cx+ex2), int(cy+ey2)))
        for j in range(len(pts)-1):
            seg_pct = j / len(pts)
            bright = 0.10 + 0.40 * (0.5 + 0.5*math.sin(seg_pct*math.pi*2 - angle*2))
            d.line([pts[j], pts[j+1]], fill=(*CYAN, int(255*arc_a*bright)), width=1)
        # Glowing dot at head
        head_i = int(((angle % (2*math.pi)) / (2*math.pi)) * len(pts)) % len(pts)
        hpt = pts[head_i]
        dot(d, hpt[0], hpt[1], 2, CYAN, arc_a*0.9)


def draw_hud_rings(d, cx, cy, t, a):
    """Grandes anillos HUD concentricos — az2 style"""
    for r, rot_spd, la in [
        (160,  0.14, 0.70),
        (260, -0.09, 0.50),
        (375,  0.06, 0.30),
    ]:
        d.ellipse([(cx-r,cy-r),(cx+r,cy+r)],
                  outline=(*CYAN, int(255*a*la)), width=1)
        angle = t * rot_spd * math.pi
        for k in range(4):
            ang = angle + k * math.pi/2
            ix1 = int(cx + r * math.cos(ang))
            iy1 = int(cy + r * math.sin(ang))
            ix2 = int(cx + (r+12) * math.cos(ang))
            iy2 = int(cy + (r+12) * math.sin(ang))
            d.line([(ix1,iy1),(ix2,iy2)], fill=(*CYAN, int(255*a*la*2.0)), width=1)

    # Crosshair sutil de pantalla completa
    MAP_MX2 = 110
    d.line([(MAP_MX2, cy),(W-MAP_MX2, cy)], fill=(*CYAN, int(255*a*0.15)), width=1)
    d.line([(cx, 80),(cx, H-80)],            fill=(*CYAN, int(255*a*0.15)), width=1)

    # Labels
    tgt = "ACQUIRING TARGET"
    if a > 0.7:
        tgt = "TARGET ACQUIRED"
    tgtw = tw(d, tgt, FM14)
    d.text((cx-tgtw//2, cy-390), tgt, font=FM14,
           fill=(*CYAN, int(180*a)))
    coord = "37.9 S  58.3 W  /  BALCARCE"
    cw2 = tw(d, coord, FM14)
    d.text((cx-cw2//2, cy-370), coord, font=FM14,
           fill=(*CYAN, int(130*a)))
    acq = "ACTIVE MONITORING  /  SENTINEL-1A  +  S2"
    aqw = tw(d, acq, FM14)
    d.text((cx-aqw//2, cy+388), acq, font=FM14,
           fill=(*CYAN, int(180*a)))


# ═══════════════════════════════════════════════════════════════
# DATOS
# ═══════════════════════════════════════════════════════════════
SHA256    = "99fa9be2368cc7c9265b917d5f1b9148e3e86d6e97ba461d5b795a5fcb28c9f9"
TIMESTAMP = "2026-04-04  08:32:17 UTC"

CONTINENTS = [
    [(-168,71),(-165,64),(-162,60),(-153,58),(-148,61),(-141,60),
     (-131,55),(-127,50),(-124,47),(-124,37),(-118,33),(-112,28),
     (-106,24),(-97,20),(-90,16),(-85,11),(-79,9),
     (-84,9),(-87,16),(-90,21),(-97,26),(-90,30),(-82,30),(-81,31),
     (-75,35),(-74,41),(-70,42),(-66,44),(-60,47),(-53,47),
     (-55,50),(-60,47),(-65,57),(-68,62),(-73,68),(-80,73),
     (-90,73),(-100,73),(-110,72),(-125,72),(-140,70),(-157,70),(-168,71)],
    [(-65,76),(-55,75),(-45,76),(-38,78),(-25,76),(-20,72),
     (-23,68),(-28,65),(-37,63),(-43,63),(-52,65),(-57,66),(-62,67),(-65,71),(-65,76)],
    [(-79,9),(-77,10),(-74,12),(-62,11),(-60,8),(-52,5),
     (-49,0),(-35,-5),(-35,-10),(-37,-14),(-40,-20),(-43,-23),
     (-48,-25),(-52,-32),(-52,-33),(-57,-38),(-62,-43),
     (-65,-47),(-68,-53),(-72,-55),(-68,-56),(-65,-55),
     (-57,-51),(-53,-33),(-49,-28),(-40,-20),(-35,-8),
     (-35,-5),(-50,2),(-59,5),(-60,8),(-62,11),(-72,12),(-79,9)],
    [(-9,36),(-5,36),(0,39),(3,41),(4,44),(7,44),(8,47),(10,47),
     (13,46),(14,41),(15,41),(18,40),(20,38),(23,38),(26,39),
     (28,41),(30,43),(29,46),(28,55),(24,57),(20,59),(18,60),
     (15,58),(12,56),(10,57),(8,55),(5,52),(2,51),(0,50),
     (-2,49),(-5,48),(-8,46),(-9,44),(-9,40),(-9,36)],
    [(5,58),(10,57),(12,56),(18,56),(18,57),(20,59),(22,60),
     (26,64),(28,68),(28,71),(24,70),(20,70),(18,70),(16,69),
     (14,68),(15,65),(12,60),(10,58),(5,58)],
    [(-17,15),(-17,21),(-14,24),(-8,30),(-2,32),(0,33),(5,33),
     (10,33),(14,32),(20,31),(25,30),(30,28),(32,25),(37,22),
     (42,12),(44,12),(44,11),(43,7),(42,2),(41,-1),
     (40,-10),(38,-18),(36,-22),(35,-27),(32,-30),(29,-32),
     (27,-34),(25,-34),(20,-35),(18,-34),(17,-32),(14,-28),
     (11,-18),(10,-8),(9,-1),(8,2),(3,4),(1,6),(-2,5),
     (-5,5),(-8,5),(-12,7),(-15,12),(-17,15)],
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
    [(114,-22),(115,-30),(118,-35),(122,-34),(126,-34),(130,-33),
     (135,-35),(138,-36),(141,-38),(148,-38),(151,-34),(153,-28),
     (153,-22),(148,-18),(145,-18),(142,-18),(138,-14),(136,-12),
     (130,-12),(128,-14),(124,-16),(118,-20),(114,-22)],
    [(173,-35),(175,-38),(174,-42),(172,-44),(170,-46)],
    [(172,-43),(174,-41),(176,-37),(175,-36),(173,-35)],
    [(-180,-70),(-150,-72),(-120,-72),(-90,-70),(-60,-72),
     (-30,-72),(0,-70),(30,-72),(60,-72),(90,-70),(120,-72),
     (150,-72),(180,-70)],
]

MAP_MX, MAP_MY = 110, 80
def ll2xy(lon, lat):
    x = int(MAP_MX + (lon+180)/360*(W-2*MAP_MX))
    y = int(MAP_MY + (90-lat)/180*(H-2*MAP_MY))
    return x, y

_CONT_SEGS = []
for _poly in CONTINENTS:
    _pts = [ll2xy(lo, la) for lo, la in _poly]
    for _i in range(len(_pts)-1):
        if abs(_pts[_i][0]-_pts[_i+1][0]) < W//3:
            _CONT_SEGS.append((_pts[_i], _pts[_i+1]))

ZONES = [
    ("CORDOBA",      "Agro",          -31.4,  -64.2, GOLD,   70, "alert", False),
    ("BALCARCE",     "Agro",          -37.9,  -58.3, GOLD,   70, "ok",   True),
    ("VACA MUERTA",  "Energy/O&G",    -38.8,  -68.9, ORANGE, 42, "ok",   False),
    ("ROTTERDAM",    "Maritime",       51.9,    4.5,  BLUE,   38, "ok",   False),
    ("PERMIAN",      "Oil & Gas",      31.8, -102.4,  ORANGE, 52, "ok",   False),
    ("PILBARA",      "Mining",        -22.0,  118.0,  PURPLE, 41, "ok",   False),
    ("AMAZONAS",     "ESG/Deforest.",  -3.5,  -60.0,  TEAL,   66, "ok",   False),
    ("INDIANA",      "Agro",           40.3,  -86.1,  GOLD,   38, "alert",False),
    ("MALACCA",      "Shipping",        2.2,  102.2,  BLUE,   42, "ok",   False),
]
_ZXY = [ll2xy(lo, la) for _, _, la, lo, _, _, _, _ in ZONES]
_SCAN_FRAC = [(x - MAP_MX) / (W - 2*MAP_MX) for x, _ in _ZXY]

# Posicion Balcarce en el mapa para HUD rings
_BAL_MX, _BAL_MY = _ZXY[1]

# Pre-calcular posicion de cada caracter del titulo
_tmp = Image.new("RGBA", (10, 10))
_td  = ImageDraw.Draw(_tmp)
_TITLE = "FARO  PROTOCOL"
_TW    = tw(_td, _TITLE, FGB100)
_TX0   = (W - _TW) // 2
_CHAR_X = []
_cx2 = 0
for _ch in _TITLE:
    _CHAR_X.append(_TX0 + _cx2)
    _cx2 += tw(_td, _ch, FGB100)
del _tmp, _td

# ═══════════════════════════════════════════════════════════════
# IMAGEN BALCARCE — split de pantalla
# ═══════════════════════════════════════════════════════════════
SPLIT_X = int(W * 0.43)
_RX_BASE = SPLIT_X + 22
_RY_MARG = 55
_bal_raw = Image.open(os.path.join(ROOT, "faro_reporte_fusion_balcarce.png")).convert("RGB")
_AVAIL_W = W - _RX_BASE - 18
_AVAIL_H = H - 2 * _RY_MARG
_rsc = min(_AVAIL_W / _bal_raw.width, _AVAIL_H / _bal_raw.height)
_RIW = int(_bal_raw.width * _rsc)
_RIH = int(_bal_raw.height * _rsc)
_bal = _bal_raw.resize((_RIW, _RIH), Image.LANCZOS)
_RX  = _RX_BASE
_RY  = _RY_MARG + (_AVAIL_H - _RIH) // 2

BSTATS = [
    ("NDVI",       0.531, "{:.3f}",      TEAL),
    ("SAR",       -13.1,  "{:.1f} dB",   BLUE),
    ("Score",      70.0,  "{:.0f}",      GREEN),
    ("Rinde est.", 3.32,  "{:.2f} t/ha", GREEN),
]
STATUS_ROWS = [
    ("NDVI ANALYSIS",  "CERTIFIED", TEAL),
    ("SAR PROCESSING", "CERTIFIED", TEAL),
    ("FUSION INDEX",   "VALIDATED", GREEN),
]
_BAR_NORMS = [0.531, 0.869, 0.70, 0.553]  # normalizado 0-1 para progress bars

def count_up(target, fmt, t2, dur=1.8, start_delay=0.):
    lt = t2 - start_delay
    if lt < 0: return fmt.format(0 if target >= 0 else -0.0)
    p = ease(min(1., lt/dur))
    return fmt.format(target * p)

# ═══════════════════════════════════════════════════════════════
# ESCENA 1 — Intro (0–4s)
# Streak + letras + arcos orbitales (az1)
# ═══════════════════════════════════════════════════════════════
def make_intro(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    cy  = H//2 - 25

    # Glow central (0-1.4s)
    if t < 1.4:
        pt = ease(min(1., t/0.6))
        for r in [60, 40, 20, 8, 3]:
            a_r = pt * (0.04 if r>20 else (0.12 if r>8 else 0.5))
            d.ellipse([(W//2-r, cy-r),(W//2+r, cy+r)], fill=(*GOLD, int(255*a_r)))
        hr = int(lerp(0, 120, ease(min(1., t/0.8))))
        if hr > 0:
            d.ellipse([(W//2-hr, cy-hr),(W//2+hr, cy+hr)],
                      outline=(*GOLD, int(60*pt)), width=1)

    # Streak horizontal (0.5-1.5s)
    st = t - 0.5
    if st > 0:
        sa = ease(min(1., st/0.3))
        fe = ease(max(0., min(1., 1.0-(t-1.5)/0.4)))
        streak_a = sa * fe
        streak_w = int(lerp(0, W, ease(min(1., st/0.5))))
        x1s = W//2 - streak_w//2
        x2s = W//2 + streak_w//2
        for dy, wa in [(0,0.9),(-1,0.4),(1,0.4),(-2,0.15),(2,0.15)]:
            d.line([(x1s, cy+dy),(x2s, cy+dy)],
                   fill=(*GOLD, int(255*streak_a*wa)), width=1)

    # Letras una por una (1.2-3.0s)
    title_t = t - 1.2
    cps = len(_TITLE) / 1.4
    ty  = cy - 55
    for i, (ch, cx3) in enumerate(zip(_TITLE, _CHAR_X)):
        appear = title_t - i/cps
        if appear < 0: continue
        ca = ease(min(1., appear/0.12))
        flash = max(0., 1.0 - appear/0.08)
        col2 = (int(lerp(GOLD2[0], GOLD[0], min(1., appear/0.3))),
                int(lerp(GOLD2[1], GOLD[1], min(1., appear/0.3))),
                int(lerp(GOLD2[2], GOLD[2], min(1., appear/0.3))))
        d.text((cx3, ty), ch, font=FGB100, fill=(*col2, int(255*ca)))
        if flash > 0.01:
            d.text((cx3, ty), ch, font=FGB100, fill=(*WHITE, int(255*flash*0.6)))

    # Subtitulo (2.5-4.0s)
    sub_t = t - 2.5
    if sub_t > 0:
        sub_a = ease(min(1., sub_t/0.4))
        cx_text(d, "Physical Truth from Orbit", cy+65, FM32, WHITE, int(220*sub_a))
        hline(d, cy+115, W//2-220, W//2+220, GOLD, 0.30*sub_a)

    # Arcos orbitales (2.2-4.0s) — az1
    orb_t = t - 2.2
    if orb_t > 0:
        orb_a = ease(min(1., orb_t/0.5))
        draw_orbital_arcs(d, W//2, cy+40, t, orb_a * 0.85)

    # Label orbital bottom
    if t > 3.0:
        la2 = ease(min(1., (t-3.0)/0.3))
        lbl2 = "ORBIT 512 km  |  SENTINEL-1A  |  S2 L2A  |  SAR + NDVI"
        d.text((W//2 - tw(d, lbl2, FM14)//2, cy+148), lbl2,
               font=FM14, fill=(*CYAN, int(140*la2)))

    if t < 0.4:
        fade = 1.0 - ease(t/0.4)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S1 = VideoClip(make_intro, duration=4)

# ═══════════════════════════════════════════════════════════════
# ESCENA 2 — Mapa global + HUD targeting rings (4–18s → 14s)
# Continentes dorados · radar scan · sonar pings · az2 HUD
# ═══════════════════════════════════════════════════════════════
SCAN_START = 0.5
SCAN_DUR   = 4.5

def make_map(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")

    a_scene = ease(min(1., t/0.3))
    a_cont  = ease(min(1., t/0.5)) * a_scene

    # Continentes base
    for (x1c,y1c),(x2c,y2c) in _CONT_SEGS:
        d.line([(x1c,y1c),(x2c,y2c)], fill=(*GOLD3, int(55*a_cont)), width=1)

    scan_prog = min(1., max(0., (t-SCAN_START)/SCAN_DUR))
    x_scan    = int(MAP_MX + scan_prog*(W-2*MAP_MX))

    # Continentes revelados post-scan
    for (x1c,y1c),(x2c,y2c) in _CONT_SEGS:
        if x1c < x_scan or x2c < x_scan:
            xmax = max(x1c, x2c)
            reveal = min(1., max(0., (x_scan-xmax)/60))
            if reveal > 0:
                d.line([(x1c,y1c),(x2c,y2c)],
                       fill=(*GOLD, int(45*reveal*a_cont)), width=1)

    # Radar scan line
    if SCAN_START < t < SCAN_START + SCAN_DUR + 0.5:
        sa = min(1., max(0., 1.0-(t-SCAN_START-SCAN_DUR)/0.5))
        for dx in range(80, 0, -2):
            trail_x = x_scan - dx
            if trail_x < MAP_MX: continue
            ta2 = int(28*(1-dx/80)*sa)
            d.line([(trail_x,MAP_MY),(trail_x,H-MAP_MY)], fill=(*GREEN, ta2), width=1)
        pulse_s = 0.6 + 0.4*math.sin(t*40)
        d.line([(x_scan,MAP_MY),(x_scan,H-MAP_MY)],
               fill=(*GREEN, int(220*sa*pulse_s)), width=2)
        for dx in (1,2,3,5,8):
            ga = int(70*(1-dx/9)*sa)
            for sx in (x_scan-dx, x_scan+dx):
                if MAP_MX < sx < W-MAP_MX:
                    d.line([(sx,MAP_MY),(sx,H-MAP_MY)], fill=(*GREEN, ga), width=1)

    # Zonas
    for i, (name, sector, lat, lon, color, score, status, _) in enumerate(ZONES):
        zx, zy = _ZXY[i]
        frac   = _SCAN_FRAC[i]
        age    = (scan_prog - frac) * SCAN_DUR
        if scan_prog < frac - 0.03: continue
        a_zone = ease(min(1., age/0.3))

        # Sonar rings
        for ring_idx in range(2):
            phase    = ring_idx * 0.9 + i * 0.4
            ring_cyc = (t - SCAN_START + phase) % 2.2
            rp       = ring_cyc / 2.2
            rr       = int(rp * 52)
            ra       = int(200*(1-rp)*a_zone*0.55)
            if ra > 0 and rr > 2:
                d.ellipse([(zx-rr,zy-rr),(zx+rr,zy+rr)],
                          outline=(*color, ra), width=1)

        ch_age = min(1., age/0.6)
        if ch_age < 1:
            crosshair(d, zx, zy, 22, color, (1-ch_age)*0.8*a_zone)

        dot(d, zx, zy, 5, color, a_zone*0.9)
        dot(d, zx, zy, 2, WHITE, a_zone*0.85)

        if age > 0.25:
            la = ease(min(1., (age-0.25)/0.25))
            lw2 = tw(d, name, FM14)
            lx  = zx - lw2//2
            oy  = -28 if zy < H//2 else 16
            d.rectangle([(lx-4,zy+oy-2),(lx+lw2+4,zy+oy+18)],
                        fill=(*BG, int(200*la)))
            d.text((lx, zy+oy), name, font=FM14,
                   fill=(*color, int(255*la)))
            if age > 0.6:
                sa2   = ease(min(1., (age-0.6)/0.3))
                s_col = GREEN if score >= 50 else ORANGE
                stxt  = f"Score {score}"
                sw    = tw(d, stxt, FM14)
                sx2   = zx - sw//2
                oy2   = oy + (18 if oy < 0 else -18)
                d.rectangle([(sx2-4,zy+oy2-2),(sx2+sw+4,zy+oy2+16)],
                             fill=(*BG, int(180*sa2)))
                d.text((sx2, zy+oy2), stxt, font=FM14,
                       fill=(*s_col, int(220*sa2)))

    # HUD targeting rings en Balcarce (az2) — aparece cuando el scan llega a SA
    bal_frac = _SCAN_FRAC[1]
    bal_age  = (scan_prog - bal_frac) * SCAN_DUR
    if bal_age > 0.8:
        ring_a = ease(min(1., (bal_age-0.8)/0.8))
        draw_hud_rings(d, _BAL_MX, _BAL_MY, t, ring_a * a_scene)

    # HUD frame superior/inferior
    hline(d, 68, 80, W-80, GOLD, 0.20*a_scene)
    hline(d, H-68, 80, W-80, GOLD, 0.20*a_scene)
    d.text((85, 38), "FARO PROTOCOL  /  GLOBAL ASSET MONITORING",
           font=FM18, fill=(*GOLD, int(160*a_scene)))
    n_vis = sum(1 for i in range(len(ZONES)) if (scan_prog-_SCAN_FRAC[i]) > 0)
    active_str = f"{n_vis}/{len(ZONES)} ACTIVE"
    d.text((W-85-tw(d,active_str,FM18), 38), active_str,
           font=FM18, fill=(*GREEN, int(160*a_scene)))

    d.text((85, H-55), "SAR + NDVI  /  SENTINEL-1A  /  SENTINEL-2",
           font=FM14, fill=(*GOLD, int(120*a_scene)))

    # Timestamp corner (az3 style)
    ts2 = "12 APR 2026  08:32 UTC"
    d.text((W-85-tw(d,ts2,FM14), H-55), ts2,
           font=FM14, fill=(*CYAN, int(120*a_scene)))

    if t < 0.3:
        fade = 1.0 - ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S2 = VideoClip(make_map, duration=14)

# ═══════════════════════════════════════════════════════════════
# ESCENA 3 — Balcarce (18–30s → 12s)
# Az3 style: left panel CERTIFIED + right: sat image con scan/topo
# ═══════════════════════════════════════════════════════════════
def make_balcarce(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")

    a_scene = ease(min(1., t/0.3))

    # Grid sutil en panel izquierdo (az4 style)
    grid_a = ease(min(1., t/0.6)) * 0.055
    PX = 55
    PW = SPLIT_X - PX - 22
    for gx in range(PX, SPLIT_X, 38):
        d.line([(gx, 80),(gx, H-80)], fill=(*CYAN, int(255*grid_a)), width=1)
    for gy in range(80, H-80, 38):
        d.line([(PX, gy),(SPLIT_X-10, gy)], fill=(*CYAN, int(255*grid_a)), width=1)

    # === PANEL DERECHO: imagen satelital ===
    slide = ease(min(1., t/0.35))
    ix = int(lerp(W+60, _RX, slide))

    bal_layer = Image.new("RGBA",(W,H),(0,0,0,0))
    bal_copy  = _bal.copy().convert("RGBA")
    dim_layer = Image.new("RGBA", bal_copy.size, (0,0,5,45))
    bal_copy  = Image.alpha_composite(bal_copy, dim_layer)
    bal_layer.paste(bal_copy, (ix, _RY))
    img = Image.alpha_composite(img, bal_layer)
    d   = ImageDraw.Draw(img, "RGBA")

    # Brackets en imagen (CYAN + GOLD — az style)
    br_a = ease(min(1., (t-0.2)/0.3))
    if ix < W:
        corner_brackets(d, ix, _RY, ix+_RIW, _RY+_RIH, CYAN, 0.75*br_a, 32)
        corner_brackets(d, ix, _RY, ix+_RIW, _RY+_RIH, GOLD, 0.30*br_a, 55)

    # Scan sweep sobre imagen (az3 style)
    if t > 0.4 and ix < W:
        cyc_t = (t - 0.4) % 3.2 / 3.2
        scan_y2 = int(_RY + cyc_t * _RIH)
        for dy in range(60, 0, -3):
            sy = scan_y2 - dy
            if sy >= _RY:
                d.line([(ix,sy),(ix+_RIW,sy)], fill=(*GREEN, int(16*(1-dy/60))), width=1)
        d.line([(ix, scan_y2),(ix+_RIW, scan_y2)], fill=(*GREEN, 95), width=1)

    # Lineas topograficas sobre imagen (az3 style)
    topo_a = ease(min(1., (t-1.0)/1.2)) * 0.26
    if topo_a > 0.01 and ix < W:
        tx1 = max(ix, _RX)
        tx2 = min(ix+_RIW, W-16)
        ty1 = _RY + 15
        ty2 = _RY + _RIH - 15
        tw4 = tx2 - tx1
        for row_i, row_y in enumerate(range(ty1, ty2, 26)):
            pts4 = []
            for xi in range(0, tw4+1, 16):
                wave = int(13 * math.sin((xi/max(tw4,1)) * 3.8*math.pi
                                         + row_i * 1.35 + t*0.18))
                pts4.append((tx1+xi, row_y+wave))
            for j in range(len(pts4)-1):
                d.line([pts4[j], pts4[j+1]],
                       fill=(*GREEN, int(255*topo_a)), width=1)

    # Timestamp sobre imagen (az3 style)
    ts_a = ease(min(1., (t-0.5)/0.3))
    ts_txt = "12 APR 2026  08:32 UTC"
    d.text((_RX+10, _RY+_RIH-26), ts_txt,
           font=FM14, fill=(*CYAN, int(160*ts_a)))

    # === PANEL IZQUIERDO ===
    # Header
    hline(d, 95, PX, PX+PW, GOLD, 0.35*a_scene)
    d.text((PX, 40), "FARO PROTOCOL  /  CERTIFIED FIELD REPORT",
           font=FM18, fill=(*GOLD, int(170*a_scene)))

    # Nombre zona — letra por letra
    zone_t = t - 0.1
    zone_name = "BALCARCE"
    cps3 = len(zone_name) / 0.5
    for i, ch in enumerate(zone_name):
        ap = zone_t - i/cps3
        if ap < 0: continue
        ca = ease(min(1., ap/0.1))
        pw3 = tw(d, zone_name[:i], FGB72)
        d.text((PX+pw3, 112), ch, font=FGB72, fill=(*GOLD, int(255*ca)))

    na = ease(min(1., (t-0.3)/0.25))
    d.text((PX, 200), "37.9 S  58.3 W  /  BUENOS AIRES  /  ARGENTINA",
           font=FA22, fill=(*WHITE, int(160*na)))
    d.text((PX, 228), "VERTICAL: AGRICULTURE  /  SENTINEL-1A + 2",
           font=FM18, fill=(*TEAL, int(150*na)))

    hline(d, 262, PX, PX+PW, GOLD, 0.22*na)

    # STATUS ROWS con badge CERTIFIED (az3 style)
    sy0 = 275
    for si, (lbl_r, badge_r, bcol) in enumerate(STATUS_ROWS):
        row_a = ease(min(1., (t-0.35-si*0.15)/0.25))
        if row_a <= 0: continue
        ry = sy0 + si * 37
        d.line([(PX, ry-2),(PX+PW, ry-2)], fill=(*GOLD, int(32*row_a)), width=1)
        d.text((PX+8, ry+4), lbl_r, font=FM14, fill=(*WHITE, int(155*row_a)))
        bw2 = tw(d, badge_r, FM14) + 16
        bx2 = PX + PW - bw2
        d.rectangle([(bx2, ry+3),(bx2+bw2, ry+24)],
                    fill=(*bcol, int(22*row_a)))
        d.rectangle([(bx2, ry+3),(bx2+bw2, ry+24)],
                    outline=(*bcol, int(160*row_a)), width=1)
        d.text((bx2+8, ry+5), badge_r, font=FM14,
               fill=(*bcol, int(240*row_a)))

    hline(d, sy0+len(STATUS_ROWS)*37+6, PX, PX+PW, GOLD, 0.18*na)

    # Métricas con progress bars
    MET_Y = sy0 + len(STATUS_ROWS)*37 + 20
    ROW_H = (H - MET_Y - 100) // len(BSTATS)
    for i, (lbl_m, target, fmt, color) in enumerate(BSTATS):
        ta = ease(min(1., (t-0.55-i*0.22)/0.25))
        if ta <= 0: continue
        ry = MET_Y + i*ROW_H
        d.rectangle([(PX, ry),(PX+3, ry+ROW_H-16)], fill=(*color, int(200*ta)))
        d.text((PX+14, ry+4), lbl_m.upper(), font=FM18, fill=(*WHITE, int(175*ta)))
        val_str = count_up(target, fmt, t, 1.6, 0.6+i*0.22)
        d.text((PX+14, ry+26), val_str, font=FM44, fill=(*color, int(255*ta)))
        # Progress bar (az3 style)
        bar_a = ease(min(1., (t-1.0-i*0.2)/0.6))
        if bar_a > 0:
            bar_y = ry + ROW_H - 20
            bar_w3 = PW - 22
            d.rectangle([(PX+14, bar_y),(PX+14+bar_w3, bar_y+5)],
                        fill=(*color, int(28*ta)))
            fill_w = int(bar_w3 * _BAR_NORMS[i] * bar_a)
            if fill_w > 0:
                d.rectangle([(PX+14, bar_y),(PX+14+fill_w, bar_y+5)],
                            fill=(*color, int(165*ta*bar_a)))
        hline(d, ry+ROW_H-14, PX, PX+PW-10, GOLD, 0.10*ta)

    # SHA badge bottom
    sha_t = t - 3.5
    if sha_t > 0:
        sha_a = ease(min(1., sha_t/0.3))
        BY = H - 92
        d.rectangle([(PX, BY),(PX+PW, BY+55)], fill=(*BG, int(230*sha_a)))
        d.rectangle([(PX, BY),(PX+PW, BY+55)],
                    outline=(*GOLD, int(70*sha_a)), width=1)
        d.text((PX+12, BY+8), "SHA-256 SEALED  -  Faro Protocol 2026",
               font=FM14, fill=(*GOLD, int(255*sha_a*0.85)))
        d.text((PX+12, BY+30), "Proof of data integrity before market open",
               font=FM14, fill=(*WHITE, int(155*sha_a)))

    if t < 0.3:
        fade = 1.0-ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S3 = VideoClip(make_balcarce, duration=12)

# ═══════════════════════════════════════════════════════════════
# ESCENA 4 — SHA-256 reveal (30–42s → 12s)
# + barra INTEGRITY: CERTIFIED + mini waveform (az4)
# ═══════════════════════════════════════════════════════════════
_HEX_CHARS = "0123456789abcdef"
_RNG = random.Random(42)
_RAIN_COLS = 48
_RAIN_CW   = W // _RAIN_COLS
_RAIN_DATA = []
for _ci in range(_RAIN_COLS):
    _speed  = _RNG.uniform(5, 14)
    _offset = _RNG.uniform(-4, 0)
    _chars  = [_RNG.choice(_HEX_CHARS) for _ in range(40)]
    _length = _RNG.randint(8, 20)
    _RAIN_DATA.append((_speed, _offset, _chars, _length))

_WAVE_DATA = [0.38 + 0.28*math.sin(i*0.42) + 0.08*math.cos(i*1.15)
              for i in range(55)]

def draw_rain(d, t, alpha_mult=1.0):
    for ci, (speed, offset, chars, length) in enumerate(_RAIN_DATA):
        cx4 = ci * _RAIN_CW + 8
        head_f = (offset + speed * t) % (H//16 + length)
        head_y = int(head_f * 16)
        for j in range(length):
            cy4 = head_y - j*16
            if cy4 < 0 or cy4 > H: continue
            brightness = max(0., 1 - j/length)
            col_c = (*GOLD2, int(180*brightness*alpha_mult)) if j == 0 \
                    else (*GOLD3, int(90*brightness*alpha_mult))
            ch = chars[(int(head_f) + j) % len(chars)]
            d.text((cx4, cy4), ch, font=FM12, fill=col_c)

def sha_char_at(pos, t2):
    lock_t = pos * 0.14
    lt = t2 - lock_t
    if lt < 0: return None, 0.
    if lt < 0.25:
        cycle = int(lt * 28) % 16
        return _HEX_CHARS[cycle], min(1., lt/0.08)
    return SHA256[pos], 1.0

def make_sha(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")

    if t < 1.5:
        rain_a = ease(t/1.5)
    elif t > 9.5:
        rain_a = ease(max(0., (11.-t)/1.5))
    else:
        rain_a = 1.0
    draw_rain(d, t, rain_a * 0.55)
    scanline_overlay(d, 0.015)

    sha_t = t - 0.8
    a_g   = ease(min(1., sha_t/0.3)) if sha_t > 0 else 0.

    if a_g > 0:
        hline(d, 85, 200, W-200, GOLD, 0.30*a_g)
        cx_text(d, "CRYPTOGRAPHIC PROOF OF AUTHENTICITY", 46, FM18,
                GOLD, int(160*a_g))

        CHAR_W   = 30
        LINE1    = SHA256[:32]; LINE2 = SHA256[32:]
        TOTAL_W1 = len(LINE1)*CHAR_W
        TOTAL_W2 = len(LINE2)*CHAR_W
        x1s2     = (W-TOTAL_W1)//2
        x2s2     = (W-TOTAL_W2)//2
        y_sha1   = H//2 - 85
        y_sha2   = y_sha1 + 60

        lbl5 = "SHA-256"
        lw5  = tw(d, lbl5, FM24)
        d.text(((W-lw5)//2, y_sha1-38), lbl5,
               font=FM24, fill=(*GREEN, int(200*a_g)))
        hline(d, y_sha1-10, W//2-250, W//2+250, GREEN, 0.30*a_g)

        for pos in range(len(SHA256)):
            ch, ca = sha_char_at(pos, sha_t)
            if ch is None: continue
            row = 0 if pos < 32 else 1
            col = pos if pos < 32 else pos-32
            x   = (x1s2 if row==0 else x2s2) + col*CHAR_W
            y   = y_sha1 if row==0 else y_sha2
            locked = (ch == SHA256[pos] and ca >= 1.0)
            col_c  = GREEN if locked else GOLD2
            d.text((x, y), ch, font=FM24, fill=(*col_c, int(255*ca*a_g)))
            lock_age = sha_t - pos*0.14 - 0.25
            if 0 < lock_age < 0.08:
                fa = (1-lock_age/0.08)*0.7
                d.text((x, y), ch, font=FM24, fill=(*WHITE, int(255*fa*a_g)))

        hline(d, y_sha2+50, W//2-250, W//2+250, GREEN, 0.20*a_g)

        ts_appear = sha_t - (64*0.14 + 0.3)
        if ts_appear > 0:
            ta2 = ease(min(1., ts_appear/0.4))
            ts_w2 = tw(d, TIMESTAMP, FM32)
            d.text(((W-ts_w2)//2, y_sha2+70), TIMESTAMP,
                   font=FM32, fill=(*GOLD2, int(220*ta2)))
            badge_t = ts_appear - 0.5
            if badge_t > 0:
                ba = ease(min(1., badge_t/0.35))
                bw5, bh5 = 380, 54
                bx5 = (W-bw5)//2; by5 = y_sha2 + 120
                d.rectangle([(bx5,by5),(bx5+bw5,by5+bh5)], fill=(*BG, int(240*ba)))
                d.rectangle([(bx5,by5),(bx5+bw5,by5+bh5)],
                            outline=(*GREEN, int(180*ba)), width=1)
                badge_txt5 = "VERIFIED  /  BLOCKCHAIN ANCHORED"
                bw5b = tw(d, badge_txt5, FM18)
                d.text(((W-bw5b)//2, by5+14), badge_txt5,
                       font=FM18, fill=(*GREEN, int(255*ba)))

    # INTEGRITY bar — az4 style
    integrity_t = (sha_t - (64*0.14 + 1.2)) if sha_t > 0 else -1.
    if integrity_t > 0:
        ia = ease(min(1., integrity_t/0.5))
        BY2 = H - 68
        bar_x1 = W//2 + 60; bar_x2 = W - 120
        bar_w5 = bar_x2 - bar_x1
        d.rectangle([(bar_x1-12, BY2-6),(bar_x2+4, BY2+46)],
                    fill=(*BG, int(220*ia)))
        d.rectangle([(bar_x1-12, BY2-6),(bar_x2+4, BY2+46)],
                    outline=(*CYAN, int(55*ia)), width=1)
        int_txt5 = "SUBSURFACE INTEGRITY"
        d.text((bar_x1, BY2+2), int_txt5, font=FM14, fill=(*WHITE, int(155*ia)))
        cert_txt5 = "CERTIFIED"
        cw5 = tw(d, cert_txt5, FM14)
        d.text((bar_x2-cw5, BY2+2), cert_txt5,
               font=FM14, fill=(*GREEN, int(255*ia)))
        pts_n = min(len(_WAVE_DATA), int(integrity_t * 14) + 2)
        if pts_n >= 2:
            draw_mini_chart(d, bar_x1, BY2+18, bar_w5, 22,
                           _WAVE_DATA[:pts_n], TEAL, ia)

    if t < 0.3:
        fade = 1.0-ease(t/0.3)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S4 = VideoClip(make_sha, duration=12)

# ═══════════════════════════════════════════════════════════════
# ESCENA 5 — Tres frases de impacto (42–52s → 10s)
# ═══════════════════════════════════════════════════════════════
PHRASES = [
    ("48 hours before",   "official data.",              GOLD),
    ("Cryptographically", "verified.",                    GREEN),
    ("Any asset.",        "Any location.\nAny sector.",   BLUE),
]

def draw_blur_text(d, text, x, y, font, color, alpha, blur=12, copies=7):
    for i in range(copies):
        ox      = int(lerp(blur, 0, (i+1)/copies))
        alpha_i = int(alpha * (i+1)/copies * 0.7)
        d.text((x+ox, y), text, font=font, fill=(*color, alpha_i))
    d.text((x, y), text, font=font, fill=(*color, int(alpha)))

def make_phrases(t):
    pd  = 10.0 / len(PHRASES)
    pi  = min(int(t/pd), len(PHRASES)-1)
    lt  = t - pi*pd
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    scanline_overlay(d, 0.018)

    line1, line2, color = PHRASES[pi]

    if   lt < 0.25:      a = ease(lt/0.25)
    elif lt > pd-0.25:   a = ease((pd-lt)/0.25)
    else:                a = 1.0

    blur_t  = max(0., 0.4 - lt)
    blur_px = int(blur_t * 55)

    d.text((85, 48), f"0{pi+1} / 0{len(PHRASES)}", font=FM18,
           fill=(*GOLD, int(200*a*0.5)))

    cy_base = H//2 - 60
    x1p     = (W - tw(d, line1, FGB72))//2
    draw_blur_text(d, line1, x1p, cy_base, FGB72, color, int(255*a), blur_px, 8)

    lines2 = line2.split("\n")
    y_off  = th(d, line1, FGB72) + 18
    for i2, ln in enumerate(lines2):
        x2p = (W - tw(d, ln, FG60))//2
        a2  = ease(min(1., (lt-0.07-i2*0.06)/0.2))
        draw_blur_text(d, ln, x2p, cy_base+y_off, FG60, color,
                       int(255*a*a2), blur_px//2, 6)
        y_off += th(d, ln, FG60) + 12

    line_ap = ease(min(1., (lt-0.15)/0.2))
    hline(d, cy_base-32, W//2-160, W//2+160, color, 0.35*a*line_ap)
    hline(d, cy_base+y_off+10, W//2-100, W//2+100, color, 0.20*a*line_ap)

    for di in range(len(PHRASES)):
        dx2p = W//2 + (di-1)*30
        col2p = color if di == pi else DIM
        aap   = 0.85 if di == pi else 0.4
        d.ellipse([(dx2p-5,H-52-5),(dx2p+5,H-52+5)],
                  fill=(*col2p, int(255*aap)))

    if t < 0.25:
        fade = 1.0-ease(t/0.25)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S5 = VideoClip(make_phrases, duration=10)

# ═══════════════════════════════════════════════════════════════
# ESCENA 6 — 6 sectores en grid (52–58s → 6s)
# + mini sparkline charts (az4 style)
# ═══════════════════════════════════════════════════════════════
SECTORS = [
    ("AGRICULTURE",   "Crops - Yield - Stress",       GOLD,   "01"),
    ("ENERGY / O&G",  "Permian - Vaca Muerta",        ORANGE, "02"),
    ("MARITIME",      "Rotterdam - Port activity",    BLUE,   "03"),
    ("MINING",        "Pilbara - Iron ore - REE",     PURPLE, "04"),
    ("ESG / FOREST",  "Amazonas - Carbon - Land",     TEAL,   "05"),
    ("SHIPPING",      "Malacca - Strait - Freight",   BLUE,   "06"),
]
_SPARKLINES = [
    [0.50,0.60,0.55,0.70,0.65,0.80,0.75,0.90,0.85,0.95],
    [0.70,0.65,0.75,0.70,0.80,0.75,0.85,0.80,0.90,0.88],
    [0.30,0.40,0.35,0.45,0.40,0.50,0.45,0.55,0.50,0.60],
    [0.60,0.55,0.65,0.60,0.70,0.75,0.70,0.80,0.75,0.85],
    [0.80,0.75,0.70,0.65,0.60,0.55,0.50,0.55,0.50,0.45],
    [0.40,0.45,0.50,0.45,0.55,0.50,0.60,0.55,0.65,0.70],
]

def make_sectors(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    scanline_overlay(d, 0.02)

    a_hdr = ease(min(1., t/0.2))
    cx_text(d, "SECTOR COVERAGE", 45, FG48, GOLD, int(255*a_hdr))
    hline(d, 108, 160, W-160, GOLD, 0.25*a_hdr)

    cols6, rows6 = 3, 2
    CW6 = (W-120)//cols6; CH6 = (H-165)//rows6
    interval6 = 6.0/len(SECTORS)

    for i, (name6, desc6, color6, num6) in enumerate(SECTORS):
        col6  = i % cols6; row6 = i // cols6
        age6  = t - i*interval6
        if age6 < 0: continue

        ta6  = ease(min(1., age6/0.2))
        bx1  = 60 + col6*CW6 + 18
        by1  = 128 + row6*CH6 + 14
        bx2  = bx1 + CW6 - 36
        by2  = by1 + CH6 - 28

        flash6 = max(0., 1.0 - age6/0.12)
        d.rectangle([(bx1,by1),(bx2,by2)], fill=(*BG, int(250*ta6)))
        if flash6 > 0:
            d.rectangle([(bx1,by1),(bx2,by2)], fill=(*color6, int(75*flash6)))
        d.rectangle([(bx1,by1),(bx2,by2)], outline=(*color6, int(65*ta6)), width=1)
        d.rectangle([(bx1,by1),(bx1+4,by2)], fill=(*color6, int(210*ta6)))
        corner_brackets(d, bx1, by1, bx2, by2, color6, 0.60*ta6, 16)

        d.text((bx2-tw(d,num6,FM18)-12, by1+10), num6,
               font=FM18, fill=(*color6, int(80*ta6)))
        d.text((bx1+18, by1+16), name6, font=FG28, fill=(*color6, int(255*ta6)))
        d.text((bx1+18, by1+58), desc6, font=FM18, fill=(*WHITE, int(160*ta6)))

        # Status dot
        da6 = ease(min(1., (age6-0.15)/0.2))
        if da6 > 0:
            dot(d, bx2-22, by1+22, 5, GREEN, da6*0.9)
            dot(d, bx2-22, by1+22, 2, WHITE, da6*0.8)

        # Mini sparkline chart (az4 style)
        chart_a6 = ease(min(1., (age6-0.28)/0.4))
        if chart_a6 > 0.01:
            ch6_x = bx1+18
            ch6_y = by2 - 52
            ch6_w = (bx2-bx1)//2 - 12
            ch6_h = 36
            pts_n6 = min(len(_SPARKLINES[i]), int(age6*9)+1)
            if pts_n6 >= 2:
                draw_mini_chart(d, ch6_x, ch6_y, ch6_w, ch6_h,
                               _SPARKLINES[i][:pts_n6], color6, chart_a6*ta6)

        # "ACTIVE" label next to chart
        if da6 > 0.5:
            act_a = ease(min(1., (age6-0.45)/0.3))
            d.text((bx1+18 + (bx2-bx1)//2, by2-46), "ACTIVE",
                   font=FM12, fill=(*GREEN, int(180*act_a*da6)))

    if t < 0.2:
        fade = 1.0-ease(t/0.2)
        black = Image.new("RGBA",(W,H),(0,0,0,int(255*fade)))
        img   = Image.alpha_composite(img, black)

    return npf(img)

S6 = VideoClip(make_sectors, duration=6)

# ═══════════════════════════════════════════════════════════════
# ESCENA 7 — Outro (58–60s → 2s)
# Logo + arcos orbitales (echo de S1)
# ═══════════════════════════════════════════════════════════════
URL = "faro-protocol.netlify.app"

def make_outro(t):
    img = bg()
    d   = ImageDraw.Draw(img, "RGBA")
    cy  = H//2

    if   t < 0.4:  a = ease(t/0.4)
    elif t > 1.4:  a = ease((2.-t)/0.6)
    else:          a = 1.0

    pulse = 0.5 + 0.5*math.sin(t*4)

    hline(d, cy-92, W//2-380, W//2+380, GOLD, 0.28*a)

    title7 = "FARO  PROTOCOL"
    tw7    = tw(d, title7, FGB100)
    d.text(((W-tw7)//2, cy-78), title7, font=FGB100, fill=(*GOLD, int(255*a)))

    cx_text(d, "Physical Truth from Orbit", cy+42, FG28, WHITE, int(220*a*0.85))
    hline(d, cy+92, W//2-280, W//2+280, GOLD, 0.25*a)

    url_a = ease(min(1., (t-0.3)/0.3))
    cx_text(d, URL, cy+118, FG28, GOLD2, int(255*a*url_a))

    tag7 = "SAR  /  NDVI  /  SHA-256  /  24h delivery  /  Any location"
    cx_text(d, tag7, cy+165, FM18, WHITE, int(150*a*url_a*0.7))

    dot(d, W//2, cy+212, 4, GOLD, a*(0.35+0.3*pulse))

    # Orbital arcs (echo de S1 — az1)
    if t > 0.25:
        oa = ease(min(1., (t-0.25)/0.5)) * a
        draw_orbital_arcs(d, W//2, cy+35, t*2.2, oa*0.55)

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
    os.makedirs(os.path.join(ROOT, "outputs"), exist_ok=True)

    clips  = [S1, S2, S3, S4, S5, S6, S7]
    durs   = [c.duration for c in clips]
    total  = sum(durs)
    labels = ["Intro", "Mapa", "Balcarce", "SHA-256", "Frases", "Sectores", "Outro"]

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
        ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"],
        logger="bar",
    )
    sz = os.path.getsize(OUT) / 1e6
    print(f"[OK] {sz:.1f} MB  ->  {OUT}")


if __name__ == "__main__":
    main()
