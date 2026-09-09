"""점심 추천 로직.

- 반경 안의 식당 중에서 랜덤으로 뽑되, 완전한 균등 추첨은 아니다.
  '음식이 맛있어요' 비율, 사내 별점, 최근에 뽑혔는지, 거리를 가중치로 쓴다.
- 한 번에 뽑는 N개는 세부분류(돈가스/국밥/...)가 서로 겹치지 않게 한다.
- 가본 적 없는 곳에는 약간의 가산점을 줘서 새로운 식당도 걸리게 한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Sequence

from .categories import dedupe_key

# 사내 별점 베이지안 보정: 표본이 적을 때 전체 평균 쪽으로 끌어당긴다.
PRIOR_STARS = 3.5
PRIOR_WEIGHT = 3.0

# 맛있어요 비율을 모를 때 쓰는 중립값
DEFAULT_TASTE = 0.55


@dataclass
class Pick:
    place: dict[str, Any]
    weight: float
    reasons: dict[str, float] = field(default_factory=dict)


def _days_since(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def taste_factor(taste_ratio: float | None, review_total: int | None) -> float:
    """'음식이 맛있어요' 비율 -> 0.6 ~ 1.6 배."""
    if taste_ratio is None:
        return 1.0
    ratio = min(1.0, max(0.0, taste_ratio))
    # 리뷰 수가 적으면 비율을 덜 신뢰한다.
    n = review_total or 0
    confidence = min(1.0, n / 30.0) if n else 0.4
    centered = (ratio - DEFAULT_TASTE) * confidence
    return max(0.4, 1.0 + centered * 1.6)


def team_factor(avg_stars: float | None, count: int | None) -> float:
    """사내 별점 -> 0.35 ~ 1.7 배. 표본이 적으면 영향이 작다."""
    if not count:
        return 1.0
    n = float(count)
    smoothed = (PRIOR_STARS * PRIOR_WEIGHT + (avg_stars or PRIOR_STARS) * n) / (PRIOR_WEIGHT + n)
    return max(0.35, 1.0 + (smoothed - PRIOR_STARS) * 0.45)


def novelty_factor(days_since_recommended: float | None) -> float:
    """최근에 뽑힌 곳은 덜, 아직 안 뽑힌 곳은 조금 더."""
    if days_since_recommended is None:
        return 1.25  # 아직 한 번도 안 뽑힌 곳 - 발굴 가산점
    if days_since_recommended < 1:
        return 0.05
    if days_since_recommended < 7:
        return 0.3
    if days_since_recommended < 14:
        return 0.7
    return 1.0


# 거리 가중치. 선형이 아니라 완만한 곡선을 쓴다.
#   가까운 구간끼리는 차이가 작아서 (지수 1.5) 코앞 몇 집으로 쏠리지 않고,
#   반경 끝으로 갈수록 확실히 떨어져서 먼 곳과 동등해지지도 않는다.
#   반경 500m 기준 50m 지점은 500m 지점보다 약 1.65배 자주 뽑힌다.
DISTANCE_NEAR = 1.25   # 바로 앞
DISTANCE_DROP = 0.50   # 반경 끝까지 떨어지는 폭
DISTANCE_CURVE = 1.5   # 클수록 가까운 구간이 평평해진다


def distance_factor(distance_m: float | None, radius_m: int) -> float:
    """반경 안에서 가까울수록 유리. 0.75 ~ 1.25배."""
    if distance_m is None or radius_m <= 0:
        return 1.0
    ratio = min(1.0, max(0.0, distance_m / radius_m))
    return max(0.7, DISTANCE_NEAR - DISTANCE_DROP * ratio**DISTANCE_CURVE)


def score(candidate: dict[str, Any], radius_m: int, now: datetime) -> tuple[float, dict[str, float]]:
    reasons = {
        "taste": taste_factor(candidate.get("taste_ratio"), candidate.get("review_total")),
        "team": team_factor(candidate.get("team_avg"), candidate.get("team_count")),
        "novelty": novelty_factor(_days_since(candidate.get("last_recommended_at"), now)),
        "distance": distance_factor(candidate.get("distance_m"), radius_m),
    }
    weight = 1.0
    for value in reasons.values():
        weight *= value
    return max(weight, 1e-6), reasons


def recommend(
    candidates: Sequence[dict[str, Any]],
    count: int = 3,
    radius_m: int = 500,
    rng: random.Random | None = None,
    now: datetime | None = None,
    avoid_same_major: bool = True,
    exclude_details: set[str] | None = None,
    exclude_majors: set[str] | None = None,
) -> list[Pick]:
    """가중 랜덤으로 count 개를 뽑는다. 세부분류는 절대 겹치지 않는다.

    후보가 부족해서 세부분류를 겹치지 않게 채울 수 없으면 채울 수 있는 만큼만
    돌려준다. (억지로 돈가스 두 곳을 넣지 않는다.)
    """
    rng = rng or random.Random()
    now = now or datetime.now(timezone.utc)
    if count <= 0 or not candidates:
        return []

    # Efraimidis-Spirakis: key = U^(1/w) 를 내림차순으로 = 비복원 가중 추출
    keyed: list[tuple[float, float, dict[str, float], dict[str, Any]]] = []
    for c in candidates:
        weight, reasons = score(c, radius_m, now)
        u = rng.random() or 1e-12
        keyed.append((u ** (1.0 / weight), weight, reasons, c))
    keyed.sort(key=lambda t: t[0], reverse=True)

    picks: list[Pick] = []
    # 이미 화면에 떠 있는 카드의 분류를 넘겨받아 그것도 피한다 (카드 한 장만 교체할 때)
    used_details: set[str] = set(exclude_details or ())
    used_majors: set[str] = set(exclude_majors or ())

    def take(require_major_unique: bool) -> None:
        for _, weight, reasons, c in keyed:
            if len(picks) >= count:
                return
            if any(p.place["id"] == c["id"] for p in picks):
                continue
            detail = dedupe_key(c.get("major_category", ""), c.get("detail_category", ""))
            if detail in used_details:
                continue
            major = (c.get("major_category") or "").strip()
            if require_major_unique and major and major in used_majors:
                continue
            picks.append(Pick(place=c, weight=weight, reasons=reasons))
            used_details.add(detail)
            if major:
                used_majors.add(major)

    if avoid_same_major:
        take(require_major_unique=True)
    take(require_major_unique=False)
    return picks


def explain(pick: Pick) -> str:
    """카드 하단에 붙일 한 줄 설명. 왜 이 집이 걸렸는지 짧게."""
    bits = []
    place = pick.place
    if pick.reasons.get("novelty", 1) >= 1.2:
        bits.append("아직 안 뽑힌 곳")
    taste = place.get("taste_ratio")
    if taste is not None and taste >= 0.8 and (place.get("review_total") or 0) >= 20:
        bits.append(f"맛있어요 {round(taste * 100)}%")
    if (place.get("team_count") or 0) >= 2 and (place.get("team_avg") or 0) >= 4.0:
        bits.append("사내 평점 높음")
    distance = place.get("distance_m")
    if distance is not None and distance <= 200:
        bits.append("걸어서 금방")
    return " · ".join(bits) or "오늘의 랜덤"


def today_kst() -> str:
    """KST 기준 오늘 날짜 문자열."""
    ts = datetime.now(timezone.utc).timestamp() + 9 * 3600
    return date.fromtimestamp(ts).isoformat()


def batch_id(rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    return f"{today_kst()}-{rng.randrange(16**6):06x}"


__all__ = [
    "Pick", "recommend", "score", "explain", "today_kst", "batch_id",
    "taste_factor", "team_factor", "novelty_factor", "distance_factor",
]
