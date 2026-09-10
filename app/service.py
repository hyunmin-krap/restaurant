"""DB 와 추천 로직을 잇는 서비스 계층. HTTP 와 CLI 가 같이 쓴다."""
from __future__ import annotations

import random
import re
import sqlite3
from typing import Any

from . import db as dbm
from .categories import classify
from urllib.parse import quote

from .geo import haversine_m, walking_minutes
from .recommender import Pick, batch_id, explain, recommend, today_kst

# 네이버 지도 앱 스킴이 요구하는 호출자 식별자.
APP_NAME = "lunch.picker"

CANDIDATE_SQL = """
SELECT p.*,
       (SELECT AVG(stars)        FROM ratings r  WHERE r.place_id  = p.id) AS team_avg,
       (SELECT COUNT(*)          FROM ratings r  WHERE r.place_id  = p.id) AS team_count,
       (SELECT MAX(rc.created_at) FROM recommendations rc WHERE rc.place_id = p.id) AS last_recommended_at
  FROM places p
 WHERE p.is_active = 1
   AND p.id NOT IN (SELECT place_id FROM blocks)
   AND (p.lunch_open IS NULL OR p.lunch_open = 1)
   AND (p.distance_m IS NULL OR p.distance_m <= :radius)
"""


# 지번주소에서 동 이름만 집어낸다. '서울 마포구 도화동 25-1' -> '도화동'
# 'A동 2F' 처럼 건물 동·층은 한글로 시작하지 않아서 걸리지 않는다.
_DONG = re.compile(r"[가-힣]{2,}\d*(?:동|가|읍|면|리)(?=\s|$)")
_GU = re.compile(r"[가-힣]{2,}(?:구|군|시)(?=\s|$)")


def _locality(place: dict[str, Any]) -> str:
    """상호명에 붙일 짧은 지역명. 없으면 빈 문자열."""
    # 지번주소를 먼저 본다. 도로명주소에는 건물 이름·층이 붙어 있어 검색을 망친다.
    for field in ("address", "road_address"):
        text = (place.get(field) or "").strip()
        if not text:
            continue
        found = _DONG.search(text) or _GU.search(text)
        if found:
            return found.group(0)
    return ""


def _search_query(place: dict[str, Any]) -> str:
    """네이버 지도 검색어.

    주소를 통째로 붙이면 '몽중헌 공덕점 서울특별시 마포구 마포대로 92 효성
    해링턴스퀘어 A동 2F' 가 되어 오히려 아무것도 안 나온다.
    상호명 + 동 이름이면 충분히 좁혀지고 확실히 걸린다.
    """
    name = (place.get("name") or "").strip()
    dong = _locality(place)
    # 상호명에 이미 지역이 들어 있으면('공덕점') 굳이 또 붙이지 않는다.
    if dong and dong[:-1] and dong[:-1] in name.replace(" ", ""):
        dong = ""
    return " ".join(filter(None, [name, dong]))


def naver_map_url(place: dict[str, Any]) -> str:
    """네이버 지도 웹 링크. 플레이스 ID 를 알면 상세 페이지로 바로 보낸다."""
    pid = place.get("naver_place_id")
    if pid:
        return f"https://map.naver.com/p/entry/place/{pid}"
    return f"https://map.naver.com/p/search/{quote(_search_query(place))}"


def naver_app_url(place: dict[str, Any]) -> str:
    """모바일 네이버 지도 앱 스킴. 앱이 없으면 열리지 않으므로 웹 링크로 폴백한다."""
    pid = place.get("naver_place_id")
    if pid:
        return f"nmap://place?id={pid}&appname={APP_NAME}"
    return f"nmap://search?query={quote(_search_query(place))}&appname={APP_NAME}"


def naver_directions_url(place: dict[str, Any], office: tuple[float, float] | None,
                         office_name: str = "회사") -> str | None:
    """회사 -> 식당 도보 길찾기 링크. 좌표가 없으면 None."""
    lat, lng = place.get("lat"), place.get("lng")
    if office is None or lat is None or lng is None:
        return None
    start = f"{office[1]},{office[0]},{quote(office_name)}"
    goal = f"{lng},{lat},{quote(place.get('name') or '식당')}"
    return f"https://map.naver.com/p/directions/{start}/{goal}/-/walk"


