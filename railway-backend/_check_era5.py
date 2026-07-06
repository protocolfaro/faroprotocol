"""Quick sanity check for faro_era5_land_sectorial updates."""
import sys, os, importlib, tempfile
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'sports', 'clients', 'velez'))
os.environ['SUPABASE_URL'] = 'https://fake.supabase.co'
os.environ['SUPABASE_KEY'] = 'fake-key'
os.environ.pop('CDS_API_KEY', None)

import faro_era5_land_sectorial as era5

results = {}

# T1: missing CDS_API_KEY
errs = era5.preflight_check()
results['T1_missing_key'] = any('CDS_API_KEY' in e for e in errs)

# T2: bad format (short key, no colon)
os.environ['CDS_API_KEY'] = 'nodots'
importlib.reload(era5)
errs2 = era5.preflight_check()
results['T2_bad_format'] = any('formato' in e.lower() or 'UID' in e for e in errs2)

# T2b: new UUID format (ECMWF 2024+) must pass validation
os.environ['CDS_API_KEY'] = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
importlib.reload(era5)
errs2b = era5.preflight_check()
results['T2b_uuid_format_ok'] = not any('formato' in e.lower() for e in errs2b)

# T3: sectors loaded (real sector_definitions.json has 5)
os.environ['CDS_API_KEY'] = '99999:abc'
importlib.reload(era5)
results['T3_sectors_loaded'] = len(era5.SECTORS) == 5

# T4: process_all_sectors returns failed when no key
os.environ.pop('CDS_API_KEY', None)
importlib.reload(era5)
r = era5.process_all_sectors()
results['T4_fail_no_key'] = r['status'] == 'failed' and 'preflight_errors' in r

# T5: download returns None without _HAS_CDS
os.environ['CDS_API_KEY'] = '99999:abc'
importlib.reload(era5)
bbox = {'latitud_min': -34.65, 'latitud_max': -34.63,
        'longitud_min': -58.53, 'longitud_max': -58.51}
with patch.object(era5, '_HAS_CDS', False):
    with tempfile.TemporaryDirectory() as tmp:
        r5 = era5._download_era5_land('x', bbox, datetime(2026, 7, 1, tzinfo=timezone.utc), tmp)
results['T5_no_cdsapi_none'] = r5 is None

# T6: _validate_output passthrough (does not alter values)
sample = {'sector_id': 's', 'et0_mm_dia': 2.0, 'rh_pct': 75.0, 'sm_0_7cm_m3m3': 0.30}
out = era5._validate_output(sample, 's')
results['T6_validate_passthrough'] = out == sample

# T7: preflight detects missing SECTORS
with patch.object(era5, 'SECTORS', {}):
    errs7 = era5.preflight_check()
results['T7_empty_sectors'] = any('sector' in e.lower() for e in errs7)

# T8: only_sector filter works (unknown sector returns failed)
r8 = era5.process_all_sectors(only_sector='nonexistent_sector_xyz')
results['T8_unknown_sector_fails'] = r8['status'] == 'failed'

# T9: _expand_bbox ensures minimum span for ERA5-Land 0.1° grid
small_bbox = {'latitud_min': -34.6395, 'latitud_max': -34.6360,
              'longitud_min': -58.5230, 'longitud_max': -58.5185}
exp = era5._expand_bbox(small_bbox, min_span=0.25)
lat_span = round(exp['latitud_max'] - exp['latitud_min'], 4)
lon_span = round(exp['longitud_max'] - exp['longitud_min'], 4)
results['T9_expand_bbox_min025'] = lat_span >= 0.24 and lon_span >= 0.24

print()
for name, ok in results.items():
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {name}')
passed = sum(results.values())
print(f'\n{passed}/{len(results)} passed')
sys.exit(0 if passed == len(results) else 1)
