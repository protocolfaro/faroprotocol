import sys, os, types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "sports", "clients", "velez"))
sys.path.insert(0, os.path.join(_ROOT, "core"))
sys.path.insert(0, _ROOT)

for _m in [
    "rasterio", "rasterio.crs", "rasterio.transform", "rasterio.warp", "rasterio.windows",
    "planetary_computer", "pystac_client",
]:
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)
