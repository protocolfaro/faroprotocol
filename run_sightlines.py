import sys, json, logging
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, 'railway-backend/dale-play')
show_config = json.load(open('railway-backend/dale-play/shows/airbag_2026-05-31.json', encoding='utf-8'))
from dale_play_stadium_3d import build_3d_model
from dale_play_sightlines import run_and_save
model  = build_3d_model(show_config['rider'])
result = run_and_save(model)
print(json.dumps(result['resumen'], indent=2))
print()
for t, v in result['tribunas'].items():
    vis  = v['visibilidad']
    flag = ' [OBSTRUIDA]' if v['obstruida_por_escenario'] else ''
    ad   = vis['adecuada_pct']
    bu   = vis['buena_pct']
    ex   = vis['excelente_pct']
    cp   = vis['c_prom_m']
    print(f"  {t:6s}: adecuada={ad:5.1f}%  buena={bu:5.1f}%  excelente={ex:5.1f}%  c_prom={cp:+.3f}m{flag}")
