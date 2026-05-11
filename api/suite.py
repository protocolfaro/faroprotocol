import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from flask import Flask, request, jsonify
from flask_cors import CORS
import faro_engine_suite as suite

app = Flask(__name__)
CORS(app)


@app.route('/', methods=['POST'])
def suite_endpoint():
    body   = request.get_json(silent=True) or {}
    lat    = float(body.get('lat', 0))
    lon    = float(body.get('lon', 0))
    sector = str(body.get('sector', 'agro')).lower()
    return jsonify(suite.run_suite(lat, lon, sector))


handler = app
