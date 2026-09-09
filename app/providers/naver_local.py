"""네이버 검색 API - 지역(로컬) 검색으로 회사 주변 식당을 모은다.

공식 API 라 안정적이지만 제약이 있다.
  * 한 번의 질의에 최대 5건 (display<=5, start=1) 만 준다.
  * 좌표 반경 검색이 없다. 그래서 "역삼동 돈가스" 처럼 지역명 + 음식 키워드로
    여러 번 나눠 질의하고, 받아온 좌표를 회사 좌표와 비교해 반경 안만 남긴다.
문서: https://developers.naver.com/docs/serviceapi/search/local/
"""
from __future__ import annotations

import time
import urllib.parse
from typing import Iterator

from ..categories import is_restaurant
from ..geo import haversine_m, parse_naver_coords
from .base import Place, ProviderError, http_json, strip_tags

ENDPOINT = "https://openapi.naver.com/v1/search/local.json"

# 지역명과 조합해서 던질 음식 키워드. 넓게 훑으려고 일부러 겹치게 뒀다.
FOOD_KEYWORDS: tuple[str, ...] = (
    "맛집", "한식", "중식", "일식", "양식", "분식", "아시아음식",
    "백반", "가정식", "국밥", "해장국", "설렁탕", "곰탕", "삼계탕", "감자탕",
    "김치찌개", "된장찌개", "부대찌개", "찜닭", "닭갈비", "제육볶음",
    "고깃집", "삼겹살", "곱창", "족발", "보쌈", "순대",
    "냉면", "칼국수", "국수", "막국수", "만두",
    "돈가스", "초밥", "회", "라멘", "우동", "소바", "덮밥", "카레",
    "짜장면", "짬뽕", "마라탕", "양꼬치", "탕수육",
    "파스타", "피자", "스테이크", "샐러드", "샌드위치", "햄버거", "브런치",
    "쌀국수", "베트남음식", "태국음식", "인도음식",
    "김밥", "떡볶이", "도시락", "죽", "뷔페", "치킨",
)


class NaverLocalProvider:
    def __init__(self, client_id: str, client_secret: str, request_delay: float = 0.12) -> None:
        if not client_id or not client_secret:
            raise ProviderError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없습니다. "
                "https://developers.naver.com/apps 에서 '검색' API 를 신청하세요."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.request_delay = request_delay

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }

    def search(self, query: str, display: int = 5) -> list[dict]:
        params = urllib.parse.urlencode(
            {"query": query, "display": max(1, min(5, display)), "start": 1, "sort": "random"}
        )
        payload = http_json(f"{ENDPOINT}?{params}", headers=self._headers)
        return payload.get("items", []) or []

    def _to_place(self, item: dict, office: tuple[float, float]) -> Place | None:
        name = strip_tags(item.get("title"))
        if not name:
            return None
        category = item.get("category", "") or ""
        if not is_restaurant(category, name):
            return None
        coords = parse_naver_coords(item.get("mapx"), item.get("mapy"))
        if coords is None:
            return None
        lat, lng = coords
        road = (item.get("roadAddress") or "").strip()
        addr = (item.get("address") or "").strip()
        # 같은 가게가 질의마다 중복으로 오므로 상호 + 도로명주소로 식별한다.
        pid = f"naver:{name}|{road or addr}"
        return Place(
            id=pid,
            name=name,
            raw_category=category,
            road_address=road,
            address=addr,
            lat=lat,
            lng=lng,
            phone=(item.get("telephone") or "").strip(),
            link=(item.get("link") or "").strip(),
            distance_m=round(haversine_m(office[0], office[1], lat, lng), 1),
            source="naver_local",
        )

    def collect_nearby(
        self,
        office_lat: float,
        office_lng: float,
        area_keyword: str,
        radius_m: int,
        keywords: tuple[str, ...] = FOOD_KEYWORDS,
        on_progress=None,
    ) -> Iterator[Place]:
        """지역 키워드 + 음식 키워드를 돌면서 반경 안의 식당을 흘려보낸다."""
        seen: set[str] = set()
        office = (office_lat, office_lng)
        for idx, keyword in enumerate(keywords, start=1):
            query = f"{area_keyword} {keyword}".strip()
            try:
                items = self.search(query)
            except ProviderError as exc:
                if on_progress:
                    on_progress(idx, len(keywords), query, f"실패: {exc}")
                continue
            found = 0
            for item in items:
                place = self._to_place(item, office)
                if place is None or place.id in seen:
                    continue
                if place.distance_m is not None and place.distance_m > radius_m:
                    continue
                seen.add(place.id)
                found += 1
                yield place
            if on_progress:
                on_progress(idx, len(keywords), query, f"{found}곳 추가")
            time.sleep(self.request_delay)
