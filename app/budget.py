"""외부 API 호출 한도를 앱 자체에서 관리한다.

바깥 서비스의 한도만 믿지 않는 이유:

  * 네이버 API HUB 는 지금 무료지만 종량제라, 유료로 전환되면 쓴 만큼 자동 결제된다.
    콘솔에도 한도·알림이 있지만 그건 '넘고 나서' 알려 주는 쪽에 가깝다.
  * 구글 Places 는 영업시간(regularOpeningHours)이 Enterprise SKU 라
    무료 한도가 월 1,000건으로 다른 필드보다 빡빡하다.

이 앱의 실제 호출량은 둘 다 한참 밑이다(버튼을 누를 때만 부르고, 평소엔 0건).
그래서 사고로 폭주하는 경우만 막으면 되고, 여기서 그 마지막 방어선을 친다.
한도에 걸리면 호출을 멈춘다. 구글은 조용히 네이버로 넘어가고,
네이버는 BudgetExhausted 를 올려 화면에 사유를 보여 준다.

카운터는 settings 테이블에 KST 기준 일별·월별로 쌓는다.
지워도 되고, 지우면 그 기간 사용량이 0 으로 돌아간다.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import db as dbm


def _kst(now: datetime | None = None) -> datetime:
    """호출 한도는 한국 시간 기준으로 끊는다."""
    now = now or datetime.now(timezone.utc)
    return datetime.fromtimestamp(now.timestamp() + 9 * 3600, tz=timezone.utc)


def month_key(provider: str, now: datetime | None = None) -> str:
    return f"{provider}_calls_{_kst(now):%Y-%m}"


def day_key(provider: str, now: datetime | None = None) -> str:
    return f"{provider}_calls_{_kst(now):%Y-%m-%d}"


def _read(conn: sqlite3.Connection, key: str) -> int:
    raw = dbm.get_setting(conn, key, "0")
    try:
        return max(0, int(raw or 0))
    except ValueError:
        # 사람이 손으로 건드렸거나 깨진 값. 0 으로 보고 다시 쌓는다.
        return 0


def used(conn: sqlite3.Connection, provider: str, now: datetime | None = None) -> int:
    """이번 달 사용량."""
    return _read(conn, month_key(provider, now))


def used_today(conn: sqlite3.Connection, provider: str, now: datetime | None = None) -> int:
    return _read(conn, day_key(provider, now))


def remaining(
    conn: sqlite3.Connection,
    provider: str,
    monthly_limit: int,
    daily_limit: int = 0,
    now: datetime | None = None,
) -> int:
    """남은 호출 수. 일·월 한도 중 더 빡빡한 쪽을 따른다."""
    left = max(0, monthly_limit - used(conn, provider, now))
    if daily_limit > 0:
        left = min(left, max(0, daily_limit - used_today(conn, provider, now)))
    return left


def consume(
    conn: sqlite3.Connection, provider: str, count: int = 1, now: datetime | None = None
) -> int:
    """호출 수를 일·월 카운터에 함께 더하고 이번 달 누적을 돌려준다."""
    count = max(0, count)
    total = used(conn, provider, now) + count
    dbm.set_setting(conn, month_key(provider, now), str(total))
    dbm.set_setting(conn, day_key(provider, now), str(used_today(conn, provider, now) + count))
    return total


def status(
    conn: sqlite3.Connection,
    provider: str,
    monthly_limit: int,
    daily_limit: int = 0,
    now: datetime | None = None,
) -> dict:
    """화면에 그대로 뿌릴 수 있는 사용량 요약."""
    spent = used(conn, provider, now)
    today = used_today(conn, provider, now)
    left = remaining(conn, provider, monthly_limit, daily_limit, now)
    return {
        "provider": provider,
        "month": f"{_kst(now):%Y-%m}",
        "used": spent,
        "used_today": today,
        "limit": monthly_limit,
        "daily_limit": daily_limit,
        "remaining": left,
        "exhausted": left <= 0,
    }
