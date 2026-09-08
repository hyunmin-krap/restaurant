"""좌표 유틸 - 거리 계산과 네이버 좌표 파싱."""
from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_008.8


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """두 WGS84 좌표 사이의 대권 거리(미터)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def walking_minutes(distance_m: float, speed_m_per_min: float = 75.0) -> int:
    """도보 소요 시간(분). 성인 평균 4.5km/h 기준, 최소 1분."""
    return max(1, round(distance_m / speed_m_per_min))


# ── 네이버 좌표 파싱 ────────────────────────────────────────────────
# 네이버 지역검색 API 의 mapx/mapy 는 두 가지 형식이 섞여 있다.
#   * 현행: WGS84 경위도 * 10^7  (예: 1270276620 / 374979310)
#   * 구형: KATECH(TM128) 미터   (예: 310000 / 552000)
# 자릿수로 구분해서 둘 다 처리한다.

_BESSEL_A = 6_377_397.155
_BESSEL_F = 1 / 299.1528128
_WGS84_A = 6_378_137.0
_WGS84_F = 1 / 298.257223563

# 한국 지역 Bessel -> WGS84 datum shift
_DX, _DY, _DZ = -146.43, 507.89, 681.46

_TM128 = {
    "lat0": math.radians(38.0),
    "lon0": math.radians(128.0),
    "k0": 0.9999,
    "false_easting": 400_000.0,
    "false_northing": 600_000.0,
}


def _meridian_arc(a: float, e2: float, phi: float) -> float:
    return a * (
        (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
        + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
        - (35 * e2**3 / 3072) * math.sin(6 * phi)
    )


def _tm_inverse(x: float, y: float) -> tuple[float, float]:
    """TM128(Bessel) 평면좌표 -> Bessel 경위도(라디안)."""
    a, f = _BESSEL_A, _BESSEL_F
    e2 = 2 * f - f * f
    ep2 = e2 / (1 - e2)
    p = _TM128

    m = (y - p["false_northing"]) / p["k0"] + _meridian_arc(a, e2, p["lat0"])
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    mu = m / (a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
        + (151 * e1**3 / 96) * math.sin(6 * mu)
        + (1097 * e1**4 / 512) * math.sin(8 * mu)
    )

    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    sin2 = math.sin(phi1) ** 2
    n1 = a / math.sqrt(1 - e2 * sin2)
    r1 = a * (1 - e2) / (1 - e2 * sin2) ** 1.5
    d = (x - p["false_easting"]) / (n1 * p["k0"])

    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * ep2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * ep2 - 3 * c1**2) * d**6 / 720
    )
    lon = p["lon0"] + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * ep2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(phi1)
    return lat, lon


def _molodensky_bessel_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """Bessel 1841 경위도(라디안) -> WGS84 경위도(도)."""
    from_a, from_f = _BESSEL_A, _BESSEL_F
    from_b = from_a * (1 - from_f)
    da = _WGS84_A - from_a
    df = _WGS84_F - from_f
    e2 = 2 * from_f - from_f**2
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    rn = from_a / math.sqrt(1 - e2 * sin_lat**2)
    rm = from_a * (1 - e2) / (1 - e2 * sin_lat**2) ** 1.5

    dlat = (
        -_DX * sin_lat * cos_lon
        - _DY * sin_lat * sin_lon
        + _DZ * cos_lat
        + da * (rn * e2 * sin_lat * cos_lat) / from_a
        + df * (rm * (from_a / from_b) + rn * (from_b / from_a)) * sin_lat * cos_lat
    ) / rm
    dlon = (-_DX * sin_lon + _DY * cos_lon) / (rn * cos_lat)
    return math.degrees(lat + dlat), math.degrees(lon + dlon)


def tm128_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """KATECH(TM128) -> (위도, 경도)."""
    lat, lon = _tm_inverse(x, y)
    return _molodensky_bessel_to_wgs84(lat, lon)


def parse_naver_coords(mapx: str | float, mapy: str | float) -> tuple[float, float] | None:
    """네이버 mapx/mapy -> (위도, 경도). 형식을 판별하지 못하면 None."""
    try:
        x = float(str(mapx).strip())
        y = float(str(mapy).strip())
    except (TypeError, ValueError):
        return None
    if x == 0 and y == 0:
        return None

    # 현행 형식: WGS84 * 10^7 (경도 ~1.27e9, 위도 ~3.7e8)
    if x > 1_000_000_0:
        lat, lng = y / 1e7, x / 1e7
    else:
        lat, lng = tm128_to_wgs84(x, y)

    if not (32.0 <= lat <= 39.5 and 124.0 <= lng <= 132.5):
        return None
    return round(lat, 7), round(lng, 7)
