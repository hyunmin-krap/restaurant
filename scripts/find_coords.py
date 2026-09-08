#!/usr/bin/env python3
"""회사 주소로 좌표를 찾는다.

    python3 scripts/find_coords.py "서울 강남구 테헤란로 152"

네이버 지역검색 API 를 쓰므로 .env 에 NAVER_CLIENT_ID/SECRET 이 있어야 한다.
찾은 값을 .env 의 OFFICE_LAT / OFFICE_LNG 에 넣으면 된다.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import CONFIG  # noqa: E402
from app.geo import parse_naver_coords  # noqa: E402
from app.providers import NaverLocalProvider, ProviderError  # noqa: E402
from app.providers.base import strip_tags  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    query = " ".join(argv[1:])
    try:
        provider = NaverLocalProvider(CONFIG.naver_client_id, CONFIG.naver_client_secret)
        items = provider.search(query)
    except ProviderError as exc:
        print(f"실패: {exc}")
        return 1
    if not items:
        print("검색 결과가 없습니다. 건물명이나 도로명 주소로 다시 시도해 보세요.")
        return 1
    for item in items:
        coords = parse_naver_coords(item.get("mapx"), item.get("mapy"))
        if not coords:
            continue
        lat, lng = coords
        print(f"\n{strip_tags(item.get('title'))}  [{item.get('category','')}]")
        print(f"  {item.get('roadAddress') or item.get('address')}")
        print(f"  OFFICE_LAT={lat}")
        print(f"  OFFICE_LNG={lng}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
