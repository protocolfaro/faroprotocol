import json, sys, logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
sys.path.insert(0, '.')

show_cfg = json.loads(open('shows/airbag_2026-05-31.json', encoding='utf-8').read())
from dale_play_pipeline import run_show_audit
result = run_show_audit(show_cfg, mode='full')

print('\n=== RESULTADO PIPELINE ===')
print(f"Pipeline error: {result.get('_pipeline_error', 'NINGUNO')}")
print(f"Smoke errors:   {result.get('_smoke_errors', 'NINGUNO')}")
print(f"PNG: {result.get('report_png')}")
print('\n--- Confianza por módulo ---')
modules_keys = ['satellite','weather','soil','acoustic','drainage','egms',
                'spl_compliance','structural','rider_compliance']
for k in modules_keys:
    d = result.get(k) or {}
    err = d.get('error', '')
    est = d.get('estimado', False) or d.get('fallback_usado', False)
    tag = 'ROJO' if err else ('AMARILLO' if est else 'VERDE')
    print(f"  {k:25s} [{tag}] {err[:60] if err else ''}")

# Verificar run_log
import pathlib
rl = pathlib.Path('models/run_log_airbag_2026-05-31.json')
if rl.exists():
    data = json.loads(rl.read_text(encoding='utf-8'))
    print(f'\nRun log: {data["duracion_total_s"]}s · confianza={data["confianza_general"]}')
    print(f'  Ejecutados: {len(data["modulos_ejecutados"])} · Fallidos: {len(data["modulos_fallidos"])}')
