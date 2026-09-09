"""영업시간 -> '점심에 문 여는가' 판정.

네이버 지도에서 [영업중 · 12시]로 필터를 거는 것과 같은 판정을 코드로 한다.
평일 12:00~13:00 사이에 실제로 열려 있어야 점심 후보로 본다.
브레이크타임이 점심을 물면 (예: 11:00~11:30 영업 후 브레이크) 제외한다.

네이버가 주는 영업시간 형식이 한 가지가 아니라서 파서는 관대하게 만들었다.
확신이 없으면 None('모름')을 돌려주고, 모름은 후보에서 빼지 않는다.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

LUNCH_START = 12 * 60        # 12:00
LUNCH_END = 13 * 60          # 13:00
WEEKDAYS = ("월", "화", "수", "목", "금")
WEEKDAY_ALIASES = {
    "mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금",
    "sat": "토", "sun": "일", "monday": "월", "tuesday": "화", "wednesday": "수",
    "thursday": "목", "friday": "금", "saturday": "토", "sunday": "일",
}
_EVERYDAY = ("매일", "연중무휴", "everyday", "daily")
_CLOSED = ("휴무", "정기휴무", "휴무일", "closed", "휴점")
_TIME = re.compile(r"(\d{1,2})\s*[:시]\s*(\d{0,2})")
_RANGE = re.compile(r"(\d{1,2}\s*[:시]\s*\d{0,2})\s*[-~–—]\s*(\d{1,2}\s*[:시]\s*\d{0,2})")


def to_minutes(value: Any) -> int | None:
    """'11:30', '1130', '11시 30분', 11.5 -> 자정 이후 분. 못 읽으면 None."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minutes = int(value)
        return minutes if 0 <= minutes <= 24 * 60 else None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{3,4}", text):          # '1130'
        hour, minute = int(text[:-2]), int(text[-2:])
    else:
        match = _TIME.search(text)
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
    if not (0 <= hour <= 30 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def _overlaps_lunch(start: int, end: int) -> bool:
    """[start, end) 구간이 12:00~13:00 을 조금이라도 걸치는가. 자정 넘김 처리."""
    if end <= start:                             # 예: 17:00 ~ 02:00
        return _overlaps_lunch(start, 24 * 60) or _overlaps_lunch(0, end)
    return start < LUNCH_END and end > LUNCH_START


def covers_lunch(start: Any, end: Any, breaks: Iterable[tuple[Any, Any]] = ()) -> bool | None:
    """이 영업구간이 점심시간을 커버하는가. 시간을 못 읽으면 None."""
    open_at, close_at = to_minutes(start), to_minutes(end)
    if open_at is None or close_at is None:
        return None
    if not _overlaps_lunch(open_at, close_at):
        return False
    # 브레이크타임이 점심시간을 통째로 덮으면 점심 장사를 안 하는 것으로 본다.
    for raw_start, raw_end in breaks:
        b_start, b_end = to_minutes(raw_start), to_minutes(raw_end)
        if b_start is None or b_end is None:
            continue
        if b_start <= LUNCH_START and b_end >= LUNCH_END:
            return False
    return True


def _extract_breaks(entry: dict) -> list[tuple[Any, Any]]:
    for key in ("breakHours", "breakTimes", "breakHour", "breaks"):
        raw = entry.get(key)
        if not raw:
            continue
        if isinstance(raw, str):
            match = _RANGE.search(raw)
            if match:
                return [(match.group(1), match.group(2))]
            continue
        out = []
        for item in raw if isinstance(raw, list) else [raw]:
            if isinstance(item, dict):
                out.append((
                    item.get("start") or item.get("startTime") or item.get("from"),
                    item.get("end") or item.get("endTime") or item.get("to"),
                ))
        if out:
            return out
    return []


def _normalize_day(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(k in text for k in _EVERYDAY):
        return "매일"
    for alias, day in WEEKDAY_ALIASES.items():
        if text.startswith(alias):
            return day
    for day in ("월", "화", "수", "목", "금", "토", "일"):
        if day in text:
            return day
    return None


def _parse_entry(entry: dict) -> tuple[list[str], bool | None]:
    """영업시간 한 줄 -> (해당 요일들, 점심 영업 여부)."""
    day = _normalize_day(entry.get("day") or entry.get("dayOfWeek") or entry.get("name"))
    days = list(WEEKDAYS) if day == "매일" else ([day] if day else [])

    blob = " ".join(str(v) for v in entry.values() if isinstance(v, str))
    if any(k in blob for k in _CLOSED) and not _RANGE.search(blob):
        return days, False

    start = entry.get("startTime") or entry.get("start") or entry.get("open") or entry.get("from")
    end = entry.get("endTime") or entry.get("end") or entry.get("close") or entry.get("to")
    if start is None or end is None:
        match = _RANGE.search(blob)
        if not match:
            return days, None
        start, end = match.group(1), match.group(2)
    return days, covers_lunch(start, end, _extract_breaks(entry))


def _parse_text(text: str) -> bool | None:
    """'매일 11:00 - 21:00 / 브레이크타임 15:00 - 17:00' 같은 자유 문장."""
    if not text:
        return None
    lunch_verdicts = []
    for line in re.split(r"[\n·|]+", text):
        if not line.strip():
            continue
        if any(k in line for k in _CLOSED) and not _RANGE.search(line):
            continue
        ranges = _RANGE.findall(line)
        if not ranges:
            continue
        is_break = "브레이크" in line or "break" in line.lower()
        for start, end in ranges:
            if is_break:
                if covers_lunch("00:00", "23:59", [(start, end)]) is False:
                    return False
            else:
                lunch_verdicts.append(covers_lunch(start, end))
    known = [v for v in lunch_verdicts if v is not None]
    return any(known) if known else None


def parse_business_hours(payload: Any) -> dict[str, Any] | None:
    """영업시간 데이터 -> {'lunch_open': bool, 'text': str}. 못 읽으면 None.

    평일(월~금) 중 절반 이상 점심에 열려 있으면 점심 영업으로 본다.
    (수요일 하루 휴무 같은 경우까지 제외하지 않기 위해서)
    """
    if payload is None:
        return None
    if isinstance(payload, str):
        verdict = _parse_text(payload)
        return None if verdict is None else {"lunch_open": verdict, "text": payload.strip()[:300]}

    entries: list[dict] = []
    if isinstance(payload, dict):
        for key in ("businessHours", "newBusinessHours", "hours", "dayOfWeeks", "detail"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = [e for e in value if isinstance(e, dict)]
                break
        else:
            entries = [payload]
    elif isinstance(payload, list):
        entries = [e for e in payload if isinstance(e, dict)]

    if not entries:
        return None

    by_day: dict[str, bool] = {}
    unscoped: list[bool] = []
    for entry in entries:
        days, verdict = _parse_entry(entry)
        if verdict is None:
            continue
        if days:
            for day in days:
                by_day[day] = verdict
        else:
            unscoped.append(verdict)

    weekday_verdicts = [by_day[d] for d in WEEKDAYS if d in by_day]
    if weekday_verdicts:
        open_days = sum(1 for v in weekday_verdicts if v)
        lunch_open = open_days * 2 >= len(weekday_verdicts)
    elif unscoped:
        lunch_open = any(unscoped)
    elif by_day:
        lunch_open = any(by_day.values())
    else:
        return None

    return {"lunch_open": lunch_open, "text": summarize(entries)[:300]}


def summarize(entries: list[dict]) -> str:
    """화면에 보여줄 영업시간 한 줄."""
    parts = []
    for entry in entries[:7]:
        day = _normalize_day(entry.get("day") or entry.get("dayOfWeek") or entry.get("name")) or ""
        start = entry.get("startTime") or entry.get("start") or ""
        end = entry.get("endTime") or entry.get("end") or ""
        if start and end:
            parts.append(f"{day} {start}-{end}".strip())
    return " · ".join(parts)


# ── 구글 Places 영업시간 ────────────────────────────────────────────
# 구글은 브레이크타임을 별도 필드로 주지 않고, 하루를 여러 구간으로 쪼개서 준다.
# (11:00~15:00 영업 후 17:00~21:00 재개 = 같은 요일에 구간 2개)
# 그래서 "그 요일의 구간 중 하나라도 점심을 덮으면 점심 영업"으로 보면 된다.
GOOGLE_DAYS = {0: "일", 1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토"}


def _google_minutes(point: dict) -> int | None:
    if not isinstance(point, dict) or "hour" not in point:
        return None
    hour, minute = point.get("hour"), point.get("minute") or 0
    if not isinstance(hour, int) or not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


def from_google_periods(periods: Any, descriptions: Any = None) -> dict[str, Any] | None:
    """구글 regularOpeningHours.periods -> {'lunch_open': bool, 'text': str}."""
    if not isinstance(periods, list) or not periods:
        return None

    by_day: dict[str, bool] = {}
    spans: dict[str, list[str]] = {}
    for period in periods:
        if not isinstance(period, dict):
            continue
        opening = period.get("open")
        day = GOOGLE_DAYS.get((opening or {}).get("day")) if isinstance(opening, dict) else None
        start = _google_minutes(opening or {})
        if day is None or start is None:
            continue

        closing = period.get("close")
        if not isinstance(closing, dict):
            # close 가 없으면 24시간 영업
            by_day[day] = True
            spans.setdefault(day, []).append("24시간")
            continue
        end = _google_minutes(closing)
        if end is None:
            continue
        if closing.get("day") != opening.get("day"):
            end += 24 * 60                      # 자정을 넘겨 닫는 경우
        covers = _overlaps_lunch(start, end)
        by_day[day] = by_day.get(day, False) or covers
        spans.setdefault(day, []).append(
            f"{start // 60:02d}:{start % 60:02d}-{end // 60 % 24:02d}:{end % 60:02d}"
        )

    if not by_day:
        return None

    weekday_verdicts = [by_day[d] for d in WEEKDAYS if d in by_day]
    if weekday_verdicts:
        lunch_open = sum(1 for v in weekday_verdicts if v) * 2 >= len(weekday_verdicts)
    else:
        lunch_open = any(by_day.values())

    if isinstance(descriptions, list) and descriptions:
        text = " · ".join(str(d) for d in descriptions[:7])
    else:
        order = list(WEEKDAYS) + ["토", "일"]
        text = " · ".join(f"{d} {'/'.join(spans[d])}" for d in order if d in spans)
    return {"lunch_open": lunch_open, "text": text[:300]}
