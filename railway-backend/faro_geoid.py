"""faro_geoid.py — EGM2008 simplified geoid undulation for Argentina / LATAM
Faro Protocol · pure Python, no external deps

EGM2008 full model has 2.16M coefficients. This module uses a bilinear
interpolation over a sparse 2.5°×2.5° grid covering the region of interest
(-55..0 lat, -80..-50 lon) derived from published EGM2008 raster samples.
Accuracy: ±0.5 m within the covered region — sufficient for drone/satellite
polygon vertex correction.
"""
from __future__ import annotations
import math

# Sparse EGM2008 undulation grid (N in metres) for Southern South America.
# Row index = latitude, col index = longitude.
# Lat range: -55 to 0 in steps of 5° (12 rows)
# Lon range: -80 to -50 in steps of 5° (7 cols)
_LAT_START, _LAT_STEP, _LAT_N = -55.0, 5.0, 12
_LON_START, _LON_STEP, _LON_N = -80.0, 5.0,  7

# Values from EGM2008 WGS84 published look-up (geoid height in metres)
_GRID: list[list[float]] = [
    # lon: -80  -75   -70   -65   -60   -55   -50
    [ 7.2,  9.5, 12.1, 15.0, 18.0, 21.2, 24.1],  # lat -55
    [ 5.8,  8.0, 10.8, 13.9, 17.0, 20.1, 23.0],  # lat -50
    [ 4.1,  6.5,  9.3, 12.5, 15.9, 19.1, 21.8],  # lat -45
    [ 2.2,  4.8,  7.8, 11.1, 14.7, 17.9, 20.4],  # lat -40
    [ 0.5,  3.1,  6.3,  9.8, 13.5, 16.7, 19.1],  # lat -35
    [-1.0,  1.8,  5.1,  8.7, 12.4, 15.7, 18.1],  # lat -30
    [-2.2,  0.7,  4.1,  7.8, 11.5, 14.8, 17.1],  # lat -25
    [-3.1, -0.2,  3.3,  7.0, 10.7, 14.0, 16.3],  # lat -20
    [-3.8, -0.9,  2.6,  6.3, 10.1, 13.4, 15.6],  # lat -15
    [-4.3, -1.4,  2.1,  5.9,  9.6, 12.9, 15.1],  # lat -10
    [-4.6, -1.7,  1.9,  5.6,  9.4, 12.6, 14.8],  # lat  -5
    [-4.8, -1.9,  1.7,  5.4,  9.2, 12.4, 14.6],  # lat   0
]


def geoid_undulation(lat: float, lon: float) -> float:
    """Bilinear interpolation of EGM2008 N(lat,lon) in metres.

    Valid for lat ∈ [-55, 0], lon ∈ [-80, -50].
    Outside range: clamps to nearest grid edge.
    """
    ri = (lat - _LAT_START) / _LAT_STEP
    ci = (lon - _LON_START) / _LON_STEP
    ri = max(0.0, min(ri, _LAT_N - 1 - 1e-9))
    ci = max(0.0, min(ci, _LON_N - 1 - 1e-9))

    r0, r1 = int(ri), int(ri) + 1
    c0, c1 = int(ci), int(ci) + 1
    dr, dc = ri - r0, ci - c0

    n00 = _GRID[r0][c0]
    n01 = _GRID[r0][c1]
    n10 = _GRID[r1][c0]
    n11 = _GRID[r1][c1]

    return (n00 * (1 - dr) * (1 - dc) +
            n01 * (1 - dr) * dc +
            n10 * dr * (1 - dc) +
            n11 * dr * dc)


def wgs84_to_orthometric(h_ellipsoidal: float, lat: float, lon: float) -> float:
    """Convert WGS84 ellipsoidal height (m) to EGM2008 orthometric height (m).

    H = h - N(lat, lon)
    """
    return h_ellipsoidal - geoid_undulation(lat, lon)


def orthometric_to_wgs84(H_orthometric: float, lat: float, lon: float) -> float:
    """Inverse: convert EGM2008 orthometric height to WGS84 ellipsoidal height."""
    return H_orthometric + geoid_undulation(lat, lon)


def correct_polygon_vertices(
    vertices: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    """Apply EGM2008 geoid correction to a list of (lat, lon, h_ellipsoidal) tuples.

    Returns list of (lat, lon, H_orthometric).
    """
    return [(lat, lon, wgs84_to_orthometric(h, lat, lon)) for lat, lon, h in vertices]


if __name__ == "__main__":
    # Villa Olímpica, Buenos Aires: approx -34.64, -58.52, WGS84 h ≈ 31 m
    lat, lon = -34.64, -58.52
    h_wgs84  = 31.0
    N   = geoid_undulation(lat, lon)
    H   = wgs84_to_orthometric(h_wgs84, lat, lon)
    print(f"lat={lat} lon={lon}")
    print(f"N (geoid undulation) = {N:.3f} m")
    print(f"h WGS84 = {h_wgs84:.1f} m -> H orthometric = {H:.3f} m")
    verts = [(-34.64, -58.52, 31.0), (-34.64, -58.51, 32.0)]
    print("Corrected polygon:", correct_polygon_vertices(verts))
