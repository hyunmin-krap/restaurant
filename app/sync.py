"""네이버에서 주변 식당을 모아 DB 에 채우는 작업."""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from . import db as dbm
from .categories import classify
from .providers import NaverLocalProvider, NaverPlaceReviewProvider, ProviderError


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


def enrich_taste(
    conn: sqlite3.Connection,
    reviewer: NaverPlaceReviewProvider,
    limit: int = 50,
    state: SyncState | None = None,
    lock: threading.Lock | None = None,
    only_missing: bool = True,
) -> int:
    """'음식이 맛있어요' 비율을 채운다. 실패한 곳은 조용히 건너뛴다."""
    state = state or SyncState()
    sql = """SELECT id, name, road_address, address, link, naver_place_id
               FROM places WHERE is_active = 1"""
    if only_missing:
        sql += " AND (taste_ratio IS NULL AND (taste_source IS NULL OR taste_source <> 'manual'))"
    sql += " ORDER BY distance_m LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    state.total = len(rows)
    filled = 0
    for idx, row in enumerate(rows, start=1):
        state.done = idx
        stats = reviewer.enrich(
            name=row["name"],
            address=row["road_address"] or row["address"] or "",
            link=row["link"] or "",
            place_id=row["naver_place_id"],
        )
        if not stats:
            state.message = f"{row['name']} — 리뷰 지표 없음"
            state.log.append(state.message)
            continue
        writes = [
            lambda: dbm.set_taste(
                conn, row["id"], stats.get("ratio"), stats.get("votes"),
                stats.get("review_total") or stats.get("total"), "naver_place",
            ),
            lambda: conn.execute(
                "UPDATE places SET naver_place_id = ? WHERE id = ?",
                (stats.get("place_id"), row["id"]),
            ),
        ]
        if lock:
            with lock:
                for write in writes:
                    write()
                conn.commit()
        else:
            for write in writes:
                write()
            conn.commit()
        filled += 1
        state.added = filled
        pct = round((stats.get("ratio") or 0) * 100)
        state.message = f"{row['name']} — 맛있어요 {pct}%"
        state.log.append(state.message)
    return filled


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
