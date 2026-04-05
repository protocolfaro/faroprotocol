"""
FARO PROTOCOL — Actualizador de precios de mercado (faro_price_updater.py)

Corre automáticamente todos los lunes a las 08:00 hora Argentina (ART, UTC-3).
En cada ejecución:
  1. Consulta precios actuales de mercado desde fuentes públicas
  2. Actualiza datos/faro_prices.json  (consumido por faro_finance.py)
  3. Regenera el Business Case con los valores nuevos
  4. Actualiza la sección 'finance' de data.json
  5. Guarda historial en datos/faro_prices_log.json
  6. Notifica via Paperclip (LLM si hay API key, terminal si no)

Fuentes de datos (todas públicas, sin autenticación requerida):
  Urea USD/MT   : IndexMundi — FusionCharts XML embed
                  https://www.indexmundi.com/commodities/?commodity=urea
  Brent USD/bbl : Yahoo Finance API — precio de cierre más reciente
                  https://query1.finance.yahoo.com/v8/finance/chart/BZ=F
  USD/ARS       : dolarapi.com — tipo de cambio oficial publicado
                  https://dolarapi.com/v1/dolares/oficial
  Fallback ARS  : BCRA API oficial
                  https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones

Cálculos derivados:
  Diesel USD/L  = (Brent_USD_bbl / 158.987) × DIESEL_REFINING_MARKUP
  Combustible/ha = BASE_OP_NON_FUEL + Diesel_USD_L × LITROS_POR_HA

Uso:
    python faro_price_updater.py            # ejecutar ahora (modo default)
    python faro_price_updater.py --now      # igual que default
    python faro_price_updater.py --daemon   # scheduler lunes 08:00 ART (proceso continuo)
    python faro_price_updater.py --log      # mostrar historial de precios
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).parent
DATOS_DIR    = PROJECT_ROOT / 'datos'
PRICES_FILE  = DATOS_DIR / 'faro_prices.json'
LOG_FILE     = DATOS_DIR / 'faro_prices_log.json'
DATA_JSON    = PROJECT_ROOT / 'data.json'

# Zona horaria Argentina (UTC-3, sin DST)
ART = timezone(timedelta(hours=-3))

# ─── Constantes de cálculo ─────────────────────────────────────────────────────

# Diesel / gasoil agrícola
BBL_TO_LITER           = 158.987   # 1 barril = 158.987 litros
DIESEL_REFINING_MARKUP = 1.20      # margen de refinación + crack spread (~20% sobre crudo)
LITROS_POR_HA          = 50.0      # litros de gasoil por hectárea, labores totales AR
BASE_OP_NON_FUEL_USD_HA = 30.0     # costo no-combustible (maquinaria, mano obra, etc.)

# Timeout para requests HTTP
HTTP_TIMEOUT = 12  # segundos

# Áreas para las que regenerar el Business Case (solo las que tienen Score en data.json)
AREAS_BUSINESS_CASE = {
    'cordoba':    5_000,   # hectáreas de referencia
    'balcarce':   3_000,
    'indiana':   10_000,
    'rotterdam':  1_200,
    'amazonas':  50_000,
}


# ─── Fetchers de precios ──────────────────────────────────────────────────────

class PriceFetcher:
    """Obtiene precios de mercado desde fuentes públicas con fallback automático."""

    def _get(self, url: str, headers: Optional[dict] = None) -> bytes:
        h = {'User-Agent': 'Mozilla/5.0 FaroProtocol/1.0'}
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.read()

    # ── Urea ──────────────────────────────────────────────────────────────────

    def fetch_urea_usd_tn(self) -> tuple[float, str]:
        """
        Precio de urea granulada en USD/tonelada métrica.
        Fuente: IndexMundi — datos del mercado spot global.
        Retorna (precio, etiqueta_fuente).
        """
        url = 'https://www.indexmundi.com/commodities/?commodity=urea&months=12'
        try:
            html = self._get(url).decode('utf-8', 'replace')
            # Los datos están en el XML del FusionCharts embebido en el JS
            # Formato: <set label='Mon-YYYY' value='NNN.NN' showLabel='1'/>
            sets = re.findall(r"<set label='([^']+)' value='([\d.]+)'", html)
            if sets:
                label, value = sets[-1]
                precio = round(float(value), 2)
                return precio, f"IndexMundi/{label}"
        except Exception as e:
            _log_warn(f"fetch_urea: IndexMundi falló: {e}")

        # Fallback: valor del último log
        cached = _precio_desde_log('PRECIO_UREA_USD_TN')
        if cached is not None:
            return cached, "cache"

        return 600.0, "default"

    # ── Brent crude → diesel ──────────────────────────────────────────────────

    def fetch_brent_usd_bbl(self) -> tuple[float, str]:
        """
        Precio del crudo Brent en USD/barril (contrato de futuros front-month).
        Fuente: Yahoo Finance, símbolo BZ=F.
        """
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1d&range=5d'
        try:
            raw = self._get(url)
            data = json.loads(raw)
            meta = data['chart']['result'][0]['meta']
            precio = meta.get('regularMarketPrice') or meta.get('previousClose')
            if precio and precio > 0:
                return round(float(precio), 2), "Yahoo/BZ=F"
        except Exception as e:
            _log_warn(f"fetch_brent: Yahoo Finance falló: {e}")

        # Fallback: último log
        cached = _precio_desde_log('BRENT_USD_BBL')
        if cached is not None:
            return cached, "cache"

        return 75.0, "default"

    def calcular_diesel_usd_l(self, brent_usd_bbl: float) -> float:
        """
        Estima el precio del gasoil D agrícola en USD/L a partir del crudo Brent.

        Método:
            diesel_usd_l = (brent_usd_bbl / BBL_TO_LITER) × DIESEL_REFINING_MARKUP
            Donde DIESEL_REFINING_MARKUP (~1.20) incluye costos de refinación y
            crack spread promedio histórico del diesel sobre el crudo.

        Esta es la base internacional. El gasoil agrícola argentino se referencia
        a paridad de importación, fluctuando en torno a este benchmark.
        """
        return round((brent_usd_bbl / BBL_TO_LITER) * DIESEL_REFINING_MARKUP, 4)

    def calcular_costo_combustible_ha(self, diesel_usd_l: float) -> float:
        """
        Costo total de labores por hectárea en USD/ha.

        Componentes:
            Combustible  : diesel_usd_l × LITROS_POR_HA (50 L/ha)
            No-combustible: BASE_OP_NON_FUEL_USD_HA (30 USD/ha)
                           — amortización maquinaria, mano de obra, fungicidas

        La parte no-combustible es relativamente estable y se actualiza solo si
        el índice de precios internos de Argentina varía >10% respecto al año previo.
        """
        fuel_cost = diesel_usd_l * LITROS_POR_HA
        return round(BASE_OP_NON_FUEL_USD_HA + fuel_cost, 2)

    # ── Tipo de cambio USD/ARS ────────────────────────────────────────────────

    def fetch_usdars_oficial(self) -> tuple[float, str]:
        """
        Tipo de cambio oficial USD/ARS (pesos argentinos por dólar).
        Fuente primaria : dolarapi.com (interface sobre BCRA)
        Fallback        : BCRA API v1 directa
        """
        # Primario: dolarapi
        try:
            raw = self._get('https://dolarapi.com/v1/dolares/oficial')
            d = json.loads(raw)
            venta = d.get('venta')
            if venta and float(venta) > 0:
                return round(float(venta), 2), "dolarapi/oficial"
        except Exception as e:
            _log_warn(f"fetch_usdars: dolarapi falló: {e}")

        # Fallback: BCRA v1
        try:
            raw = self._get('https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones')
            data = json.loads(raw)
            for item in data['results']['detalle']:
                if item['codigoMoneda'] == 'USD':
                    rate = item.get('tipoCotizacion') or item.get('tipoPase')
                    if rate and float(rate) > 0:
                        return round(float(rate), 2), "BCRA/oficial"
        except Exception as e:
            _log_warn(f"fetch_usdars: BCRA falló: {e}")

        # Cache
        cached = _precio_desde_log('TIPO_CAMBIO_USD_ARS')
        if cached is not None:
            return cached, "cache"

        return 1000.0, "default"

    # ── Ciclo de fetch completo ───────────────────────────────────────────────

    def obtener_todos(self) -> dict:
        """
        Ejecuta todos los fetchers y retorna un dict con los precios y metadatos.
        Cada campo incluye el valor y la fuente de donde se obtuvo.
        """
        print("  Consultando fuentes de mercado...")

        urea_val, urea_src       = self.fetch_urea_usd_tn()
        brent_val, brent_src     = self.fetch_brent_usd_bbl()
        usdars_val, usdars_src   = self.fetch_usdars_oficial()

        diesel_val               = self.calcular_diesel_usd_l(brent_val)
        combustible_ha           = self.calcular_costo_combustible_ha(diesel_val)

        print(f"    Urea          : USD {urea_val}/tn  [{urea_src}]")
        print(f"    Brent         : USD {brent_val}/bbl  [{brent_src}]")
        print(f"    Diesel proxy  : USD {diesel_val}/L")
        print(f"    Combustible/ha: USD {combustible_ha}/ha")
        print(f"    USD/ARS ofic. : {usdars_val}  [{usdars_src}]")

        return {
            "PRECIO_UREA_USD_TN":    urea_val,
            "BRENT_USD_BBL":         brent_val,
            "PRECIO_DIESEL_USD_L":   diesel_val,
            "COSTO_COMBUSTIBLE_HA":  combustible_ha,
            "TIPO_CAMBIO_USD_ARS":   usdars_val,
            "_fuentes": {
                "urea":    urea_src,
                "brent":   brent_src,
                "usdars":  usdars_src,
            },
        }


# ─── Logger de precios ────────────────────────────────────────────────────────

class PriceLogger:
    """Mantiene un historial de precios con fecha en datos/faro_prices_log.json."""

    def __init__(self, log_path: Path = LOG_FILE):
        self.path = log_path

    def _cargar(self) -> list:
        if not self.path.exists():
            return []
        try:
            with open(self.path, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def registrar(self, precios: dict) -> None:
        """Agrega una entrada al historial con timestamp."""
        DATOS_DIR.mkdir(exist_ok=True)
        log = self._cargar()

        entrada = {
            "fecha":                datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00"),
            "PRECIO_UREA_USD_TN":   precios.get("PRECIO_UREA_USD_TN"),
            "BRENT_USD_BBL":        precios.get("BRENT_USD_BBL"),
            "PRECIO_DIESEL_USD_L":  precios.get("PRECIO_DIESEL_USD_L"),
            "COSTO_COMBUSTIBLE_HA": precios.get("COSTO_COMBUSTIBLE_HA"),
            "TIPO_CAMBIO_USD_ARS":  precios.get("TIPO_CAMBIO_USD_ARS"),
            "fuentes":              precios.get("_fuentes", {}),
        }
        log.append(entrada)

        # Mantener solo los últimos 104 registros (~2 años de datos semanales)
        if len(log) > 104:
            log = log[-104:]

        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(log, f, indent=2, ensure_ascii=False)

    def ultimo_registro(self) -> Optional[dict]:
        log = self._cargar()
        return log[-1] if log else None

    def mostrar_historial(self, n: int = 10) -> None:
        log = self._cargar()
        if not log:
            print("  (sin registros en el historial)")
            return
        recientes = log[-n:]
        print(f"\n  {'Fecha':<22} {'Urea USD/tn':>12} {'Brent $/bbl':>12} "
              f"{'Diesel $/L':>11} {'Comb/ha':>9} {'USD/ARS':>8}")
        print("  " + "-" * 80)
        for e in recientes:
            print(f"  {e['fecha'][:19]:<22} "
                  f"{str(e.get('PRECIO_UREA_USD_TN','—')):>12} "
                  f"{str(e.get('BRENT_USD_BBL','—')):>12} "
                  f"{str(e.get('PRECIO_DIESEL_USD_L','—')):>11} "
                  f"{str(e.get('COSTO_COMBUSTIBLE_HA','—')):>9} "
                  f"{str(e.get('TIPO_CAMBIO_USD_ARS','—')):>8}")


def _precio_desde_log(campo: str) -> Optional[float]:
    """Lee el último valor registrado de un campo del historial."""
    ultimo = PriceLogger().ultimo_registro()
    if ultimo and campo in ultimo and ultimo[campo] is not None:
        return float(ultimo[campo])
    return None


def _log_warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


# ─── Actualizador de faro_finance ─────────────────────────────────────────────

class FinanceUpdater:
    """Actualiza datos/faro_prices.json y regenera los Business Cases."""

    def guardar_precios(self, precios: dict) -> None:
        """
        Escribe datos/faro_prices.json con los precios actualizados.
        faro_finance.py carga este archivo automáticamente al importarse.
        """
        DATOS_DIR.mkdir(exist_ok=True)

        # Preservar campos sin underscore (las constantes exportables)
        exportable = {k: v for k, v in precios.items() if not k.startswith('_')}
        exportable['_actualizado'] = datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00")
        exportable['_fuentes']     = precios.get('_fuentes', {})

        with open(PRICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(exportable, f, indent=2, ensure_ascii=False)
        print(f"  Precios guardados → {PRICES_FILE}")

    def regenerar_business_cases(self) -> dict[str, dict]:
        """
        Regenera el Business Case para todas las áreas que tienen un Score válido.
        Recarga faro_finance después de actualizar faro_prices.json para usar
        los precios nuevos.

        Retorna dict {area_name: informe_dict}.
        """
        # Recargar faro_finance con los precios nuevos
        import importlib
        import faro_finance
        importlib.reload(faro_finance)
        from faro_finance import FaroFinance
        from faro_areas import load_area

        # Leer scores actuales de data.json
        scores: dict[str, float] = {}
        if DATA_JSON.exists():
            with open(DATA_JSON, encoding='utf-8') as f:
                data_actual = json.load(f)
            for area_name, insight in data_actual.get('insights', {}).items():
                s = insight.get('score_faro')
                if s is not None:
                    scores[area_name] = float(s)

        finance = FaroFinance()
        casos: dict[str, dict] = {}

        print("  Regenerando Business Cases...")
        for area_name, ha in AREAS_BUSINESS_CASE.items():
            score = scores.get(area_name)
            if score is None:
                print(f"    {area_name:<15} — sin score, omitido")
                continue
            try:
                area_meta = load_area(area_name)
                inf = finance.generar(area_meta=area_meta, score=score, hectareas=ha)
                casos[area_name] = {
                    "score_faro":           score,
                    "hectareas":            ha,
                    "ahorro_urea_ha":       inf.ahorro_urea_ha,
                    "ahorro_combustible_ha": inf.ahorro_combustible_ha,
                    "ahorro_multas_ha":     inf.ahorro_multas_ha,
                    "ahorro_total_ha":      inf.ahorro_total_ha,
                    "ahorro_total_usd":     inf.ahorro_total_usd,
                    "roi_x":                inf.roi_x,
                    "sha256_informe":       inf.sha256_informe,
                    "generado_en":          inf.fecha,
                }
                print(f"    {area_name:<15} score={score:.0f}  "
                      f"ahorro={inf.ahorro_total_ha:.2f} USD/ha  "
                      f"ROI={inf.roi_x}x")
            except Exception as e:
                print(f"    {area_name:<15} ERROR: {e}")

        return casos

    def actualizar_data_json(self, precios: dict, casos: dict) -> None:
        """
        Agrega/actualiza la sección 'finance' en data.json con los precios
        actuales y los Business Cases regenerados.
        """
        data: dict = {}
        if DATA_JSON.exists():
            with open(DATA_JSON, encoding='utf-8') as f:
                data = json.load(f)

        data.setdefault('finance', {})
        data['finance']['ultimo_update'] = datetime.now(ART).strftime("%Y-%m-%dT%H:%M:%S-03:00")
        data['finance']['precios'] = {
            "urea_usd_tn":       precios.get('PRECIO_UREA_USD_TN'),
            "brent_usd_bbl":     precios.get('BRENT_USD_BBL'),
            "diesel_usd_l":      precios.get('PRECIO_DIESEL_USD_L'),
            "combustible_ha_usd": precios.get('COSTO_COMBUSTIBLE_HA'),
            "usdars_oficial":    precios.get('TIPO_CAMBIO_USD_ARS'),
            "fuentes":           precios.get('_fuentes', {}),
        }
        data['finance']['business_cases'] = casos

        with open(DATA_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  data.json actualizado — sección 'finance'")


# ─── Notificador Paperclip ────────────────────────────────────────────────────

class PaperclipNotifier:
    """
    Envía una notificación de actualización de precios via Paperclip.
    Si ANTHROPIC_API_KEY está configurada, usa Claude para un resumen ejecutivo.
    Si no, imprime directamente al terminal con formato premium.
    """

    def notificar(self, precios: dict, casos: dict, duracion_s: float) -> str:
        api_key = _cargar_env_key()

        resumen_datos = {
            "evento":          "actualizacion_precios_semanal",
            "fecha":           datetime.now(ART).strftime("%Y-%m-%d %H:%M ART"),
            "duracion_s":      round(duracion_s, 1),
            "precios_nuevos":  {
                "urea_usd_tn":    precios.get('PRECIO_UREA_USD_TN'),
                "brent_usd_bbl":  precios.get('BRENT_USD_BBL'),
                "diesel_usd_l":   precios.get('PRECIO_DIESEL_USD_L'),
                "comb_ha_usd":    precios.get('COSTO_COMBUSTIBLE_HA'),
                "usdars":         precios.get('TIPO_CAMBIO_USD_ARS'),
            },
            "fuentes":   precios.get('_fuentes', {}),
            "casos":     {k: {"roi_x": v["roi_x"], "ahorro_ha": v["ahorro_total_ha"]}
                          for k, v in casos.items()},
        }

        if api_key:
            return self._notificar_llm(api_key, resumen_datos)
        else:
            return self._notificar_terminal(resumen_datos)

    def _notificar_llm(self, api_key: str, datos: dict) -> str:
        try:
            import anthropic
            cliente = anthropic.Anthropic(api_key=api_key)
            prompt = f"""Sos Paperclip, el agente monitor de FARO PROTOCOL.
