"""네이버에서 주변 식당을 모아 DB 에 채우는 작업."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import budget as budget_mod
from . import db as dbm
from .categories import classify
from .providers import (
    GooglePlacesProvider, NaverLocalProvider, NaverPlaceReviewProvider, ProviderError,
)


@dataclass
class SyncState:
    running: bool = False
    done: int = 0
    total: int = 0
    added: int = 0
    message: str = ""
    error: str = ""
    log: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running, "done": self.done, "total": self.total,
            "added": self.added, "message": self.message, "error": self.error,
            "log": self.log[-30:],
        }


def sync_places(
    conn: sqlite3.Connection,
    provider: NaverLocalProvider,
    office_lat: float,
    office_lng: float,
    area_keyword: str,
    radius_m: int,
    state: SyncState | None = None,
    lock: threading.Lock | None = None,
) -> int:
    """반경 안 식당을 수집해 upsert. 새로 들어온 건수를 돌려준다."""
    state = state or SyncState()
    added = 0

    def progress(done: int, total: int, query: str, note: str) -> None:
        state.done, state.total = done, total
        state.message = f"{query} — {note}"
        state.log.append(state.message)

    for place in provider.collect_nearby(
        office_lat, office_lng, area_keyword, radius_m, on_progress=progress
    ):
        major, detail = classify(place.raw_category, place.name)
        row = {
            "id": place.id, "name": place.name,
            "road_address": place.road_address, "address": place.address,
            "lat": place.lat, "lng": place.lng, "raw_category": place.raw_category,
            "major_category": major, "detail_category": detail,
            "phone": place.phone, "link": place.link,
            "naver_place_id": place.naver_place_id,
            "distance_m": place.distance_m, "source": place.source,
        }
        if lock:
            with lock:
                dbm.upsert_place(conn, row)
                conn.commit()
        else:
            dbm.upsert_place(conn, row)
            conn.commit()
        added += 1
        state.added = added
    return added


def enrich_places(
    conn: sqlite3.Connection,
    reviewer: NaverPlaceReviewProvider | None = None,
    google: GooglePlacesProvider | None = None,
    limit: int = 50,
    google_call_limit: int = 900,
    state: SyncState | None = None,
    lock: threading.Lock | None = None,
    only_missing: bool = True,
) -> int:
    """'음식이 맛있어요' 비율과 영업시간(점심 영업 여부)을 채운다.

    네이버 지도에서 [영업중 · 12시] 필터를 거는 것과 같은 판정을 자동으로 한다.

    영업시간은 구글 Places(공식 API)를 1순위로 쓰고, 구글 키가 없거나 못 찾으면
    네이버 플레이스(비공식)로 넘어간다. '맛있어요' 비율은 네이버에만 있다.
    사람이 손으로 표시한 값(lunch_source='manual')은 어느 쪽도 덮어쓰지 않는다.
    실패한 곳은 조용히 건너뛴다.
    """
    state = state or SyncState()
    sql = """SELECT id, name, road_address, address, link,
                    naver_place_id, google_place_id, lat, lng
               FROM places WHERE is_active = 1"""
    if only_missing:
        sql += """ AND (
                    (taste_ratio IS NULL AND (taste_source IS NULL OR taste_source <> 'manual'))
                 OR (lunch_open  IS NULL AND (lunch_source IS NULL OR lunch_source <> 'manual'))
               )"""
    sql += " ORDER BY distance_m LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    state.total = len(rows)
    filled = 0
    budget_warned = False

    for idx, row in enumerate(rows, start=1):
        state.done = idx
        address = row["road_address"] or row["address"] or ""
        info: dict = {}

        # 1순위: 구글 공식 API 로 영업시간 (이번 달 호출 한도 안에서만)
        if google is not None and budget_mod.remaining(conn, google_call_limit) <= 0:
            if not budget_warned:
                budget_warned = True
                state.log.append(
                    f"이번 달 구글 호출 한도({google_call_limit}건)를 다 썼습니다. "
                    "남은 곳은 네이버 쪽으로 처리합니다."
                )
            google = None
        if google is not None:
            budget_mod.consume(conn, 1)
            hours = google.fetch_hours(
                name=row["name"], address=address,
                lat=row["lat"], lng=row["lng"],
                google_place_id=row["google_place_id"],
            )
            if hours:
                info["lunch_open"] = hours["lunch_open"]
                info["business_hours"] = hours["text"]
                info["google_place_id"] = hours.get("google_place_id")

        # 네이버: '맛있어요' 비율 (+ 구글이 못 찾았으면 영업시간까지)
        if reviewer is not None:
            naver_info = reviewer.enrich(
                name=row["name"], address=address, link=row["link"] or "",
                place_id=row["naver_place_id"],
                with_hours="lunch_open" not in info,
            )
            if naver_info:
                for key, value in naver_info.items():
                    info.setdefault(key, value)

        if not info:
            state.message = f"{row['name']} — 정보 없음"
            state.log.append(state.message)
            continue

        notes = []
        if info.get("ratio") is not None:
            notes.append(f"맛있어요 {round(info['ratio'] * 100)}%")
        if "lunch_open" in info:
            source = "구글" if info.get("google_place_id") else "네이버"
            notes.append(("점심 영업" if info["lunch_open"] else "점심 안 함") + f"({source})")

        def write() -> None:
            if info.get("ratio") is not None:
                dbm.set_taste(
                    conn, row["id"], info.get("ratio"), info.get("votes"),
                    info.get("review_total") or info.get("total"), "naver_place",
                )
            if "lunch_open" in info:
                # 사람이 이미 표시해 둔 곳은 자동 판정으로 덮지 않는다.
                current = conn.execute(
                    "SELECT lunch_source FROM places WHERE id = ?", (row["id"],)
                ).fetchone()
                if not current or current["lunch_source"] != "manual":
                    dbm.set_lunch_open(
                        conn, row["id"], info["lunch_open"],
                        "google_places" if info.get("google_place_id") else "naver_place",
                        info.get("business_hours"),
                    )
            if info.get("google_place_id"):
                conn.execute(
                    "UPDATE places SET google_place_id = ? WHERE id = ?",
                    (info["google_place_id"], row["id"]),
                )
            if info.get("place_id"):
                conn.execute(
                    "UPDATE places SET naver_place_id = ? WHERE id = ?",
                    (info["place_id"], row["id"]),
                )
            conn.commit()

        if lock:
            with lock:
                write()
        else:
            write()

        filled += 1
        state.added = filled
        state.message = f"{row['name']} — " + (" · ".join(notes) or "갱신")
        state.log.append(state.message)
    return filled


# 예전 이름. 하는 일이 늘어서 이름을 바꿨다.
enrich_taste = enrich_places


def _paste_id(name: str) -> str:
    return "paste:" + name.replace(" ", "")


def _import_without_coords(
    conn: sqlite3.Connection, name: str, category: str,
    lock: threading.Lock | None = None,
) -> str:
    """좌표 없이 상호명·업종만으로 등록한다.

    추천 쿼리가 distance_m IS NULL 을 후보로 받아 주기 때문에,
    거리를 몰라도 추천은 그대로 돌아간다. 반경 필터만 적용되지 않을 뿐이다.
    """
    major, detail = classify(category, name)
    pid = _paste_id(name)
    row = {
        "id": pid, "name": name, "road_address": "", "address": "",
        "lat": None, "lng": None, "raw_category": category,
        "major_category": major, "detail_category": detail,
        "phone": "", "link": "", "naver_place_id": None,
        "distance_m": None, "source": "naver_paste",
    }

    def write() -> None:
        dbm.upsert_place(conn, row)
        dbm.set_lunch_open(conn, pid, True, "manual")
        conn.commit()

    if lock:
        with lock:
            write()
    else:
        write()
    return pid


def import_named_places(
    conn: sqlite3.Connection,
    provider: NaverLocalProvider | None,
    entries: list[dict[str, str]],
    office_lat: float,
    office_lng: float,
    area_keyword: str = "",
    mark_others_no_lunch: bool = False,
    radius_m: int = 0,
    state: SyncState | None = None,
    lock: threading.Lock | None = None,
) -> int:
    """상호명 목록을 받아 좌표·분류를 채워 등록하고 '점심 영업'으로 표시한다.

    네이버 지도에서 [영업중 · 12시] 필터를 걸고 나온 목록을 그대로 붙여넣는 용도.
    mark_others_no_lunch 를 켜면 목록에 없는 기존 식당을 '점심 안 함'으로 돌린다.
    (= 붙여넣은 목록이 점심 영업하는 곳 전부라고 선언하는 것)
    """
    state = state or SyncState()
    state.total = len(entries)
    imported_ids: set[str] = set()
    distances: list[float] = []

    if provider is None:
        state.log.append(
            "네이버 검색 API 키가 없어 좌표 없이 등록합니다. "
            "지도 화면에 보이던 범위가 곧 거리 기준이 됩니다."
        )

    for idx, entry in enumerate(entries, start=1):
        state.done = idx
        name = (entry.get("name") or "").strip()
        if not name:
            continue
        category = (entry.get("category") or "").strip()

        # 키가 없으면 좌표 조회를 건너뛰고 붙여넣기에서 얻은 업종으로만 등록한다.
        if provider is None:
            pid = _import_without_coords(conn, name, category, lock)
            imported_ids.add(pid)
            state.added = len(imported_ids)
            major, detail = classify(category, name)
            state.message = f"{name} — 등록 ({major}·{detail}, 거리 미상)"
            state.log.append(state.message)
            continue

        query = f"{area_keyword} {name}".strip()
        try:
            items = provider.search(query, display=5)
        except ProviderError as exc:
            state.message = f"{name} — 검색 실패: {exc}"
            state.log.append(state.message)
            continue

        matched = None
        for item in items:
            place = provider._to_place(item, (office_lat, office_lng))
            if place and place.name.replace(" ", "") == name.replace(" ", ""):
                matched = place
                break
        if matched is None:
            state.message = f"{name} — 못 찾음 (상호명을 지도와 똑같이 적어 주세요)"
            state.log.append(state.message)
            continue

        major, detail = classify(matched.raw_category or category, matched.name)
        row = {
            "id": matched.id, "name": matched.name,
            "road_address": matched.road_address, "address": matched.address,
            "lat": matched.lat, "lng": matched.lng, "raw_category": matched.raw_category,
            "major_category": major, "detail_category": detail,
            "phone": matched.phone, "link": matched.link,
            "naver_place_id": matched.naver_place_id,
            "distance_m": matched.distance_m, "source": "naver_import",
        }

        def write() -> None:
            dbm.upsert_place(conn, row)
            dbm.set_lunch_open(conn, row["id"], True, "manual")
            # 예전에 키 없이 등록해 둔 같은 곳이 있으면 지운다 (추천에 두 번 뜨지 않게)
            stale = _paste_id(name)
            if stale != row["id"]:
                conn.execute("DELETE FROM places WHERE id = ?", (stale,))
            conn.commit()

        if lock:
            with lock:
                write()
        else:
            write()

        imported_ids.add(matched.id)
        if matched.distance_m is not None:
            distances.append(matched.distance_m)
        state.added = len(imported_ids)
        state.message = f"{matched.name} — 등록 ({major}·{detail}, {round(matched.distance_m or 0)}m)"
        state.log.append(state.message)

    if distances:
        near = sum(1 for d in distances if radius_m and d <= radius_m)
        state.log.append(
            f"회사에서 직선거리 기준 가장 가까운 곳 {round(min(distances))}m, "
            f"가장 먼 곳 {round(max(distances))}m."
            + (f" {radius_m}m 안은 {near}곳입니다." if radius_m else "")
        )

    if mark_others_no_lunch and imported_ids:
        marks = [
            (r["id"],)
            for r in conn.execute(
                "SELECT id FROM places WHERE is_active = 1 AND lunch_open IS NOT 0"
            ).fetchall()
            if r["id"] not in imported_ids
        ]

        def write_others() -> None:
            for (pid,) in marks:
                dbm.set_lunch_open(conn, pid, False, "manual")
            conn.commit()

        if lock:
            with lock:
                write_others()
        else:
            write_others()
        state.log.append(f"목록에 없는 {len(marks)}곳을 '점심 안 함'으로 표시했습니다.")

    return len(imported_ids)


def run_in_thread(fn: Callable[[], Any], state: SyncState) -> threading.Thread:
    def wrapper() -> None:
        state.running = True
        state.error = ""
        state.log.clear()
        try:
            fn()
            state.message = f"완료 — {state.added}곳 처리"
        except ProviderError as exc:
            state.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - 백그라운드 작업이라 삼킨다
            state.error = f"{type(exc).__name__}: {exc}"
        finally:
            state.running = False

    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return thread
