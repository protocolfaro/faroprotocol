#!/usr/bin/env python3
"""
faro_demo_video.py -- Video demo Faro Protocol 60s @ 1920x1080
Genera faro_demo.mp4

Uso:
    python faro_demo_video.py
"""
from __future__ import annotations
import sys, math

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = Path(__file__).parent

# ── Paleta ──────────────────────────────────────────────────────────────────
BG       = (10,  10,  26)
BG2      = (13,  16,  32)
GOLD     = (201, 168, 76)
GOLD_L   = (226, 201, 126)
WHITE    = (242, 237, 228)
WHITE3   = (136, 136, 153)
GREEN    = (0,   255, 65)
GREEN_D  = (0,   140, 30)
BLUE     = (42,  100, 150)
AMBER    = (192, 122, 48)
RED      = (176, 48,  48)

W, H  = 1920, 1080
FPS   = 24
PNG_Y_TOP = 175   # salta el área de título matplotlib (evita contenido cortado abajo)

# ── Fuentes ──────────────────────────────────────────────────────────────────
def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return None

GEORGIA   = 'C:/Windows/Fonts/georgia.ttf'
GEORGIAB  = 'C:/Windows/Fonts/georgiab.ttf'
CONSOLAS  = 'C:/Windows/Fonts/consola.ttf'
VERDANA   = 'C:/Windows/Fonts/verdana.ttf'
VERDANAB  = 'C:/Windows/Fonts/verdanab.ttf'

def font_serif(size):
    return _font(GEORGIAB, size) or _font(GEORGIA, size) or ImageFont.load_default()

def font_sans(size):
    return _font(VERDANAB, size) or _font(VERDANA, size) or ImageFont.load_default()

def font_mono(size):
    return _font(CONSOLAS, size) or ImageFont.load_default()

# ── Helpers PIL ───────────────────────────────────────────────────────────────
def new_frame(bg=BG):
    img = Image.new('RGB', (W, H), bg)
    return img, ImageDraw.Draw(img)

def to_arr(img):
    return np.array(img)

