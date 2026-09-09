#!/usr/bin/env python3
"""주변 식당을 수집해 DB 에 채운다 (서버 없이 CLI 로).

    python3 scripts/sync_places.py --area 역삼동
    python3 scripts/sync_places.py --area 역삼동 --radius 800
    python3 scripts/sync_places.py --taste --limit 80     # 맛있어요 비율 채우기
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import budget as budget_mod  # noqa: E402
from app import db as dbm  # noqa: E402
from app.config import CONFIG  # noqa: E402
from app.providers import (  # noqa: E402
    GooglePlacesProvider, NaverLocalProvider, NaverPlaceReviewProvider, ProviderError,
)
from app.sync import SyncState, enrich_places, sync_places  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="회사 주변 식당 수집")
    parser.add_argument("--area", help="지역 검색 키워드 (예: 역삼동, 판교역)")
    parser.add_argument("--radius", type=int, default=CONFIG.radius_m, help="반경(m)")
    parser.add_argument("--enrich", "--taste", dest="enrich", action="store_true",
                        help="'맛있어요' 비율과 영업시간(점심 영업 여부) 채우기")
    parser.add_argument("--limit", type=int, default=50, help="--enrich 일 때 처리할 식당 수")
    parser.add_argument("--refresh", action="store_true", help="--enrich 일 때 이미 있는 값도 갱신")
    args = parser.parse_args()

    conn = dbm.connect(CONFIG.db_path)
    dbm.init_db(conn)
    state = SyncState()

    if args.enrich:
        google = GooglePlacesProvider(CONFIG.google_maps_api_key) if CONFIG.has_google_key else None
        reviewer = NaverPlaceReviewProvider() if CONFIG.enable_place_review_scrape else None
        if google is None and reviewer is None:
            print("채울 소스가 없습니다.")
            print("  · 영업시간(권장): .env 에 GOOGLE_MAPS_API_KEY 를 넣으세요. 구글 공식 API 입니다.")
            print("  · '맛있어요' 비율: ENABLE_PLACE_REVIEW_SCRAPE=1 (비공식 경로, 막힐 수 있음)")
            return 1
        print("영업시간 소스:", "구글 Places (공식)" if google else "네이버 플레이스 (비공식)")
        print("맛있어요 비율:", "네이버 플레이스" if reviewer else "안 함")
        if google:
            b = budget_mod.status(conn, CONFIG.google_monthly_call_limit)
            print(f"구글 호출: 이번 달 {b['used']}/{b['limit']}건 사용 (남은 {b['remaining']}건)")
        filled = enrich_places(
            conn, reviewer, google, limit=args.limit,
            google_call_limit=CONFIG.google_monthly_call_limit,
            state=state, only_missing=not args.refresh,
        )
        for line in state.log:
            print(" ", line)
        print(f"\n{filled}곳을 갱신했습니다 (맛있어요 비율 · 점심 영업 여부).")
        if google:
            b = budget_mod.status(conn, CONFIG.google_monthly_call_limit)
            print(f"구글 호출 누적: {b['used']}/{b['limit']}건")
        return 0

    area = args.area or dbm.get_setting(conn, "area_keyword")
    if not area:
        print("--area 로 지역 키워드를 주세요. 예: --area 역삼동")
        return 2
    dbm.set_setting(conn, "area_keyword", area)

    try:
        provider = NaverLocalProvider(CONFIG.naver_client_id, CONFIG.naver_client_secret)
    except ProviderError as exc:
        print(f"실패: {exc}")
        return 1

    print(f"'{area}' 주변 {args.radius}m 이내 식당을 모읍니다…")
    added = sync_places(
        conn, provider, CONFIG.office_lat, CONFIG.office_lng, area, args.radius, state=state
    )
    for line in state.log:
        print(" ", line)
    total = conn.execute("SELECT COUNT(*) c FROM places WHERE is_active=1").fetchone()["c"]
    print(f"\n{added}건 수집(중복 제외), DB 총 {total}곳.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
