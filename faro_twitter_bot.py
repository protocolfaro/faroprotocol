"""
FARO PROTOCOL — Twitter/X Bot
Posts automáticos mar/jue 14:00 UTC · Interacciones lun/mié/vie 15:30 UTC
"""

import os
import json
import random
import time
import logging
from datetime import datetime, date
from pathlib import Path

import tweepy
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger('faro.twitter')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [TWITTER] %(levelname)s — %(message)s'
)

BASE_DIR     = Path(__file__).parent
HISTORY_FILE = BASE_DIR / 'twitter_history.json'

# ── Auth ──────────────────────────────────────────────────────────────────────

def _client():
    return tweepy.Client(
        bearer_token         = os.getenv('TWITTER_BEARER_TOKEN'),
        consumer_key         = os.getenv('TWITTER_API_KEY'),
        consumer_secret      = os.getenv('TWITTER_API_SECRET'),
        access_token         = os.getenv('TWITTER_ACCESS_TOKEN'),
        access_token_secret  = os.getenv('TWITTER_ACCESS_TOKEN_SECRET'),
        wait_on_rate_limit   = True,
    )

# ── Historial ─────────────────────────────────────────────────────────────────

def _load() -> dict:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'posts': [],
        'interactions': [],
        'tuesday_idx': -1,
        'thursday_idx': -1,
        'today_interactions': 0,
        'today_date': None,
    }

def _save(h: dict):
    HISTORY_FILE.write_text(
        json.dumps(h, indent=2, ensure_ascii=False),
        encoding='utf-8'
    )

def _reset_daily_if_needed(h: dict) -> dict:
    today = date.today().isoformat()
    if h.get('today_date') != today:
        h['today_interactions'] = 0
        h['today_date'] = today
    return h

# ── Contenido ─────────────────────────────────────────────────────────────────
# Cada bloque tiene 3 variantes para nunca repetir el mismo texto exacto.

TUESDAY = [
    {
        'type': 'A',
        'variants': [
            "🛰️ FARO PROTOCOL · Monitoreo activo\nZona: Balcarce, Buenos Aires\nNDVI: 0.533 · SAR: +15.48 dB · Fusión: 0.76\nÍndice FARO: 69.5 — ÓPTIMO ✅\nEvidencia física inmutable. SHA-256 certificado.\n#FaroProtocol #Agro #SAR #VerdadFisica",
            "🛰️ FARO PROTOCOL · En vivo\nZona: Marcos Juárez, Córdoba\nNDVI: 0.518 · SAR: +14.92 dB · Fusión: 0.74\nÍndice FARO: 67.3 — ÓPTIMO ✅\nDatos reales. Sellados en blockchain.\n#FaroProtocol #Agro #SAR #VerdadFisica",
            "🛰️ FARO PROTOCOL · Reporte satelital\nZona: Gral. Pueyrredón, Buenos Aires\nNDVI: 0.491 · SAR: +13.77 dB · Fusión: 0.71\nÍndice FARO: 64.8 — ÓPTIMO ✅\nEvidencia inmutable para banco, seguro o comprador.\n#FaroProtocol #Agro #Sentinel1 #VerdadFisica",
        ],
    },
    {
        'type': 'B',
        'variants': [
            "¿Sabías que el radar SAR atraviesa nubes, polvo y noche?\nMientras otros están ciegos, Faro Protocol sigue monitoreando.\n24/7. Sin excusas climáticas.\n#SAR #Sentinel1 #MonitoreoSatelital #Agro",
            "El radar SAR no conoce el mal tiempo.\nNubes, lluvia, madrugada: Faro Protocol monitorea igual.\nDatos reales cuando más los necesitás.\n#SAR #Sentinel1 #AgTech #FaroProtocol",
            "Sentinel-1 vuela a 700km de altura y atraviesa cualquier nube.\nAsí funciona el monitoreo real de Faro Protocol.\n24/7. Sin drones. Sin visitas al campo.\n#SAR #Sentinel1 #MonitoreoSatelital #VerdadFisica",
        ],
    },
]

