import json

with open('C:/Users/Usuario/Desktop/Faro-index/railway-backend/dale-play/models/railway_run_result.json', encoding='utf-8') as f:
    d = json.load(f)

if 'error' in d:
    print('ERROR:', d['error'])
    raise SystemExit(1)

print('='*70)
print(f"PIPELINE AIRBAG 2026-05-31 — RAILWAY")
print(f"Timestamp: {d.get('timestamp', '?')}")
print(f"Venue:     {d.get('venue', '?')}")
print(f"Mode:      {d.get('mode', '?')}")
print('='*70)

# 1. SATELLITE
sat = d.get('satellite') or {}
ndvi = sat.get('ndvi')
fallback = sat.get('fallback', False) or sat.get('fallback_usado', False)
fuente = sat.get('fuente') or sat.get('source') or 'Sentinel-2 / OpenEO'
label = sat.get('label') or sat.get('estado') or '-'
print()
print('1. SATELLITE / NDVI')
print(f"   Fuente:  {fuente}")
print(f"   NDVI:    {ndvi}")
print(f"   Label:   {label}")
print(f"   Status:  {'[ESTIMADO]' if fallback else '[DATO REAL]'}")

# 2. WEATHER
wx = d.get('weather') or {}
fuente_wx = wx.get('fuente') or wx.get('source') or 'Open-Meteo API'
t_max = wx.get('temp_max_c') or wx.get('temperatura_max')
t_min = wx.get('temp_min_c') or wx.get('temperatura_min')
lluvia = wx.get('lluvia_mm') or wx.get('precipitacion_mm') or wx.get('lluvia_48h_mm')
viento = wx.get('viento_max_kmh') or wx.get('viento_kmh')
wdesc = wx.get('descripcion') or wx.get('condicion') or wx.get('estado') or '-'
fb_wx = wx.get('fallback', False) or wx.get('fallback_usado', False)
print()
print('2. WEATHER')
print(f"   Fuente:  {fuente_wx}")
print(f"   Temp:    {t_max}C max / {t_min}C min")
print(f"   Lluvia:  {lluvia} mm")
print(f"   Viento:  {viento} km/h")
print(f"   Estado:  {wdesc}")
print(f"   Status:  {'[ESTIMADO]' if fb_wx else '[DATO REAL]'}")

# 3. SOIL
soil = d.get('soil') or {}
soil_real = d.get('soil_real') or {}
fuente_soil = soil.get('fuente') or soil_real.get('fuente') or soil.get('source') or '-'
kpa_ef = soil.get('capacidad_efectiva_kpa') or soil_real.get('capacidad_portante_kpa')
textura = soil_real.get('textura_usda') or soil.get('textura') or '-'
riesgo = soil.get('riesgo_global') or soil_real.get('riesgo_hidrico') or '-'
fb_soil = soil.get('fallback', False) or soil_real.get('fallback_usado', False) or 'estimado' in str(fuente_soil).lower()
zonas_s = soil.get('zonas') or []
print()
print('3. SOIL (carga portante)')
print(f"   Fuente:  {fuente_soil}")
print(f"   kPa ef:  {kpa_ef}")
print(f"   Textura: {textura}")
print(f"   Riesgo:  {riesgo}")
print(f"   Status:  {'[ESTIMADO]' if fb_soil else '[DATO REAL]'}")
for z in zonas_s[:4]:
    presion = z.get('presion_kpa', '-')
    clasif = z.get('clasificacion', '-')
    zid = z.get('id', '?')
    print(f"   Zona {zid}: {presion} kPa -> {clasif}")

# 4. ACOUSTIC
ac = d.get('acoustic') or {}
fuente_ac = ac.get('fuente') or ac.get('source') or 'Claude API + rider'
lw = ac.get('lw_db') or ac.get('lw_estimado_db')
sistema = ac.get('sistema') or ac.get('sistema_pa') or '-'
fb_ac = ac.get('fallback', False) or ac.get('estimado', False)
print()
print('4. ACOUSTIC (SPL sectores)')
print(f"   Fuente:  {fuente_ac}")
print(f"   Lw:      {lw} dB")
print(f"   Sistema: {sistema}")
print(f"   Status:  {'[ESTIMADO]' if fb_ac else '[DATO REAL]'}")
for s in (ac.get('sectores') or []):
    sid = str(s.get('id', '?'))[:20]
    spl = s.get('spl_db', '-')
    cl = s.get('clasificacion') or s.get('label', '-')
    print(f"   {sid:<20s}: {spl:>6} dB  {cl}")

# 5. DRAINAGE
dr = d.get('drainage') or {}
metodo = dr.get('metodo') or dr.get('fuente') or '-'
hid = dr.get('hidraulica') or {}
ksat = hid.get('ksat') or {}
resdr = dr.get('resumen') or {}
print()
print('5. DRAINAGE (drenaje campo)')
print(f"   Fuente:  {metodo}")
print(f"   Ksat:    {ksat.get('ksat_mm_h', '-')} mm/h ({ksat.get('textura_usda', '-')})")
print(f"   Zonas:   {resdr.get('zonas_exclusion',0)} excl / {resdr.get('zonas_precaucion',0)} prec / {resdr.get('zonas_seguras',0)} ok")
print(f"   Cap min: {resdr.get('capacidad_min_kpa', '-')} kPa")
print(f"   Riesgo:  {resdr.get('riesgo_global', '-')}")
print(f"   Status:  [DATO REAL] (SoilGrids REST + Sentinel-2/Landsat/SAR)")