Acabás de recibir la actualización semanal de precios de mercado.
Generá un resumen ejecutivo de máximo 10 líneas. Sin markdown. Sin emojis.
Solo datos, cambios relevantes y si algún precio impacta el Business Case significativamente.
Formato: terminal premium, datos primero.

{json.dumps(datos, indent=2, ensure_ascii=False)}"""

            msg = cliente.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            texto = msg.content[0].text
            print("\n" + "-" * 60)
            print(texto)
            print("-" * 60)
            return texto
        except Exception as e:
            _log_warn(f"Paperclip LLM falló: {e}")
            return self._notificar_terminal(datos)

    def _notificar_terminal(self, datos: dict) -> str:
        p = datos['precios_nuevos']
        lineas = [
            "",
            "=" * 60,
            "  [Paperclip] Precios de mercado actualizados",
            f"  {datos['fecha']}  ({datos['duracion_s']}s)",
            "-" * 60,
            f"  Urea          : USD {p['urea_usd_tn']}/tn",
            f"  Brent crude   : USD {p['brent_usd_bbl']}/bbl",
            f"  Diesel proxy  : USD {p['diesel_usd_l']}/L",
            f"  Combustible/ha: USD {p['comb_ha_usd']}/ha",
            f"  USD/ARS ofic. : {p['usdars']}",
            "-" * 60,
        ]
        for area, c in datos['casos'].items():
            lineas.append(f"  {area:<15} ROI={c['roi_x']}x  ahorro={c['ahorro_ha']} USD/ha")
        lineas += [
            "-" * 60,
            "  faro_prices.json actualizado. Próximo ciclo: lunes 08:00 ART.",
            "=" * 60,
            "",
        ]
        texto = "\n".join(lineas)
        print(texto)
        return texto


def _cargar_env_key() -> str:
    """Carga ANTHROPIC_API_KEY del entorno o del .env del proyecto."""
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if key:
        return key
    env_file = PROJECT_ROOT / '.env'
    if env_file.exists():
        for linea in env_file.read_text(encoding='utf-8').splitlines():
            linea = linea.strip()
            if linea.startswith('ANTHROPIC_API_KEY='):
                return linea.split('=', 1)[1].strip()
    return ''


# ─── Ciclo de actualización principal ────────────────────────────────────────

def run_update() -> None:
    """
    Ejecuta el ciclo completo de actualización de precios:
    fetch → guardar → regenerar BC → actualizar data.json → log → notificar.
    """
    inicio = datetime.now(ART)
    print("\n" + "=" * 60)
    print("  FARO PRICE UPDATER — Ciclo semanal")
    print(f"  {inicio.strftime('%Y-%m-%d %H:%M:%S ART')}")
    print("=" * 60)

    fetcher  = PriceFetcher()
    logger   = PriceLogger()
    updater  = FinanceUpdater()
    notifier = PaperclipNotifier()

    # 1. Obtener precios
    print("\n[1/5] Consultando mercados...")
    precios = fetcher.obtener_todos()

    # 2. Guardar faro_prices.json
    print("\n[2/5] Guardando precios...")
    updater.guardar_precios(precios)

    # 3. Regenerar Business Cases
    print("\n[3/5] Regenerando Business Cases...")
    casos = updater.regenerar_business_cases()

    # 4. Actualizar data.json
    print("\n[4/5] Actualizando data.json...")
    updater.actualizar_data_json(precios, casos)

    # 5. Guardar log histórico
    print("\n[5/5] Registrando historial...")
    logger.registrar(precios)
    ultimo = logger.ultimo_registro()
    log = logger._cargar()
    print(f"  Log: {LOG_FILE}  ({len(log)} entradas)")

    # Notificar
    duracion = (datetime.now(ART) - inicio).total_seconds()
    notifier.notificar(precios, casos, duracion)


# ─── Scheduler (APScheduler) ──────────────────────────────────────────────────

def iniciar_scheduler() -> None:
    """
    Inicia el scheduler en modo daemon.
    Corre run_update() todos los lunes a las 08:00 hora Argentina (ART = UTC-3).
    El proceso queda bloqueado ejecutando el scheduler hasta Ctrl+C.
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = BlockingScheduler(timezone="America/Argentina/Buenos_Aires")

    scheduler.add_job(
        func=run_update,
        trigger=CronTrigger(
            day_of_week='mon',   # lunes
            hour=8,
            minute=0,
            timezone="America/Argentina/Buenos_Aires",
        ),
        id='faro_price_update_weekly',
        name='Actualización semanal de precios',
        misfire_grace_time=3600,   # si el sistema estuvo apagado, correr hasta 1h después
        replace_existing=True,
    )

    # Calcular próxima ejecución directamente desde el trigger (antes de start)
    trigger_preview = CronTrigger(
        day_of_week='mon', hour=8, minute=0,
        timezone="America/Argentina/Buenos_Aires"
    )
    proxima = trigger_preview.get_next_fire_time(None, datetime.now(ART))

    print("=" * 60)
    print("  FARO PRICE UPDATER — Scheduler activo")
    print(f"  Frecuencia  : todos los lunes 08:00 ART")
    print(f"  Próxima run : {proxima.strftime('%Y-%m-%d %H:%M:%S %Z') if proxima else '—'}")
    print(f"  Detener     : Ctrl+C")
    print("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n  Scheduler detenido.")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description='FARO PRICE UPDATER — Actualización semanal de precios',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python faro_price_updater.py          # ejecutar ciclo completo ahora\n"
            "  python faro_price_updater.py --now    # igual\n"
            "  python faro_price_updater.py --daemon # scheduler lunes 08:00 ART\n"
            "  python faro_price_updater.py --log    # ver historial de precios\n"
            "  python faro_price_updater.py --log 20 # ver últimas 20 entradas\n"
        )
    )
    parser.add_argument('--now',    action='store_true',
                        help='Ejecutar ciclo completo inmediatamente (default)')
    parser.add_argument('--daemon', action='store_true',
                        help='Iniciar scheduler — lunes 08:00 ART (proceso bloqueante)')
    parser.add_argument('--log',    nargs='?', const=10, type=int, metavar='N',
                        help='Mostrar últimas N entradas del historial (default: 10)')
    args = parser.parse_args()

    if args.log is not None:
        print("\n  FARO PRICES — Historial de actualizaciones")
        PriceLogger().mostrar_historial(n=args.log)
        return

    if args.daemon:
        iniciar_scheduler()
        return

    # Default (--now o sin flags): ejecutar ahora
    run_update()


if __name__ == '__main__':
    main()
