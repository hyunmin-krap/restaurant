"""구글 API 호출 한도 관리.

구글 Places 는 결제 계정이 필요하고, 영업시간(regularOpeningHours)은
Enterprise SKU 라 무료 한도가 월 1,000건으로 다른 필드보다 빡빡하다.
이 앱의 실제 호출량은 그보다 훨씬 적지만(식당 수 만큼, 갱신할 때만),
사고로 한도를 넘겨 과금되는 일이 없도록 앱 자체에 상한을 둔다.

한도에 걸리면 구글 호출을 멈추고 네이버 쪽으로 조용히 넘어간다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import db as dbm

KEY_PREFIX = "google_calls_"


def month_key(now: datetime | None = None) -> str:
    """KST 기준 이번 달 카운터 키."""
    now = now or datetime.now(timezone.utc)
    kst = datetime.fromtimestamp(now.timestamp() + 9 * 3600, tz=timezone.utc)
    return f"{KEY_PREFIX}{kst:%Y-%m}"


def used(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    raw = dbm.get_setting(conn, month_key(now), "0")
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def remaining(conn: sqlite3.Connection, limit: int, now: datetime | None = None) -> int:
    return max(0, limit - used(conn, now))


def consume(conn: sqlite3.Connection, count: int = 1, now: datetime | None = None) -> int:
    """호출 수를 더하고 이번 달 누적을 돌려준다."""
    total = used(conn, now) + max(0, count)
    dbm.set_setting(conn, month_key(now), str(total))
    return total


def status(conn: sqlite3.Connection, limit: int, now: datetime | None = None) -> dict:
    spent = used(conn, now)
    return {
        "month": month_key(now)[len(KEY_PREFIX):],
        "used": spent,
        "limit": limit,
        "remaining": max(0, limit - spent),
        "exhausted": spent >= limit,
    }
