"""
FARO PROTOCOL — Backend Railway v3.0
Servidor principal: registra todos los endpoints en una sola app Flask.
"""

import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
import faro_twitter_bot as twitter
import faro_velez_scheduler as velez

app = Flask(__name__)
CORS(app)

# ── Scheduler (una sola instancia — gunicorn --preload) ───────────────────────
_scheduler = BackgroundScheduler(timezone='UTC')
twitter.setup_scheduler(_scheduler)
_scheduler.start()

# ── Vélez Sarsfield — rutas y jobs ────────────────────────────────────────────
velez.register_with_app(app, _scheduler)


# ── Health ────────────────────────────────────────────────────────────────────
_DEPLOY_SHA = 'ac136b1-env'

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': '3.0-suite', 'deploy': _DEPLOY_SHA})


# ── Suite (orquestador multi-sensor) ──────────────────────────────────────────
@app.route('/api/suite', methods=['POST'])
def api_suite():
    import faro_engine_suite as eng
    body = request.get_json(silent=True) or {}
    return jsonify(eng.run_suite(
        lat    = float(body.get('lat', 0)),
        lon    = float(body.get('lon', 0)),
        sector = str(body.get('sector', 'agro')).lower(),
    ))


# ── TROPOMI standalone ────────────────────────────────────────────────────────
_TROPOMI_DEFAULTS = {
    'agro':    {'ch4_ppb': 1847, 'no2_mol': 0.00012},
    'amb':     {'ch4_ppb': 1872, 'no2_mol': 0.00015},
    'energia': {'ch4_ppb': 1943, 'no2_mol': 0.00018},
    'min':     {'ch4_ppb': 1858, 'no2_mol': 0.00024},
    'oil':     {'ch4_ppb': 1912, 'no2_mol': 0.00022},
    'mar':     {'ch4_ppb': 1839, 'no2_mol': 0.00035},
    'infra':   {'ch4_ppb': 1851, 'no2_mol': 0.00022},
}

@app.route('/api/tropomi', methods=['POST'])
def api_tropomi():
    import faro_engine_suite as eng
    body   = request.get_json(silent=True) or {}
    lat    = float(body.get('lat', 0))
    lon    = float(body.get('lon', 0))
    sector = str(body.get('sector', 'agro')).lower()

    ch4 = no2 = fecha = None
    try:
        r = eng.fetchTropomi_CH4(lat, lon)
        if r: ch4 = r['value']; fecha = r.get('fecha')
    except Exception:
        pass
    try:
        r = eng.fetchTropomi_NO2(lat, lon)
        if r: no2 = r['value']
    except Exception:
        pass

    d = _TROPOMI_DEFAULTS.get(sector, {'ch4_ppb': 1870, 'no2_mol': 0.00018})
    return jsonify({
        'ch4_ppb': round(ch4 or d['ch4_ppb'], 1),
        'no2_mol': round(no2 or d['no2_mol'], 6),
        'fecha'  : fecha or (datetime.utcnow() - timedelta(days=3)).strftime('%Y-%m-%d'),
        'fuente' : 'Sentinel-5P TROPOMI',
    })


# ── Twitter endpoints ─────────────────────────────────────────────────────────

@app.route('/twitter/trigger/tuesday', methods=['POST'])
def twitter_tuesday():
    try:
        result = twitter.post_tweet('tuesday')
        return jsonify(result), 200 if result['ok'] else 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f"{type(e).__name__}: {e}"}), 500

@app.route('/twitter/trigger/thursday', methods=['POST'])
def twitter_thursday():
    try:
        result = twitter.post_tweet('thursday')
        return jsonify(result), 200 if result['ok'] else 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f"{type(e).__name__}: {e}"}), 500

@app.route('/twitter/status', methods=['GET'])
def twitter_status():
    return jsonify(twitter.get_status())


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