THURSDAY = [
    {
        'type': 'C',
        'variants': [
            "Un siniestro mal liquidado le cuesta al productor\nentre USD 5.000 y USD 50.000.\nEl certificado Faro cuesta USD 650.\nUna vez que lo usó, se paga solo. 🔐\n#Seguros #Agro #CertificacionSatelital #FaroProtocol",
            "El ajustador decide a su favor cuando no hay evidencia.\nFaro Protocol le da evidencia técnica inmutable.\nSHA-256 sellado. Válido ante cualquier institución.\nUSD 650. Entrega 24 horas. 🔐\n#Seguros #Agro #FaroProtocol #VerdadFisica",
            "¿Cuánto perdió en el último siniestro por no tener pruebas?\nFaro Protocol certifica el estado de su campo desde el espacio.\nUn solo certificado puede recuperar USD 10.000+.\n#Seguros #Agro #CertificacionSatelital #FaroProtocol",
        ],
    },
    {
        'type': 'D',
        'variants': [
            "🏭 Vaca Muerta bajo monitoreo satelital.\nEmisiones CH4 detectadas vía TROPOMI.\nDeformación milimétrica via InSAR.\nTrazabilidad inmutable para inversores RIGI.\n#VacaMuerta #OilGas #RIGI #FaroProtocol",
            "🏭 InSAR detecta subsidencia milimétrica en yacimientos.\nFaro Protocol monitorea Vaca Muerta desde el espacio.\nDatos certificados SHA-256 para due diligence RIGI.\n#VacaMuerta #OilGas #InSAR #FaroProtocol",
            "🛰️ Sentinel-1 sobre Neuquén: deformación de suelo detectada.\nFaro Protocol procesa y certifica en 24 horas.\nEvidencia técnica para operadores y reguladores.\n#VacaMuerta #RIGI #OilGas #FaroProtocol",
        ],
    },
    {
        'type': 'E',
        'variants': [
            "⛏️ Pilbara, Australia. Delta SAR: 2.1mm detectado.\nMovimiento estructural monitoreado desde el espacio.\nSin drones. Sin sensores. Sin desplazamiento de personal.\n#Mining #InSAR #FaroProtocol #Mineria",
            "⛏️ Monitoreo de estabilidad en minería a cielo abierto.\nFaro Protocol detecta movimientos de talud con InSAR.\nAlerta temprana antes de un evento crítico.\n#Mining #InSAR #Mineria #FaroProtocol",
            "⛏️ 2.4mm de subsidencia detectados en 12 días.\nAsí trabaja InSAR para seguridad en minería.\nFaro Protocol: evidencia satelital para decisiones críticas.\n#Mining #InSAR #FaroProtocol #Mineria",
        ],
    },
]

HASHTAGS_MONITOR = ['#VacaMuerta', '#RIGI', '#Agro', '#MineriaArgentina', '#OilGas']

REPLIES = [
    "Interesante. En @FaroProtocol certificamos esto con evidencia satelital inmutable. ¿Lo conocés?",
    "Exactamente. Por eso en @FaroProtocol usamos radar SAR + SHA-256 para certificar lo que pasa en el campo. ¿Conocés el servicio?",
    "Buen punto. @FaroProtocol hace exactamente eso: evidencia física desde el espacio, certificada e inmutable. Miralo.",
    "Totalmente. La trazabilidad satelital cambia el juego. @FaroProtocol certifica agro, oil & gas y minería con SHA-256.",
    "Sí. En @FaroProtocol trabajamos con Sentinel-1 SAR para darte evidencia que ningún ajustador puede disputar. ¿Lo conocés?",
]

# ── Anti-ban helpers ──────────────────────────────────────────────────────────

def _pick_variant(pool: list, history_key: str, h: dict) -> tuple[dict, int]:
    """Rota entre bloques y elige variante no repetida en los últimos 20 posts."""
    last_idx = h.get(history_key, -1)
    idx = (last_idx + 1) % len(pool)
    block = pool[idx]
    used = {p['text'] for p in h.get('posts', [])[-20:]}
    choices = [v for v in block['variants'] if v not in used] or block['variants']
    return block, idx, random.choice(choices)

def _jitter(lo=30, hi=120):
    t = random.randint(lo, hi)
    log.info(f"Anti-ban delay: {t}s")
    time.sleep(t)

# ── Post ──────────────────────────────────────────────────────────────────────