def serialize_place(
    place: dict[str, Any],
    office: tuple[float, float] | None = None,
    office_name: str = "회사",
    exclude_ids: set[str] | None = None,
    exclude_details: set[str] | None = None,
    exclude_majors: set[str] | None = None,
) -> dict[str, Any]:
    distance = place.get("distance_m")
    taste = place.get("taste_ratio")
    team_avg = place.get("team_avg")
    return {
        "id": place["id"],
        "name": place["name"],
        "major_category": place.get("major_category") or "기타",
        "detail_category": place.get("detail_category") or "",
        "raw_category": place.get("raw_category") or "",
        "address": place.get("road_address") or place.get("address") or "",
        "phone": place.get("phone") or "",
        "distance_m": round(distance) if distance is not None else None,
        "walk_min": walking_minutes(distance) if distance is not None else None,
        "taste_ratio": round(taste, 4) if taste is not None else None,
        "taste_votes": place.get("taste_votes"),
        "taste_total": place.get("review_total"),
        "taste_source": place.get("taste_source"),
        "team_avg": round(team_avg, 2) if team_avg is not None else None,
        "team_count": place.get("team_count") or 0,
        "last_recommended_at": place.get("last_recommended_at"),
        "lunch_open": place.get("lunch_open"),
        "lunch_source": place.get("lunch_source"),
        "business_hours": place.get("business_hours") or "",
        "map_url": naver_map_url(place),
        "app_url": naver_app_url(place),
        "directions_url": naver_directions_url(place, office, office_name),
    }


def load_candidates(conn: sqlite3.Connection, radius_m: int) -> list[dict[str, Any]]:
    rows = conn.execute(CANDIDATE_SQL, {"radius": radius_m}).fetchall()
    return dbm.rows_to_dicts(rows)


def make_recommendation(
    conn: sqlite3.Connection,
    radius_m: int,
    count: int,
    record: bool = True,
    rng: random.Random | None = None,
    office: tuple[float, float] | None = None,
    office_name: str = "회사",
    exclude_ids: set[str] | None = None,
    exclude_details: set[str] | None = None,
    exclude_majors: set[str] | None = None,
) -> dict[str, Any]:
    rng = rng or random.Random()
    candidates = load_candidates(conn, radius_m)
    if exclude_ids:
        candidates = [c for c in candidates if c["id"] not in exclude_ids]
    picks: list[Pick] = recommend(
        candidates, count=count, radius_m=radius_m, rng=rng,
        exclude_details=exclude_details, exclude_majors=exclude_majors,
    )

    bid = batch_id(rng)
    if record and picks:
        ts = dbm.now_iso()
        conn.executemany(
            """INSERT INTO recommendations (batch_id, place_id, picked_on, created_at)
               VALUES (?, ?, ?, ?)""",
            [(bid, p.place["id"], today_kst(), ts) for p in picks],
        )
        conn.commit()

    items = []
    for pick in picks:
        item = serialize_place(pick.place, office, office_name)
        item["why"] = explain(pick)
        item["weight"] = round(pick.weight, 4)
        items.append(item)

    return {
        "batch_id": bid,
        "count": len(items),
        "requested": count,
        "candidate_pool": len(candidates),
        "radius_m": radius_m,
        "items": items,
        "note": _pool_note(len(candidates), len(items), count),
    }


def _pool_note(pool: int, got: int, want: int) -> str:
    if pool == 0:
        return "반경 안에 등록된 식당이 없습니다. 먼저 '식당 수집'을 실행하세요."
    if got < want:
        return f"세부분류가 겹치지 않게 뽑을 수 있는 곳이 {got}곳뿐입니다. 반경을 넓히거나 제외 목록을 확인해 보세요."
    return ""


