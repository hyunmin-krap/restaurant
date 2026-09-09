"""구글 Places API - 영업시간 전용 보조 소스.

네이버 지역검색 API 는 영업시간을 주지 않는다. 구글은 공식 API 로 준다.
그래서 식당 목록·분류·'맛있어요' 비율은 네이버에서 가져오고,
영업시간(= 점심에 문 여는지)만 구글에서 채운다.

비용: 종량제다. 다만 식당 한 곳당 한 번만 부르고 결과를 DB 에 저장하므로,
반경 안 식당 수(보통 수십~수백 곳) 만큼만 호출한다. 갱신할 때만 다시 부른다.
무료 한도는 구글 클라우드 콘솔에서 확인하세요.
문서: https://developers.google.com/maps/documentation/places/web-service/text-search
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..geo import haversine_m
from ..hours import from_google_periods
from .base import ProviderError, http_json

SEARCH_ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
DETAIL_ENDPOINT = "https://places.googleapis.com/v1/places/"

# 필요한 필드만 요청한다. 필드가 많을수록 과금 단가가 올라간다.
FIELD_MASK = ",".join((
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.regularOpeningHours.periods",
    "places.regularOpeningHours.weekdayDescriptions",
))

# 같은 가게로 인정할 최대 거리. 상호명이 흔해서 엉뚱한 지점이 잡히는 것을 막는다.
MATCH_RADIUS_M = 250.0


class GooglePlacesProvider:
    def __init__(self, api_key: str, request_delay: float = 0.15, timeout: float = 10.0) -> None:
        if not api_key:
            raise ProviderError(
                "GOOGLE_MAPS_API_KEY 가 없습니다. "
                "https://console.cloud.google.com 에서 Places API (New) 를 켜고 키를 발급하세요."
            )
        self.api_key = api_key
        self.request_delay = request_delay
        self.timeout = timeout

    def search(self, query: str, lat: float, lng: float, radius_m: int = 400) -> list[dict]:
        body = json.dumps({
            "textQuery": query,
            "languageCode": "ko",
            "regionCode": "KR",
            "maxResultCount": 5,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(max(50, radius_m)),
                }
            },
        }).encode("utf-8")
        payload = http_json(
            SEARCH_ENDPOINT,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": FIELD_MASK,
            },
            data=body,
            timeout=self.timeout,
        )
        return (payload or {}).get("places", []) or []

    @staticmethod
    def _same_place(candidate: dict, name: str, lat: float | None, lng: float | None) -> bool:
        """상호명이 같거나, 좌표가 충분히 가까우면 같은 가게로 본다."""
        display = ((candidate.get("displayName") or {}).get("text") or "").replace(" ", "")
        if display and display == name.replace(" ", ""):
            return True
        location = candidate.get("location") or {}
        if lat is None or lng is None:
            return False
        c_lat, c_lng = location.get("latitude"), location.get("longitude")
        if c_lat is None or c_lng is None:
            return False
        return haversine_m(lat, lng, c_lat, c_lng) <= MATCH_RADIUS_M

    @staticmethod
    def parse_hours(place: dict) -> dict[str, Any] | None:
        hours = place.get("regularOpeningHours") or {}
        return from_google_periods(hours.get("periods"), hours.get("weekdayDescriptions"))

    def fetch_hours_by_id(self, google_place_id: str) -> dict[str, Any] | None:
        """이미 알고 있는 구글 place id 로 바로 조회. 검색을 건너뛰어 더 싸고 정확하다."""
        if not google_place_id:
            return None
        try:
            payload = http_json(
                f"{DETAIL_ENDPOINT}{google_place_id}",
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": "id,regularOpeningHours.periods,"
                                        "regularOpeningHours.weekdayDescriptions",
                },
                timeout=self.timeout,
            )
        except ProviderError:
            return None
        parsed = self.parse_hours(payload or {})
        if parsed:
            parsed["google_place_id"] = google_place_id
        return parsed

    def fetch_hours(
        self,
        name: str,
        address: str = "",
        lat: float | None = None,
        lng: float | None = None,
        google_place_id: str | None = None,
    ) -> dict[str, Any] | None:
        """{'lunch_open', 'text', 'google_place_id'} 또는 None. 실패는 조용히 None."""
        if google_place_id:
            cached = self.fetch_hours_by_id(google_place_id)
            if cached:
                return cached
        anchor_lat = lat if lat is not None else 37.5665
        anchor_lng = lng if lng is not None else 126.9780
        query = " ".join(filter(None, [name, address])).strip()
        if not query:
            return None
        try:
            results = self.search(query, anchor_lat, anchor_lng)
        except ProviderError:
            return None
        time.sleep(self.request_delay)

        for candidate in results:
            if not self._same_place(candidate, name, lat, lng):
                continue
            parsed = self.parse_hours(candidate)
            if parsed:
                parsed["google_place_id"] = candidate.get("id")
                return parsed
            return None       # 가게는 찾았는데 영업시간이 없는 경우
        return None
