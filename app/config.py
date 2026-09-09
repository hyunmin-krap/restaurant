"""환경설정 로딩. .env 파일(있으면)과 OS 환경변수를 함께 읽는다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """의존성 없이 .env 를 읽는다. 이미 설정된 OS 환경변수를 덮어쓰지 않는다."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, "") or default)
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    return (os.environ.get(key, "") or str(int(default))).strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class Config:
    office_name: str
    office_lat: float
    office_lng: float
    radius_m: int
    recommend_count: int
    naver_client_id: str
    naver_client_secret: str
    google_maps_api_key: str
    google_monthly_call_limit: int
    naver_monthly_call_limit: int
    naver_daily_call_limit: int
    enable_place_review_scrape: bool
    host: str
    port: int
    db_path: Path

    @property
    def has_naver_keys(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def has_google_key(self) -> bool:
        return bool(self.google_maps_api_key)


def load_config() -> Config:
    db_path = Path(os.environ.get("DB_PATH", "data/lunch.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    return Config(
        office_name=os.environ.get("OFFICE_NAME", "신원빌딩 (공덕역 4번출구)"),
        office_lat=_float("OFFICE_LAT", 37.54306),
        office_lng=_float("OFFICE_LNG", 126.95111),
        radius_m=_int("SEARCH_RADIUS_M", 500),
        recommend_count=_int("RECOMMEND_COUNT", 3),
        naver_client_id=os.environ.get("NAVER_CLIENT_ID", "").strip(),
        naver_client_secret=os.environ.get("NAVER_CLIENT_SECRET", "").strip(),
        google_maps_api_key=os.environ.get("GOOGLE_MAPS_API_KEY", "").strip(),
        google_monthly_call_limit=_int("GOOGLE_MONTHLY_CALL_LIMIT", 900),
        # API HUB 무료 한도는 월 775,000 / 일 25,000 건. 이 앱은 버튼 한 번에 최대 76건
        # 쓰므로 한참 밑이다. 사고로 폭주하는 경우만 막으려고 여유 있게 잡았다.
        naver_monthly_call_limit=_int("NAVER_MONTHLY_CALL_LIMIT", 5000),
        naver_daily_call_limit=_int("NAVER_DAILY_CALL_LIMIT", 1000),
        enable_place_review_scrape=_bool("ENABLE_PLACE_REVIEW_SCRAPE", False),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=_int("PORT", 8000),
        db_path=db_path,
    )


CONFIG = load_config()