# ── 별점 ────────────────────────────────────────────────────────────
def upsert_rating(
    conn: sqlite3.Connection,
    place_id: str,
    rater: str,
    stars: int,
    comment: str = "",
    visited_on: str | None = None,
) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM places WHERE id = ?", (place_id,)).fetchone():
        raise KeyError(place_id)
    if not 1 <= int(stars) <= 5:
        raise ValueError("별점은 1~5 사이여야 합니다.")
    rater = (rater or "").strip() or "익명"
    ts = dbm.now_iso()
    conn.execute(
        """INSERT INTO ratings (place_id, rater, stars, comment, visited_on, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(place_id, rater) DO UPDATE SET
               stars      = excluded.stars,
               comment    = excluded.comment,
               visited_on = excluded.visited_on,
               updated_at = excluded.updated_at""",
        (place_id, rater, int(stars), comment.strip(), visited_on, ts, ts),
    )
    conn.commit()
    return rating_summary(conn, place_id)


def rating_summary(conn: sqlite3.Connection, place_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT AVG(stars) AS avg, COUNT(*) AS cnt FROM ratings WHERE place_id = ?",
        (place_id,),
    ).fetchone()
    items = conn.execute(
        """SELECT rater, stars, comment, visited_on, updated_at
             FROM ratings WHERE place_id = ? ORDER BY updated_at DESC""",
        (place_id,),
    ).fetchall()
    return {
        "place_id": place_id,
        "team_avg": round(row["avg"], 2) if row["avg"] is not None else None,
        "team_count": row["cnt"],
        "ratings": dbm.rows_to_dicts(items),
    }


# ── 제외(다시 보여주지 않기) ─────────────────────────────────────────
def block_place(conn: sqlite3.Connection, place_id: str, reason: str = "", by: str = "") -> None:
    if not conn.execute("SELECT 1 FROM places WHERE id = ?", (place_id,)).fetchone():
        raise KeyError(place_id)
    conn.execute(
        """INSERT INTO blocks (place_id, reason, blocked_by, created_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(place_id) DO UPDATE SET reason = excluded.reason,
                                               blocked_by = excluded.blocked_by""",
        (place_id, reason.strip(), (by or "").strip(), dbm.now_iso()),
    )
    conn.commit()


def unblock_place(conn: sqlite3.Connection, place_id: str) -> None:
    conn.execute("DELETE FROM blocks WHERE place_id = ?", (place_id,))
    conn.commit()


def set_lunch_open(conn: sqlite3.Connection, place_id: str, lunch_open: bool | None) -> None:
    """'점심 영업 안 함'으로 표시하거나 되돌린다. 표시된 곳은 추천에서 빠진다."""
    if not conn.execute("SELECT 1 FROM places WHERE id = ?", (place_id,)).fetchone():
        raise KeyError(place_id)
    dbm.set_lunch_open(conn, place_id, lunch_open, source="manual")
    conn.commit()