def ease(t):
    """Suavizado cubic ease in-out."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3

def centered_text(draw, y, text, font, color, alpha_f=1.0):
    """Dibuja texto centrado en x."""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    x  = (W - tw) // 2
    r, g, b = color
    c = (int(r*alpha_f), int(g*alpha_f), int(b*alpha_f))
    draw.text((x, y), text, font=font, fill=c)

def draw_line_h(draw, y, color, alpha=1.0):
    r, g, b = color
    c = (int(r*alpha), int(g*alpha), int(b*alpha))
    draw.line([(0, y), (W, y)], fill=c, width=1)

def glow_dot(img, cx, cy, r, color, intensity=1.0):
    """Dibuja un punto con efecto glow."""
    for radius in range(r * 4, 0, -1):
        alpha_f = (1 - radius / (r * 4)) * intensity * 0.15
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        clr = color + (int(255 * alpha_f),)
        d.ellipse([cx-radius, cy-radius, cx+radius, cy+radius], fill=clr)
        img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
    # Core dot
    d2 = ImageDraw.Draw(img)
    d2.ellipse([cx-r, cy-r, cx+r, cy+r], fill=color)
    return img

def fade_black(img, alpha):
    """Superpone negro con alpha (0=transparent, 1=black)."""
    overlay = Image.new('RGB', img.size, (0, 0, 0))
    return Image.blend(img, overlay, max(0, min(1, alpha)))

def load_asset(name):
    """Carga PNG del proyecto y lo redimensiona a 1920×1080 si necesario."""
    p = ROOT / name
    if not p.exists():
        return None
    img = Image.open(p).convert('RGB')
    return img

def fit_into(img, target_w, target_h, crop_top=PNG_Y_TOP):
    """Escala preservando aspecto y centra en target. Recorta crop_top px arriba (área de título matplotlib)."""
    if crop_top > 0 and img.height > crop_top:
        img = img.crop((0, crop_top, img.width, img.height))
    rw = target_w / img.width
    rh = target_h / img.height
    r  = min(rw, rh)
    nw, nh = int(img.width * r), int(img.height * r)
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGB', (target_w, target_h), BG)
    ox = (target_w - nw) // 2
    oy = (target_h - nh) // 2
    canvas.paste(img, (ox, oy))
    return canvas

def slide_in(asset_img, frame_w, frame_h, progress, from_side='left'):
    """Slide de imagen desde un lado. progress 0→1."""
    canvas = Image.new('RGB', (frame_w, frame_h), BG)
    p = ease_out(progress)
    if from_side == 'left':
        x = int((p - 1) * frame_w)
    elif from_side == 'right':
        x = int((1 - p) * frame_w)
    else:
        x = 0
    canvas.paste(asset_img, (x, 0))
    return canvas

# ── 9 Zonas globales ──────────────────────────────────────────────────────────
# (nombre, sector, lon, lat, color)
ZONES = [
    ('CÓRDOBA',      'AGRO',        -64.2, -31.4, GOLD),
    ('BALCARCE',     'AGRO',        -58.3, -37.7, GOLD),
    ('VACA MUERTA',  'O&G',         -69.0, -38.0, BLUE),
    ('ROTTERDAM',    'MARITIME',     4.5,   51.9,  BLUE),
    ('PERMIAN',      'OIL GAS',    -102.5,  31.8, AMBER),
    ('PILBARA',      'MINING',      118.0, -21.5,  AMBER),
    ('AMAZONAS',     'FOREST',      -60.0,  -3.0,  GREEN),
    ('INDIANA',      'AGRO',        -86.1,  39.7,  GOLD),
    ('MALACCA',      'SHIPPING',    101.5,   2.5,  BLUE),
]

def lonlat_to_px(lon, lat, pad_x=140, pad_y=120):
    x = int((lon + 180) / 360 * (W - 2*pad_x) + pad_x)
    y = int((90 - lat) / 180 * (H - 2*pad_y) + pad_y)
    return x, y

# ── Dibuja grilla de mapa ─────────────────────────────────────────────────────
def draw_map_grid(draw, alpha=0.18):
    # Meridianos
    for lon in range(-180, 181, 30):
        x = lonlat_to_px(lon, 0)[0]
        c = (int(GOLD[0]*alpha), int(GOLD[1]*alpha), int(GOLD[2]*alpha))
        draw.line([(x, 0), (x, H)], fill=c, width=1)
    # Paralelos
    for lat in range(-90, 91, 30):
        y = lonlat_to_px(0, lat)[1]
        c = (int(GOLD[0]*alpha), int(GOLD[1]*alpha), int(GOLD[2]*alpha))
        draw.line([(0, y), (W, y)], fill=c, width=1)
    # Ecuador y meridiano 0 ligeramente más brillantes
    cx = lonlat_to_px(0, 0)
    ca = (int(GOLD[0]*alpha*2), int(GOLD[1]*alpha*2), int(GOLD[2]*alpha*2))
    draw.line([(0, cx[1]), (W, cx[1])], fill=ca, width=1)
    draw.line([(cx[0], 0), (cx[0], H)], fill=ca, width=1)


# ═══════════════════════════════════════════════════════════════════════════════
# SEGMENTOS
# ═══════════════════════════════════════════════════════════════════════════════

# ── SEG 1 (0–5s): Logo fade in ────────────────────────────────────────────────
def seg1_logo(t):
    """Logo FARO PROTOCOL con fade in."""
    img, draw = new_frame()

    # Líneas decorativas horizontales
    for y_off in [-200, 200]:
        cy = H//2 + y_off
        a = ease(t/3) * 0.15
        c = (int(GOLD[0]*a), int(GOLD[1]*a), int(GOLD[2]*a))
        draw.line([(W//4, cy), (3*W//4, cy)], fill=c, width=1)

    fa = ease(t / 2.0)   # fade in en 2s

    # Subtítulo encima
    sub_font = font_mono(22)
    sub_text = 'SATELLITE  ECONOMIC  INTELLIGENCE'
    bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
    tw = bbox[2] - bbox[0]
    sx = (W - tw) // 2
    sc = (int(GOLD_L[0]*fa*0.7), int(GOLD_L[1]*fa*0.7), int(GOLD_L[2]*fa*0.7))
    draw.text((sx, H//2 - 120), sub_text, font=sub_font, fill=sc)

    # Logo principal
    logo_font = font_serif(110)
    logo_text = 'FARO PROTOCOL'
    bbox2 = draw.textbbox((0, 0), logo_text, font=logo_font)
    lw = bbox2[2] - bbox2[0]
    lx = (W - lw) // 2
    lc = (int(GOLD[0]*fa), int(GOLD[1]*fa), int(GOLD[2]*fa))
    draw.text((lx, H//2 - 80), logo_text, font=logo_font, fill=lc)

    # Línea dorada bajo el logo
    line_w = int(lw * ease(max(0, (t-1)/2)))
    lx_center = W // 2
    lc2 = (int(GOLD[0]*fa*0.6), int(GOLD[1]*fa*0.6), int(GOLD[2]*fa*0.6))
    if line_w > 0:
        draw.line([(lx_center - line_w//2, H//2 + 60),
                   (lx_center + line_w//2, H//2 + 60)], fill=lc2, width=2)

    # Tagline
    if t > 2.0:
        ta = ease((t - 2.0) / 1.5)
        tag_font = font_mono(28)
        tag_text = 'Sepa qué pasa en el terreno 48h antes que el mercado.'
        bbox3 = draw.textbbox((0, 0), tag_text, font=tag_font)
        tw3 = bbox3[2] - bbox3[0]
        tx3 = (W - tw3) // 2
        tc = (int(WHITE3[0]*ta), int(WHITE3[1]*ta), int(WHITE3[2]*ta))
        draw.text((tx3, H//2 + 120), tag_text, font=tag_font, fill=tc)

    return to_arr(img)


# ── SEG 2 (5–15s): Mapa global — zonas parpadeando ───────────────────────────
def seg2_map(t):
    """Mapa con 9 zonas apareciendo una por una."""
    img, draw = new_frame()
    draw_map_grid(draw, alpha=0.12)

    total_zones = len(ZONES)
    zone_interval = 10.0 / total_zones   # ~1.1s por zona

    f_title = font_serif(48)
    f_label = font_mono(18)
    f_sector = font_mono(14)

    # Título
    ta = ease(t / 1.0)
    tc = (int(GOLD[0]*ta*0.8), int(GOLD[1]*ta*0.8), int(GOLD[2]*ta*0.8))
    centered_text(draw, 30, 'FARO PROTOCOL  ·  GLOBAL COVERAGE', f_title, GOLD, ta*0.8)

    for i, (name, sector, lon, lat, color) in enumerate(ZONES):
        zone_appear_t = i * zone_interval
        if t < zone_appear_t:
            continue

        elapsed = t - zone_appear_t
        progress = min(1.0, elapsed / 0.8)
        fa = ease_out(progress)

        cx, cy = lonlat_to_px(lon, lat)

        # Glow rings pulsantes
        pulse = (math.sin(t * 3 + i * 0.7) + 1) / 2   # 0–1
        for ring in [3, 2, 1]:
            rsize = 8 + ring * 8 + int(pulse * 6)
            ralpha = fa * (0.06 + ring * 0.02) * (0.5 + pulse * 0.5)
            rc = (int(color[0]*ralpha), int(color[1]*ralpha), int(color[2]*ralpha))
            draw.ellipse([cx-rsize, cy-rsize, cx+rsize, cy+rsize], outline=rc, width=1)

        # Punto central
        dot_r = 5
        dc = (int(color[0]*fa), int(color[1]*fa), int(color[2]*fa))
        draw.ellipse([cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r], fill=dc)

        # Etiqueta
        if progress > 0.3:
            la = ease((progress - 0.3) / 0.7)
            lc = (int(color[0]*la), int(color[1]*la), int(color[2]*la))
            sc_c = (int(WHITE3[0]*la*0.7), int(WHITE3[1]*la*0.7), int(WHITE3[2]*la*0.7))

            # Offset etiqueta para evitar solapamientos
            offsets = [(14, -24), (14, 8), (-90, -24), (14, -24),
                       (14, 8), (14, -24), (-85, 8), (14, -24), (14, 8)]
            ox, oy = offsets[i]

            draw.text((cx + ox, cy + oy), name, font=f_label, fill=lc)
            draw.text((cx + ox, cy + oy + 20), sector, font=f_sector, fill=sc_c)

    # Contador de zonas activas
    n_visible = sum(1 for i in range(total_zones) if t >= i * zone_interval)
    counter_font = font_mono(26)
    counter_text = f'{n_visible}  /  9  ZONAS ACTIVAS'
    cta = ease(min(1, t / 2))
    cc = (int(WHITE3[0]*cta*0.7), int(WHITE3[1]*cta*0.7), int(WHITE3[2]*cta*0.7))
    draw.text((60, H - 70), counter_text, font=counter_font, fill=cc)

    # Timestamp
    ts_font = font_mono(16)
    draw.text((W - 420, H - 50), '2026-04-09  ·  SENTINEL-1/2  ·  COPERNICUS', font=ts_font,
              fill=(int(GOLD[0]*0.35), int(GOLD[1]*0.35), int(GOLD[2]*0.35)))

    return to_arr(img)


# ── SEG 3 (15–25s): Reporte Balcarce ─────────────────────────────────────────
_balcarce_img = None
def _get_balcarce():
    global _balcarce_img
    if _balcarce_img is None:
        raw = load_asset('faro_reporte_fusion_balcarce.png')
        _balcarce_img = fit_into(raw, W, H) if raw else None
    return _balcarce_img

def seg3_balcarce(t):
    """Reporte Balcarce — slide in + datos superpuestos."""
    base = _get_balcarce()

    if base is None:
        img, draw = new_frame()
        centered_text(draw, H//2, 'faro_reporte_fusion_balcarce.png not found',
                      font_mono(30), RED)
        return to_arr(img)

    # Slide desde la izquierda en los primeros 2s
    progress = min(1.0, t / 2.0)
    canvas = slide_in(base, W, H, progress, 'left')

    draw = ImageDraw.Draw(canvas)

    # Panel superior de datos (aparece después del slide)
    if t > 1.5:
        pa = ease((t - 1.5) / 1.5)

        # Barra superior semi-transparente
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([0, 0, W, 110], fill=(10, 10, 26, int(200 * pa)))
        canvas = Image.alpha_composite(canvas.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(canvas)

        # Zona
        zone_font = font_serif(56)
        gc = (int(GOLD[0]*pa), int(GOLD[1]*pa), int(GOLD[2]*pa))
        draw.text((60, 20), 'BALCARCE  ·  Argentina', font=zone_font, fill=gc)

        # Métricas inline
        m_font = font_mono(26)
        mc = (int(WHITE[0]*pa), int(WHITE[1]*pa), int(WHITE[2]*pa))
        metrics = [
            ('NDVI',   '0.5328'),
            ('SAR VV', '15.5 dB'),
            ('SCORE',  '70 / 100'),
            ('ESTADO', 'OK ✓'),
        ]
        mx = 60
        for label, val in metrics:
            lc2 = (int(WHITE3[0]*pa), int(WHITE3[1]*pa), int(WHITE3[2]*pa))
            draw.text((mx, H - 120), label, font=font_mono(16), fill=lc2)
            vc = (int(GOLD_L[0]*pa), int(GOLD_L[1]*pa), int(GOLD_L[2]*pa))
            draw.text((mx, H - 98), val, font=font_mono(28), fill=vc)
            mx += 260

    # Score highlight al final del segmento
    if t > 7.0:
        sa = ease((t - 7.0) / 2.0)
        sh_font = font_serif(120)
        sc2 = (int(GOLD[0]*sa*0.6), int(GOLD[1]*sa*0.6), int(GOLD[2]*sa*0.6))
        score_str = '70'
        bbox = draw.textbbox((0, 0), score_str, font=sh_font)
        sw = bbox[2] - bbox[0]
        draw.text((W - sw - 100, H//2 - 80), score_str, font=sh_font, fill=sc2)
        draw.text((W - sw - 95, H//2 + 55), '/ 100', font=font_mono(28), fill=sc2)

    return to_arr(canvas)


# ── SEG 4 (25–35s): Reporte Amazonas ─────────────────────────────────────────
_amazonas_img = None
def _get_amazonas():
    global _amazonas_img
    if _amazonas_img is None:
        raw = load_asset('faro_reporte_fusion_amazonas.png')
        _amazonas_img = fit_into(raw, W, H) if raw else None
    return _amazonas_img

def seg4_amazonas(t):
    """Reporte Amazonas — slide desde derecha + FSI."""
    base = _get_amazonas()

    if base is None:
        img, draw = new_frame()
        centered_text(draw, H//2, 'faro_reporte_fusion_amazonas.png not found',
                      font_mono(30), RED)
        return to_arr(img)

    progress = min(1.0, t / 2.0)
    canvas = slide_in(base, W, H, progress, 'right')
    draw = ImageDraw.Draw(canvas)

    if t > 1.5:
        pa = ease((t - 1.5) / 1.5)

        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle([0, 0, W, 110], fill=(10, 10, 26, int(200 * pa)))
        canvas = Image.alpha_composite(canvas.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(canvas)

        gc = (int(GOLD[0]*pa), int(GOLD[1]*pa), int(GOLD[2]*pa))
        draw.text((60, 20), 'AMAZONAS  ·  Brasil', font=font_serif(56), fill=gc)

        mx = 60
        metrics = [
            ('NDVI',  '0.2834'),
            ('FSI',   '36.0  Leve'),
            ('SCORE', '66 / 100'),
            ('COBERTURA', '72% bosque'),
        ]
        for label, val in metrics:
            lc2 = (int(WHITE3[0]*pa), int(WHITE3[1]*pa), int(WHITE3[2]*pa))
            draw.text((mx, H - 120), label, font=font_mono(16), fill=lc2)
            # Verde para deforestación
            vc = (int(GREEN[0]*pa*0.8), int(GREEN[1]*pa*0.8), int(GREEN[2]*pa*0.8))
            draw.text((mx, H - 98), val, font=font_mono(28), fill=vc)
            mx += 280

    # FSI callout
    if t > 6.0:
        fa = ease((t - 6.0) / 2.5)
        fsi_font = font_serif(100)
        fc = (int(GREEN[0]*fa*0.5), int(GREEN[1]*fa*0.5), int(GREEN[2]*fa*0.5))
        draw.text((W - 380, H//2 - 80), 'FSI', font=font_mono(40), fill=fc)
        draw.text((W - 380, H//2 - 30), '36.0', font=fsi_font, fill=fc)
        draw.text((W - 370, H//2 + 80), 'LEVE', font=font_mono(28), fill=fc)

    return to_arr(canvas)


# ── SEG 5 (35–45s): Sello Verde + SHA-256 ────────────────────────────────────
_cert_balcarce_img = None
def _get_cert():
    global _cert_balcarce_img
    if _cert_balcarce_img is None:
        raw = load_asset('faro_cert_balcarce.png')
        _cert_balcarce_img = fit_into(raw, W, H) if raw else None
    return _cert_balcarce_img

SHA256_BALCARCE = '5233ec238815393a6e98fdb11b42a9f86574df9586258b16c34980f26f4a8f7d'

def seg5_sello(t):
    """Sello Verde con SHA-256 real."""
    img, draw = new_frame()

    # Fondo con viñeta verde suave
    if t > 1.0:
        va = ease((t - 1.0) / 3.0) * 0.08
        overlay = Image.new('RGB', (W, H), (0, 200, 50))
        img = Image.blend(img, overlay, va)
        draw = ImageDraw.Draw(img)

    # Certificado al fondo (faded)
    cert = _get_cert()
    if cert:
        ca = ease(t / 2.0) * 0.55
        img = Image.blend(img, cert, ca)
        draw = ImageDraw.Draw(img)

    # Sello Verde central
    sa = ease(max(0, t - 0.5) / 2.0)

    cx, cy = W//2, H//2 - 40

    # Círculo exterior glow
    if sa > 0:
        for r in range(220, 100, -20):
            ga = sa * (1 - (r - 100) / 120) * 0.06
            gc = (int(GREEN[0]*ga), int(GREEN[1]*ga), int(GREEN[2]*ga))
            draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=gc, width=2)

        # Borde principal
        bc = (int(GREEN[0]*sa), int(GREEN[1]*sa), int(GREEN[2]*sa))
        draw.ellipse([cx-110, cy-110, cx+110, cy+110], outline=bc, width=3)

        # Texto del sello
        tf = font_serif(52)
        check_f = font_sans(72)
        cc = (int(GREEN[0]*sa), int(GREEN[1]*sa), int(GREEN[2]*sa))
        centered_text(draw, cy - 50, '✓', check_f, GREEN, sa)
        centered_text(draw, cy + 30, 'VERIFIED', font_mono(28), GREEN, sa * 0.9)
        centered_text(draw, cy + 62, 'SELLO VERDE', font_mono(20), GREEN, sa * 0.7)

    # SHA-256 typewriter effect
    if t > 3.0:
        sha_elapsed = t - 3.0
        sha_progress = min(1.0, sha_elapsed / 4.0)   # 4s para escribir el hash
        chars_to_show = int(sha_progress * len(SHA256_BALCARCE))
        sha_visible = SHA256_BALCARCE[:chars_to_show]

        sha_font = font_mono(22)
        sha_label = font_mono(16)

        sha_a = ease(sha_elapsed / 1.0)
        lc2 = (int(WHITE3[0]*sha_a*0.8), int(WHITE3[1]*sha_a*0.8), int(WHITE3[2]*sha_a*0.8))
        sc3 = (int(GREEN[0]*sha_a*0.8), int(GREEN[1]*sha_a*0.8), int(GREEN[2]*sha_a*0.8))

        # Panel SHA
        draw.rectangle([W//2 - 600, H - 200, W//2 + 600, H - 100],
                        fill=(13, 16, 32))
        draw.rectangle([W//2 - 600, H - 200, W//2 + 600, H - 100],
                        outline=(int(GREEN[0]*0.4), int(GREEN[1]*0.4), int(GREEN[2]*0.4)),
                        width=1)
        draw.text((W//2 - 580, H - 188), 'SHA-256  ·  BALCARCE', font=sha_label, fill=lc2)
        draw.text((W//2 - 580, H - 164), sha_visible, font=sha_font, fill=sc3)

        # Cursor parpadeante
        if chars_to_show < len(SHA256_BALCARCE):
            cursor_vis = int(t * 4) % 2 == 0
            if cursor_vis:
                bbox = draw.textbbox((0, 0), sha_visible, font=sha_font)
                cursor_x = W//2 - 580 + bbox[2]
                draw.rectangle([cursor_x, H - 164, cursor_x + 12, H - 144],
                                fill=GREEN)

    # Área y fecha
    if t > 1.0:
        aa = ease((t - 1.0) / 2.0)
        ac = (int(WHITE3[0]*aa*0.6), int(WHITE3[1]*aa*0.6), int(WHITE3[2]*aa*0.6))
        info_f = font_mono(18)
        draw.text((60, H - 60),
                  'Balcarce, Buenos Aires  ·  Score 70  ·  2026-04-09  ·  Sentinel-2 L2A',
                  font=info_f, fill=ac)

    return to_arr(img)


# ── SEG 6 (45–55s): Frases de impacto ────────────────────────────────────────
IMPACT_LINES = [
    ('48 horas antes que el mercado.',  font_serif,  80, GOLD,   2.0),
    ('Verificado con SHA-256.',          font_serif,  60, WHITE,  4.0),
    ('9 zonas globales · 6 sectores.',   font_mono,   36, WHITE3, 6.0),
    ('Faro Protocol.',                   font_serif,  48, GOLD,   8.0),
]

def seg6_frases(t):
    """Frases de impacto una por una."""
    img, draw = new_frame()

    # Líneas decorativas
    la = ease(t / 2.0) * 0.2
    lc2 = (int(GOLD[0]*la), int(GOLD[1]*la), int(GOLD[2]*la))
    draw.line([(W//4, H//2 + 230), (3*W//4, H//2 + 230)], fill=lc2, width=1)

    y_positions = [H//2 - 180, H//2 - 80, H//2 + 20, H//2 + 100]

    for i, (text, font_fn, size, color, appear_at) in enumerate(IMPACT_LINES):
        if t < appear_at:
            continue
        fa = ease(min(1.0, (t - appear_at) / 1.2))
        centered_text(draw, y_positions[i], text, font_fn(size), color, fa)

    return to_arr(img)


# ── SEG 7 (55–60s): URL + fade out ───────────────────────────────────────────
def seg7_outro(t):
    """URL final + fade to black."""
    fade_start = 3.5
    fade_dur   = 1.5
    if t >= fade_start:
        fade_a = min(1.0, (t - fade_start) / fade_dur)
    else:
        fade_a = 0.0

    img, draw = new_frame()

    show_a = ease(min(1.0, t / 1.5))

    # Logo
    logo_f = font_serif(72)
    lc2 = (int(GOLD[0]*show_a), int(GOLD[1]*show_a), int(GOLD[2]*show_a))
    centered_text(draw, H//2 - 120, 'FARO PROTOCOL', logo_f, GOLD, show_a)

    # Línea
    line_a = ease(min(1.0, max(0, t - 0.5) / 1.0)) * show_a * 0.5
    lw2 = int(500 * ease(min(1, t / 2)))
    if lw2 > 0:
        lx2 = W//2
        lc3 = (int(GOLD[0]*line_a), int(GOLD[1]*line_a), int(GOLD[2]*line_a))
        draw.line([(lx2 - lw2//2, H//2 - 40), (lx2 + lw2//2, H//2 - 40)], fill=lc3, width=1)

    # URL
    url_f = font_mono(38)
    uc = (int(WHITE[0]*show_a), int(WHITE[1]*show_a), int(WHITE[2]*show_a))
    centered_text(draw, H//2 - 10, 'faro-protocol.netlify.app', url_f, WHITE, show_a)

    # Subtexto
    sub_f = font_mono(20)
    sc2 = (int(WHITE3[0]*show_a*0.7), int(WHITE3[1]*show_a*0.7), int(WHITE3[2]*show_a*0.7))
    centered_text(draw, H//2 + 50, 'protocolfaro@gmail.com', sub_f, WHITE3, show_a*0.7)

    # SHA-256 como decoración
    sha_f = font_mono(13)
    sha_a = show_a * 0.25
    sha_c = (int(GOLD[0]*sha_a), int(GOLD[1]*sha_a), int(GOLD[2]*sha_a))
    centered_text(draw, H//2 + 120,
                  SHA256_BALCARCE[:32] + '...',
                  sha_f, GOLD, sha_a)

    # Fade a negro
    if fade_a > 0:
        img = fade_black(img, fade_a)

    return to_arr(img)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSICIÓN Y EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def build_video():
    from moviepy import VideoClip, concatenate_videoclips

    print('Construyendo segmentos...')

    segments = [
        ('Logo (0–3s)',      3.0,  seg1_logo),
        ('Mapa (3–11s)',     8.0,  seg2_map),
        ('Balcarce (11–18s)',7.0,  seg3_balcarce),
        ('Amazonas (18–25s)',7.0,  seg4_amazonas),
        ('Sello (25–35s)',   10.0, seg5_sello),
        ('Frases (35–45s)',  10.0, seg6_frases),
        ('Outro (45–50s)',    5.0, seg7_outro),
    ]

    clips = []
    for name, dur, fn in segments:
        print(f'  > {name}')
        clip = VideoClip(fn, duration=dur)
        clip = clip.with_fps(FPS)
        clips.append(clip)

    print('Concatenando...')
    final = concatenate_videoclips(clips, method='compose')

    out_path = str(ROOT / 'faro_demo.mp4')
    print(f'Exportando {out_path}  ({final.duration:.0f}s @ {FPS}fps, {W}×{H})')
    final.write_videofile(
        out_path,
        fps=FPS,
        codec='libx264',
        audio=False,
        preset='fast',
        ffmpeg_params=['-crf', '22', '-pix_fmt', 'yuv420p'],
        logger='bar',
    )
    print(f'\nOK Video generado: {out_path}')
    return out_path


if __name__ == '__main__':
    # Pre-cargar assets
    print('Cargando assets...')
    _get_balcarce()
    _get_amazonas()
    _get_cert()

    build_video()