# 6. EGMS
egms = d.get('egms') or {}
fuente_egms = egms.get('fuente') or egms.get('source') or '-'
res_egms = egms.get('resumen') or {}
vel = egms.get('velocidad_media_mm_yr') or res_egms.get('vel_media_mm_yr')
crit = res_egms.get('sectores_criticos', 0)
aten = res_egms.get('sectores_atencion', 0)
fb_egms = bool(egms.get('error')) or egms.get('fallback_usado', False)
print()
print('6. EGMS (deformacion estructural historica)')
print(f"   Fuente:  {fuente_egms}")
print(f"   Sectores criticos: {crit}")
print(f"   Sectores atencion: {aten}")
print(f"   Vel. media:        {vel} mm/yr")
print(f"   Status:  {'[ESTIMADO]' if fb_egms else '[DATO REAL]'}")
sectores_egms = egms.get('sectores') or {}
if isinstance(sectores_egms, dict):
    for k, v in sectores_egms.items():
        vel_s = v.get('vel_mm_yr', '-') if isinstance(v, dict) else v
        nivel_s = v.get('nivel', '-') if isinstance(v, dict) else '-'
        print(f"   {k}: {vel_s} mm/yr  [{nivel_s}]")

# 7. SPL COMPLIANCE
spl = d.get('spl_compliance') or {}
print()
print('7. SPL COMPLIANCE (normativa GCBA)')
print(f"   Periodo:       {spl.get('periodo', '-')}")
print(f"   SPL max int:   {spl.get('spl_max_interior_db', '-')} dB")
print(f"   SPL predial:   {spl.get('spl_predial_db', '-')} dB (limite {spl.get('limite_exterior_dba', '-')} dB)")
print(f"   Margen:        {spl.get('margen_exterior_db', '-')} dB")
print(f"   Estado global: {spl.get('estado_global', '-')}")
print(f"   Status:  [ESTIMADO] (geometria libre campo + atenuacion estructura)")
for al in (spl.get('alertas') or []):
    print(f"   ALERTA: {al}")

# 8. STRUCTURAL
st = d.get('structural') or {}
fuente_st = st.get('fuente') or st.get('source') or 'HyP3 (NASA ASF) + EGMS delta'
trib = st.get('tribunas') or st.get('sectores') or []
fb_st = st.get('fallback', False) or st.get('fallback_usado', False)
print()
print('8. STRUCTURAL / InSAR (deformacion post-show)')
print(f"   Fuente:  {fuente_st}")
for t in trib:
    nm = t.get('nombre') or t.get('id') or '?'
    los = t.get('los_mm') or t.get('deformacion_mm') or '-'
    niv = t.get('clasificacion') or t.get('nivel') or {}
    if isinstance(niv, dict):
        niv = niv.get('nivel', '-')
    delta = t.get('delta_mm') or t.get('egms_delta_mm')
    delta_str = f" | EGMS delta: {delta} mm" if delta is not None else ''
    print(f"   {str(nm):<20s}: {str(los):>7} mm  [{niv}]{delta_str}")
print(f"   Status:  {'[ESTIMADO]' if fb_st else '[DATO REAL]'}")

# 9. RIDER COMPLIANCE
rc = d.get('rider_compliance') or {}
fuente_rc = rc.get('fuente') or 'JSON rider show'
estado_rc = rc.get('estado_global') or rc.get('status') or rc.get('estado') or '-'
issues = rc.get('issues') or rc.get('alertas') or rc.get('warnings') or []
print()
print('9. RIDER COMPLIANCE')
print(f"   Fuente:  {fuente_rc}")
print(f"   Estado:  {estado_rc}")
print(f"   Status:  [DATO REAL] (rider del show)")
for i in (issues or [])[:5]:
    print(f"   ISSUE: {i}")

# 10. FII
fii = d.get('fii') or {}
comp = fii.get('componentes') or {}
print()
print('10. FII (Indice Faro de Integridad)')
print(f"   FII GLOBAL:  {fii.get('fii', '-')} / 100")
print(f"   Semaforo:    {str(fii.get('semaforo', '-')).upper()}")
print(f"   Sello:       {fii.get('sello', '-')}")
for k, v in comp.items():
    score = v.get('score', '-') if isinstance(v, dict) else '-'
    peso = v.get('peso', '-') if isinstance(v, dict) else '-'
    lbl = v.get('label', '-') if isinstance(v, dict) else '-'
    print(f"   {k.upper():<10s}: {score:>5} pts  (peso {peso}%)  {lbl}")
print(f"   Status:  [CALCULADO] (40% NDVI + 35% EGMS + 25% Layout)")

# PNG
print()
print('PNG / GITHUB')
print(f"   report_png_url: {d.get('report_png_url') or '-'}")
gh = d.get('github') or {}
gh_status = gh.get('status') or gh.get('ok') or str(gh)[:80]
print(f"   github push:    {gh_status}")
print()
print('='*70)