def post_tweet(day: str = 'tuesday') -> dict:
    """
    Postea un tweet.
    day: 'tuesday' (tipos A/B) | 'thursday' (tipos C/D/E)
    """
    h = _load()
    pool       = TUESDAY   if day == 'tuesday' else THURSDAY
    hist_key   = 'tuesday_idx' if day == 'tuesday' else 'thursday_idx'
    block, idx, text = _pick_variant(pool, hist_key, h)

    try:
        _jitter(5, 20)
        resp     = _client().create_tweet(text=text)
        tweet_id = resp.data['id']

        h['posts'].append({
            'id':        tweet_id,
            'type':      block['type'],
            'text':      text,
            'timestamp': datetime.utcnow().isoformat(),
            'day':       day,
        })
        h[hist_key] = idx
        _save(h)

        log.info(f"✅ Tweet publicado — ID {tweet_id} — Tipo {block['type']}")
        return {'ok': True, 'tweet_id': tweet_id, 'type': block['type'], 'text': text}

    except tweepy.TweepyException as e:
        log.error(f"Error al postear: {e}")
        return {'ok': False, 'error': str(e)}

# ── Interacciones ──────────────────────────────────────────────────────────────

def run_interactions() -> dict:
    """Like + reply sutil en tweets con hashtags objetivo. Máx 5/día."""
    h = _reset_daily_if_needed(_load())

    if h['today_interactions'] >= 5:
        log.info("Límite diario de interacciones alcanzado (5/5).")
        return {'ok': True, 'skipped': True, 'reason': 'daily_limit'}

    client    = _client()
    me_id     = client.get_me().data.id
    done_ids  = {i['tweet_id'] for i in h.get('interactions', [])[-100:]}
    interacted = 0
    max_new   = 5 - h['today_interactions']

    for hashtag in random.sample(HASHTAGS_MONITOR, len(HASHTAGS_MONITOR)):
        if interacted >= max_new:
            break
        try:
            results = client.search_recent_tweets(
                query=f"{hashtag} -is:retweet lang:es",
                max_results=10,
                tweet_fields=['author_id'],
            )
            if not results.data:
                continue

            for tweet in results.data:
                if interacted >= max_new:
                    break
                if str(tweet.id) in done_ids or tweet.author_id == me_id:
                    continue

                _jitter(30, 120)
                client.like(tweet.id)
                client.create_tweet(
                    text=random.choice(REPLIES),
                    in_reply_to_tweet_id=tweet.id,
                )

                h['interactions'].append({
                    'tweet_id':  str(tweet.id),
                    'hashtag':   hashtag,
                    'timestamp': datetime.utcnow().isoformat(),
                })
                h['today_interactions'] += 1
                done_ids.add(str(tweet.id))
                interacted += 1
                log.info(f"✅ Like + reply → {tweet.id} ({hashtag})")

        except tweepy.TweepyException as e:
            log.warning(f"Error con {hashtag}: {e}")

    _save(h)
    log.info(f"Interacciones completadas: {interacted} nuevas.")
    return {'ok': True, 'interactions_done': interacted}

# ── Scheduler ─────────────────────────────────────────────────────────────────

def setup_scheduler(scheduler) -> None:
    """Registra los jobs en el APScheduler de Railway."""
    scheduler.add_job(
        lambda: post_tweet('tuesday'),
        trigger='cron', day_of_week='tue', hour=14, minute=0,
        id='tw_tuesday', replace_existing=True,
    )
    scheduler.add_job(
        lambda: post_tweet('thursday'),
        trigger='cron', day_of_week='thu', hour=14, minute=0,
        id='tw_thursday', replace_existing=True,
    )
    scheduler.add_job(
        run_interactions,
        trigger='cron', day_of_week='mon,wed,fri', hour=15, minute=30,
        id='tw_interactions', replace_existing=True,
    )
    log.info("✅ Jobs de Twitter registrados (mar/jue 14:00 UTC · lun/mié/vie 15:30 UTC)")

# ── Status ────────────────────────────────────────────────────────────────────

def get_status() -> dict:
    h     = _load()
    today = date.today().isoformat()
    return {
        'last_10_posts':       h.get('posts', [])[-10:],
        'total_posts':         len(h.get('posts', [])),
        'total_interactions':  len(h.get('interactions', [])),
        'today_interactions':  h.get('today_interactions', 0) if h.get('today_date') == today else 0,
        'schedule': {
            'posts':        'martes y jueves 14:00 UTC',
            'interactions': 'lunes, miércoles y viernes 15:30 UTC',
        },
    }
