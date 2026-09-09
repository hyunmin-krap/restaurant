"""SQLite 스키마와 접근 헬퍼."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    road_address      TEXT,
    address           TEXT,
    lat               REAL,
    lng               REAL,
    raw_category      TEXT,
    major_category    TEXT,
    detail_category   TEXT,
    phone             TEXT,
    link              TEXT,
    naver_place_id    TEXT,
    distance_m        REAL,
    taste_ratio       REAL,          -- '음식이 맛있어요' 비율 (0.0 ~ 1.0)
    taste_votes       INTEGER,
    review_total      INTEGER,
    taste_source      TEXT,          -- naver_place | manual
    taste_updated_at  TEXT,
    lunch_open        INTEGER,       -- 1=점심 영업, 0=점심 영업 안 함, NULL=모름
    lunch_source      TEXT,          -- naver_place | manual
    business_hours    TEXT,          -- 영업시간 원문 (있으면 화면에 표시)
    source            TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_places_active ON places(is_active, distance_m);
CREATE INDEX IF NOT EXISTS idx_places_detail ON places(detail_category);

CREATE TABLE IF NOT EXISTS blocks (
    place_id    TEXT PRIMARY KEY REFERENCES places(id) ON DELETE CASCADE,
    reason      TEXT,
    blocked_by  TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id    TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    rater       TEXT NOT NULL,
    stars       INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    comment     TEXT,
    visited_on  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (place_id, rater)
);
CREATE INDEX IF NOT EXISTS idx_ratings_place ON ratings(place_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    TEXT NOT NULL,
    place_id    TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    picked_on   TEXT NOT NULL,       -- YYYY-MM-DD (KST)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reco_place ON recommendations(place_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reco_batch ON recommendations(batch_id);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# 나중에 추가된 컬럼들. 기존 DB 를 그대로 쓰면서 채워 넣는다.
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("places", "lunch_open INTEGER"),
    ("places", "lunch_source TEXT"),
    ("places", "business_hours TEXT"),
)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> list[str]:
    """예전 버전 DB 에 빠진 컬럼을 채운다. 데이터는 건드리지 않는다."""
    applied = []
    for table, column_def in MIGRATIONS:
        column = column_def.split()[0]
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            applied.append(f"{table}.{column}")
    if applied:
        conn.commit()
    return applied


def set_lunch_open(
    conn: sqlite3.Connection,
    place_id: str,
    lunch_open: bool | None,
    source: str = "manual",
    business_hours: str | None = None,
) -> None:
    """점심 영업 여부를 기록한다. None 이면 '모름'으로 되돌린다."""
    value = None if lunch_open is None else int(bool(lunch_open))
    conn.execute(
        """UPDATE places
              SET lunch_open = ?, lunch_source = ?,
                  business_hours = COALESCE(?, business_hours), updated_at = ?
            WHERE id = ?""",
        (value, source if value is not None else None, business_hours, now_iso(), place_id),
    )


def upsert_place(conn: sqlite3.Connection, place: dict[str, Any]) -> None:
    """수집한 식당 정보를 넣거나 갱신한다.

    사람이 손댄 값(taste_source='manual', is_active)은 덮어쓰지 않는다.
    """
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO places (
            id, name, road_address, address, lat, lng, raw_category,
            major_category, detail_category, phone, link, naver_place_id,
            distance_m, source, is_active, created_at, updated_at
        ) VALUES (
            :id, :name, :road_address, :address, :lat, :lng, :raw_category,
            :major_category, :detail_category, :phone, :link, :naver_place_id,
            :distance_m, :source, 1, :ts, :ts
        )
        ON CONFLICT(id) DO UPDATE SET
            name            = excluded.name,
            road_address    = excluded.road_address,
            address         = excluded.address,
            lat             = excluded.lat,
            lng             = excluded.lng,
            raw_category    = excluded.raw_category,
            major_category  = excluded.major_category,
            detail_category = excluded.detail_category,
            phone           = excluded.phone,
            link            = excluded.link,
            naver_place_id  = COALESCE(excluded.naver_place_id, places.naver_place_id),
            distance_m      = excluded.distance_m,
            updated_at      = excluded.updated_at
        """,
        {**place, "ts": ts},
    )


def set_taste(
    conn: sqlite3.Connection,
    place_id: str,
    ratio: float | None,
    votes: int | None,
    total: int | None,
    source: str,
) -> None:
    conn.execute(
        """UPDATE places
              SET taste_ratio = ?, taste_votes = ?, review_total = ?,
                  taste_source = ?, taste_updated_at = ?, updated_at = ?
            WHERE id = ?""",
        (ratio, votes, total, source, now_iso(), now_iso(), place_id),
    )


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                          updated_at = excluded.updated_at""",
        (key, value, now_iso()),
    )
    conn.commit()


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
