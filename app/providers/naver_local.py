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
from typing import Callable, Iterator

from ..categories import is_restaurant
from ..geo import haversine_m, parse_naver_coords
from .base import BudgetExhausted, Place, ProviderError, http_json, strip_tags


def _is_auth_failure(exc: ProviderError) -> bool:
    """키가 그쪽 것이 아니라서 막힌 건지. 그럴 때만 다른 주소를 시도한다."""
    text = str(exc)
    return any(f"HTTP {code} " in text for code in _AUTH_CODES)

# 지역검색을 부를 수 있는 곳이 두 군데다. 주소도 헤더 이름도 다르다.
#   * API HUB  - 네이버 클라우드. 2026-07-31 이후 새로 받는 키는 전부 이쪽이다.
#   * 개발자센터 - 그 전에 받아 둔 키. 2027-06-30 까지 쓸 수 있다.
# 키만 보고는 어느 쪽인지 알 수 없어서, 한 번 불러 보고 정한다.
FLAVORS: dict[str, dict] = {
    "hub": {
        "label": "NAVER API HUB",
        "endpoint": "https://naverapihub.apigw.ntruss.com/search/v1/local",
        "headers": ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY"),
    },
    "legacy": {
        "label": "네이버 개발자센터",
        "endpoint": "https://openapi.naver.com/v1/search/local.json",
        "headers": ("X-Naver-Client-Id", "X-Naver-Client-Secret"),
    },
}

# 키가 그쪽 것이 아닐 때 돌아오는 상태 코드. 이것만 다른 쪽을 시도한다.
_AUTH_CODES = (401, 403, 404)

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
    "구내식당", "비빔밥", "생선구이", "제육볶음", "닭갈비", "순두부", "육개장",
    "잔치국수", "콩국수", "낙지볶음", "장어", "케밥", "브런치", "딤섬",
)


class NaverLocalProvider:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        request_delay: float = 0.12,
        guard: Callable[[], None] | None = None,
        flavor: str = "auto",
    ) -> None:
        if not client_id or not client_secret:
            raise ProviderError(
                "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없습니다. "
                "네이버 클라우드 NAVER API HUB 에서 'NAVER 검색 > 지역' API 키를 받아 넣으세요. "
                "(개발자센터는 2026-07-31 부로 검색 API 신규 발급이 끝났습니다)"
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.request_delay = request_delay
        # 호출 직전마다 불린다. 한도를 넘었으면 BudgetExhausted 를 올려 막는다.
        # search() 한 곳만 통과하면 되므로 어느 경로로 들어와도 빠짐없이 세어진다.
        self.guard = guard
        # 'auto' 면 첫 호출에서 되는 쪽을 찾아 그다음부터는 그쪽만 쓴다.
        self.flavor = flavor if flavor in FLAVORS else "auto"

    def _headers(self, flavor: str) -> dict[str, str]:
        id_key, secret_key = FLAVORS[flavor]["headers"]
        return {id_key: self.client_id, secret_key: self.client_secret}

    def _call(self, flavor: str, params: str) -> list[dict]:
        url = f"{FLAVORS[flavor]['endpoint']}?{params}"
        payload = http_json(url, headers=self._headers(flavor))
        return payload.get("items", []) or []

    def search(self, query: str, display: int = 5) -> list[dict]:
        if self.guard is not None:
            self.guard()
        params = urllib.parse.urlencode(
            {"query": query, "display": max(1, min(5, display)), "start": 1, "sort": "random"}
        )
        if self.flavor != "auto":
            return self._call(self.flavor, params)

        # 어느 쪽 키인지 모를 때. HUB 를 먼저 보고, 인증에서 막히면 예전 쪽을 본다.
        # 되는 쪽을 찾으면 기억해서 다음부터는 한 번만 부른다.
        first_error: ProviderError | None = None
        for flavor in ("hub", "legacy"):
            try:
                items = self._call(flavor, params)
            except ProviderError as exc:
                if not _is_auth_failure(exc):
                    raise                    # 잠깐 끊긴 것 등은 그대로 올린다
                first_error = first_error or exc
                continue
            self.flavor = flavor
            return items
        raise ProviderError(
            "네이버 검색 API 키가 거절당했습니다. NAVER API HUB 와 개발자센터 양쪽 주소로 "
            f"시도했지만 둘 다 인증에 실패했습니다. Client ID/Secret 을 다시 확인해 주세요. "
            f"({first_error})"
        )

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
        failures: list[str] = []

        # 지역검색 API 는 한 질의에 최대 5건만 준다. 그래서 '공덕동 중식' 하나로는
        # 그 동네 중식당 5곳밖에 못 본다. 지역을 쉼표로 여러 개 주면 그만큼
        # 다른 5곳씩을 더 볼 수 있다. ('공덕동, 염리동, 도화동' 처럼)
        areas = [a.strip() for a in (area_keyword or "").split(",") if a.strip()] or [""]
        pairs = [(a, k) for k in keywords for a in areas]

        for idx, (area, keyword) in enumerate(pairs, start=1):
            query = f"{area} {keyword}".strip()
            try:
                items = self.search(query)
            except BudgetExhausted:
                raise                      # 남은 키워드를 더 돌아 봐야 소용없다
            except ProviderError as exc:
                failures.append(str(exc))
                if on_progress:
                    on_progress(idx, len(pairs), query, f"실패: {exc}")
                # 처음 몇 번이 내리 실패하면 키나 주소 문제다. 76번을 헛돌 이유가 없다.
                if len(failures) >= 3 and not seen:
                    raise ProviderError(
                        f"검색 요청이 계속 실패해서 멈췄습니다. {failures[0]}"
                    ) from exc
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
                on_progress(idx, len(pairs), query, f"{found}곳 추가")
            time.sleep(self.request_delay)

        if failures and not seen:
            raise ProviderError(f"{len(failures)}개 검색이 모두 실패했습니다. {failures[0]}")