def list_blocks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT b.place_id, b.reason, b.blocked_by, b.created_at,
                  p.name, p.major_category, p.detail_category
             FROM blocks b JOIN places p ON p.id = b.place_id
            ORDER BY b.created_at DESC"""
    ).fetchall()
    return dbm.rows_to_dicts(rows)


# ── 조회 ────────────────────────────────────────────────────────────
def list_places(
    conn: sqlite3.Connection,
    radius_m: int | None = None,
    office: tuple[float, float] | None = None,
    office_name: str = "회사",
) -> list[dict[str, Any]]:
    sql = """
    SELECT p.*,
           (SELECT AVG(stars) FROM ratings r WHERE r.place_id = p.id) AS team_avg,
           (SELECT COUNT(*)   FROM ratings r WHERE r.place_id = p.id) AS team_count,
           (SELECT MAX(rc.created_at) FROM recommendations rc WHERE rc.place_id = p.id) AS last_recommended_at,
           EXISTS(SELECT 1 FROM blocks b WHERE b.place_id = p.id) AS blocked
      FROM places p
     WHERE p.is_active = 1
    """
    params: dict[str, Any] = {}
    if radius_m is not None:
        sql += " AND (p.distance_m IS NULL OR p.distance_m <= :radius)"
        params["radius"] = radius_m
    sql += " ORDER BY p.distance_m IS NULL, p.distance_m"
    out = []
    for row in conn.execute(sql, params).fetchall():
        item = serialize_place(dict(row), office, office_name)
        item["blocked"] = bool(row["blocked"])
        out.append(item)
    return out


def history(conn: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT rc.batch_id, rc.picked_on, rc.created_at, p.id AS place_id, p.name,
                  p.major_category, p.detail_category,
                  (SELECT AVG(stars) FROM ratings r WHERE r.place_id = p.id) AS team_avg,
                  (SELECT COUNT(*)   FROM ratings r WHERE r.place_id = p.id) AS team_count
             FROM recommendations rc JOIN places p ON p.id = rc.place_id
            ORDER BY rc.created_at DESC LIMIT ?""",
        (limit * 5,),
    ).fetchall()
    batches: dict[str, dict[str, Any]] = {}
    for row in rows:
        batch = batches.setdefault(
            row["batch_id"],
            {"batch_id": row["batch_id"], "picked_on": row["picked_on"],
             "created_at": row["created_at"], "items": []},
        )
        batch["items"].append({
            "place_id": row["place_id"],
            "name": row["name"],
            "major_category": row["major_category"],
            "detail_category": row["detail_category"],
            "team_avg": round(row["team_avg"], 2) if row["team_avg"] is not None else None,
            "team_count": row["team_count"],
        })
    return list(batches.values())[:limit]


def stats(conn: sqlite3.Connection, radius_m: int) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) c FROM places WHERE is_active = 1").fetchone()["c"]
    in_radius = conn.execute(
        "SELECT COUNT(*) c FROM places WHERE is_active = 1 AND (distance_m IS NULL OR distance_m <= ?)",
        (radius_m,),
    ).fetchone()["c"]
    blocked = conn.execute("SELECT COUNT(*) c FROM blocks").fetchone()["c"]
    rated = conn.execute("SELECT COUNT(DISTINCT place_id) c FROM ratings").fetchone()["c"]
    with_taste = conn.execute(
        "SELECT COUNT(*) c FROM places WHERE taste_ratio IS NOT NULL"
    ).fetchone()["c"]
    no_lunch = conn.execute(
        "SELECT COUNT(*) c FROM places WHERE lunch_open = 0"
    ).fetchone()["c"]
    by_major = dbm.rows_to_dicts(
        conn.execute(
            """SELECT COALESCE(major_category,'기타') AS major, COUNT(*) AS cnt
                 FROM places
                WHERE is_active = 1 AND (distance_m IS NULL OR distance_m <= ?)
                GROUP BY major ORDER BY cnt DESC""",
            (radius_m,),
        ).fetchall()
    )
    return {
        "places_total": total,
        "places_in_radius": in_radius,
        "blocked": blocked,
        "no_lunch": no_lunch,
        "rated_places": rated,
        "places_with_taste": with_taste,
        "by_major": by_major,
    }


def add_manual_place(
    conn: sqlite3.Connection,
    name: str,
    office: tuple[float, float],
    address: str = "",
    raw_category: str = "",
    lat: float | None = None,
    lng: float | None = None,
    naver_place_id: str | None = None,
) -> str:
    """직접 등록하는 식당(수집에 안 걸리는 단골집 등)."""
    name = name.strip()
    if not name:
        raise ValueError("상호명이 필요합니다.")
    major, detail = classify(raw_category, name)
    distance = haversine_m(office[0], office[1], lat, lng) if lat and lng else None
    pid = f"manual:{name}|{address.strip()}"
    dbm.upsert_place(
        conn,
        {
            "id": pid, "name": name, "road_address": address.strip(), "address": address.strip(),
            "lat": lat, "lng": lng, "raw_category": raw_category,
            "major_category": major, "detail_category": detail,
            "phone": "", "link": "", "naver_place_id": naver_place_id,
            "distance_m": round(distance, 1) if distance is not None else None,
            "source": "manual",
        },
    )
    conn.commit()
    return pid
