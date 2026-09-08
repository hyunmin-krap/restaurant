"""네이버 플레이스 방문자 리뷰의 '음식이 맛있어요' 비율 (선택 기능).

주의: 네이버는 이 지표를 공개 API 로 제공하지 않는다. 여기서는 플레이스
웹이 쓰는 내부 GraphQL 엔드포인트를 그대로 호출한다. 즉,
  * 네이버가 스키마를 바꾸거나 막으면 언제든 동작을 멈춘다.
  * 서비스 약관상 회색지대다. 사내에서 소규모로 쓰는 용도로만 두고,
    기본값은 꺼짐(ENABLE_PLACE_REVIEW_SCRAPE=0)이다.
실패는 전부 None 으로 흘려보내고 예외를 밖으로 던지지 않는다.
앱은 이 값이 없어도 정상 동작하며, 관리 화면에서 손으로 입력할 수도 있다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from .base import ProviderError, http_json

GRAPHQL_ENDPOINT = "https://pcmap-api.place.naver.com/graphql"
SEARCH_ENDPOINT = "https://map.naver.com/p/api/search/allSearch"

# '음식이 맛있어요' 를 찾을 때 쓰는 표현. 네이버가 문구를 조금씩 바꾼다.
TASTE_PATTERNS = ("음식이 맛있", "맛있어요", "음식이맛있")

_PLACE_ID_IN_LINK = re.compile(r"(?:place|restaurant|entry)[/=](\d{6,})")

_STATS_QUERY = """
query getVisitorReviewStats($input: VisitorReviewStatsInput) {
  visitorReviewStats(input: $input) {
    id
    review { totalCount }
    analysis {
      votedKeyword {
        totalCount
        userCount
        details { code displayName category count }
      }
    }
  }
}
"""


class NaverPlaceReviewProvider:
    def __init__(self, request_delay: float = 0.5, timeout: float = 8.0) -> None:
        self.request_delay = request_delay
        self.timeout = timeout

    # ── 플레이스 ID 찾기 ────────────────────────────────────────────
    @staticmethod
    def place_id_from_link(link: str | None) -> str | None:
        if not link:
            return None
        match = _PLACE_ID_IN_LINK.search(link)
        return match.group(1) if match else None

    def resolve_place_id(self, name: str, address: str = "") -> str | None:
        """상호명(+주소)로 플레이스 ID 를 찾는다. 못 찾으면 None."""
        query = f"{address.split()[0] if address else ''} {name}".strip()
        params = urllib.parse.urlencode({"query": query, "type": "all", "searchCoord": ""})
        try:
            payload = http_json(
                f"{SEARCH_ENDPOINT}?{params}",
                headers={"Referer": "https://map.naver.com/"},
                timeout=self.timeout,
            )
        except ProviderError:
            return None
        try:
            items = payload["result"]["place"]["list"]
        except (KeyError, TypeError):
            return None
        for item in items or []:
            if str(item.get("name", "")).replace(" ", "") == name.replace(" ", ""):
                return str(item.get("id"))
        first = (items or [None])[0]
        return str(first["id"]) if first and first.get("id") else None

    # ── 리뷰 키워드 통계 ────────────────────────────────────────────
    def fetch_taste_ratio(self, place_id: str) -> dict | None:
        """{'ratio', 'votes', 'total', 'keyword'} 또는 None."""
        body = json.dumps(
            [
                {
                    "operationName": "getVisitorReviewStats",
                    "query": _STATS_QUERY,
                    "variables": {"input": {"businessId": str(place_id), "businessType": "restaurant"}},
                }
            ]
        ).encode("utf-8")
        try:
            payload = http_json(
                GRAPHQL_ENDPOINT,
                headers={
                    "Content-Type": "application/json",
                    "Referer": f"https://pcmap.place.naver.com/restaurant/{place_id}/review/visitor",
                },
                data=body,
                timeout=self.timeout,
            )
        except ProviderError:
            return None
        return self.parse_stats(payload)

    @staticmethod
    def parse_stats(payload) -> dict | None:
        """GraphQL 응답에서 '음식이 맛있어요' 비율을 뽑는다."""
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        stats = (((payload or {}).get("data") or {}).get("visitorReviewStats")) or {}
        voted = ((stats.get("analysis") or {}).get("votedKeyword")) or {}
        details = voted.get("details") or []
        # 분모: 키워드를 선택한 방문자 수 (없으면 전체 키워드 투표 수)
        total = voted.get("userCount") or voted.get("totalCount") or 0
        review_total = ((stats.get("review") or {}).get("totalCount")) or None
        if not details or not total:
            return None
        for detail in details:
            label = str(detail.get("displayName") or detail.get("code") or "")
            if any(p in label.replace(" ", "") or p in label for p in TASTE_PATTERNS):
                votes = int(detail.get("count") or 0)
                return {
                    "ratio": round(votes / total, 4) if total else None,
                    "votes": votes,
                    "total": int(total),
                    "review_total": review_total,
                    "keyword": label,
                }
        return None

    def enrich(self, name: str, address: str = "", link: str = "", place_id: str | None = None) -> dict | None:
        pid = place_id or self.place_id_from_link(link) or self.resolve_place_id(name, address)
        if not pid:
            return None
        time.sleep(self.request_delay)
        stats = self.fetch_taste_ratio(pid)
        if stats:
            stats["place_id"] = pid
        return stats
