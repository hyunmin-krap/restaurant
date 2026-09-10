"""표준 라이브러리만 쓰는 작은 HTTP 서버 + JSON API."""
from __future__ import annotations

import json
import mimetypes
import random
import threading
import urllib.parse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import budget as budget_mod
from . import db as dbm
from . import service
from .paste import MAX_ENTRIES, extract_entries
from .config import CONFIG, Config
from .providers import (
    BudgetExhausted,
    GooglePlacesProvider, NaverLocalProvider, NaverPlaceReviewProvider, ProviderError,
)
from .sync import SyncState, enrich_places, import_named_places, run_in_thread, sync_places

STATIC_DIR = Path(__file__).resolve().parent / "static"


class AppState:
    """DB 커넥션과 진행 중인 작업 상태를 들고 있는 컨테이너."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.conn = dbm.connect(config.db_path)
        dbm.init_db(self.conn)
        self.lock = threading.Lock()
        self.sync_state = SyncState()

    # 설정은 DB(settings) 가 .env 보다 우선한다. 화면에서 바꾼 값이 살아남게.
    def setting_int(self, key: str, fallback: int) -> int:
        raw = dbm.get_setting(self.conn, key)
        try:
            return int(raw) if raw is not None else fallback
        except ValueError:
            return fallback

    def setting_str(self, key: str, fallback: str) -> str:
        return dbm.get_setting(self.conn, key) or fallback

    @property
    def radius_m(self) -> int:
        return self.setting_int("radius_m", self.config.radius_m)

    @property
    def recommend_count(self) -> int:
        return self.setting_int("recommend_count", self.config.recommend_count)

    @property
    def office(self) -> tuple[float, float]:
        lat = float(self.setting_str("office_lat", str(self.config.office_lat)))
        lng = float(self.setting_str("office_lng", str(self.config.office_lng)))
        return lat, lng

    @property
    def area_keyword(self) -> str:
        return self.setting_str("area_keyword", "")

    # API 키도 화면에서 넣을 수 있게 한다. .env 를 열지 않아도 되도록.
    # 화면에서 넣은 값이 .env 보다 우선한다.
    @property
    def naver_client_id(self) -> str:
        return self.setting_str("naver_client_id", self.config.naver_client_id)

    @property
    def naver_client_secret(self) -> str:
        return self.setting_str("naver_client_secret", self.config.naver_client_secret)

    @property
    def has_naver_keys(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    # 호출 상한은 앱이 스스로 지키는 값이라 언제든 조절할 수 있어야 한다.
    # (네이버 쪽 무료 한도는 월 775,000 / 일 25,000 건으로 훨씬 넉넉하다)
    @property
    def naver_monthly_call_limit(self) -> int:
        return self.setting_int(
            "naver_monthly_call_limit", self.config.naver_monthly_call_limit)

    @property
    def naver_daily_call_limit(self) -> int:
        return self.setting_int(
            "naver_daily_call_limit", self.config.naver_daily_call_limit)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


Handler = Callable[["LunchHandler", dict[str, list[str]], dict[str, Any]], Any]
ROUTES: dict[tuple[str, str], Handler] = {}


def route(method: str, path: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        ROUTES[(method, path)] = fn
        return fn

    return deco


# ── 엔드포인트 ──────────────────────────────────────────────────────
@route("GET", "/api/config")
def get_config(h: "LunchHandler", q, body):
    s = h.state
    return {
        "office_name": s.setting_str("office_name", s.config.office_name),
        "office_lat": s.office[0],
        "office_lng": s.office[1],
        "radius_m": s.radius_m,
        "recommend_count": s.recommend_count,
        "area_keyword": s.area_keyword,
        "has_naver_keys": s.has_naver_keys,
        # 키 값은 화면으로 돌려주지 않는다. 넣었는지 여부만 알려 준다.
        "naver_keys_from_env": bool(s.config.has_naver_keys),
        "review_scrape_enabled": s.config.enable_place_review_scrape,
        "has_google_key": s.config.has_google_key,
        "google_budget": (budget_mod.status(s.conn, "google", s.config.google_monthly_call_limit)
                          if s.config.has_google_key else None),
        "naver_budget": (budget_mod.status(
            s.conn, "naver",
            s.naver_monthly_call_limit, s.naver_daily_call_limit,
        ) if s.has_naver_keys else None),
        "naver_monthly_call_limit": s.naver_monthly_call_limit,
        "naver_daily_call_limit": s.naver_daily_call_limit,
    }


@route("POST", "/api/config")
def post_config(h: "LunchHandler", q, body):
    allowed = {"office_name", "office_lat", "office_lng", "radius_m",
               "recommend_count", "area_keyword",
               "naver_monthly_call_limit", "naver_daily_call_limit"}
    secrets = {"naver_client_id", "naver_client_secret"}
    for key, value in body.items():
        if key in allowed and value not in (None, ""):
            dbm.set_setting(h.state.conn, key, str(value))
        elif key in secrets and value is not None:
            # 붙여넣을 때 앞뒤 공백이 딸려 오는 일이 잦다.
            # 빈 값으로 저장하면 .env 값으로 되돌아간다.
            dbm.set_setting(h.state.conn, key, str(value).strip())
    return get_config(h, q, body)


@route("GET", "/api/recommend")
def get_recommend(h: "LunchHandler", q, body):
    s = h.state
    count = _int_param(q, "count", s.recommend_count)
    radius = _int_param(q, "radius", s.radius_m)
    record = _int_param(q, "record", 1) == 1
    with s.lock:
        return service.make_recommendation(
            s.conn, radius_m=radius, count=count, record=record, rng=random.Random(),
            office=s.office, office_name=s.setting_str("office_name", s.config.office_name),
            exclude_ids=_csv_param(q, "exclude"),
            exclude_details=_csv_param(q, "exclude_detail"),
            exclude_majors=_csv_param(q, "exclude_major"),
        )


@route("GET", "/api/places")
def get_places(h: "LunchHandler", q, body):
    radius = q.get("radius", [None])[0]
    radius_m = int(radius) if radius not in (None, "", "all") else None
    s = h.state
    return {"items": service.list_places(
        s.conn, radius_m, office=s.office,
        office_name=s.setting_str("office_name", s.config.office_name),
    )}


@route("POST", "/api/places")
def post_place(h: "LunchHandler", q, body):
    try:
        pid = service.add_manual_place(
            h.state.conn,
            name=body.get("name", ""),
            office=h.state.office,
            address=body.get("address", ""),
            raw_category=body.get("category", ""),
            lat=_float_or_none(body.get("lat")),
            lng=_float_or_none(body.get("lng")),
            naver_place_id=(body.get("naver_place_id") or None),
        )
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
    return {"place_id": pid}


@route("POST", "/api/taste")
def post_taste(h: "LunchHandler", q, body):
    """'음식이 맛있어요' 비율을 손으로 입력 (0~100 또는 0~1)."""
    place_id = body.get("place_id", "")
    raw = body.get("ratio")
    if raw in (None, ""):
        dbm.set_taste(h.state.conn, place_id, None, None, None, "manual")
        h.state.conn.commit()
        return {"ok": True, "ratio": None}
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "비율은 숫자여야 합니다.") from exc
    ratio = value / 100.0 if value > 1 else value
    if not 0 <= ratio <= 1:
        raise ApiError(400, "비율은 0~100 사이여야 합니다.")
    total = body.get("total")
    dbm.set_taste(
        h.state.conn, place_id, round(ratio, 4), None,
        int(total) if str(total or "").isdigit() else None, "manual",
    )
    h.state.conn.commit()
    return {"ok": True, "ratio": round(ratio, 4)}


@route("POST", "/api/lunch")
def post_lunch(h: "LunchHandler", q, body):
    """점심 영업 여부 표시. open=false 면 추천 후보에서 빠진다."""
    raw = body.get("open")
    lunch_open = None if raw in (None, "", "unknown") else bool(raw)
    try:
        service.set_lunch_open(h.state.conn, body.get("place_id", ""), lunch_open)
    except KeyError as exc:
        raise ApiError(404, "없는 식당입니다.") from exc
    return {"ok": True, "lunch_open": lunch_open}


@route("POST", "/api/ratings")
def post_rating(h: "LunchHandler", q, body):
    try:
        return service.upsert_rating(
            h.state.conn,
            place_id=body.get("place_id", ""),
            rater=body.get("rater", ""),
            stars=int(body.get("stars", 0)),
            comment=body.get("comment", "") or "",
            visited_on=body.get("visited_on"),
        )
    except KeyError as exc:
        raise ApiError(404, "없는 식당입니다.") from exc
    except (TypeError, ValueError) as exc:
        raise ApiError(400, str(exc)) from exc


@route("GET", "/api/ratings")
def get_ratings(h: "LunchHandler", q, body):
    place_id = q.get("place_id", [""])[0]
    if not place_id:
        raise ApiError(400, "place_id 가 필요합니다.")
    return service.rating_summary(h.state.conn, place_id)


@route("GET", "/api/blocks")
def get_blocks(h: "LunchHandler", q, body):
    return {"items": service.list_blocks(h.state.conn)}


@route("POST", "/api/blocks")
def post_block(h: "LunchHandler", q, body):
    try:
        service.block_place(
            h.state.conn, body.get("place_id", ""),
            reason=body.get("reason", "") or "", by=body.get("by", "") or "",
        )
    except KeyError as exc:
        raise ApiError(404, "없는 식당입니다.") from exc
    return {"ok": True}


@route("DELETE", "/api/blocks")
def delete_block(h: "LunchHandler", q, body):
    place_id = q.get("place_id", [""])[0] or body.get("place_id", "")
    service.unblock_place(h.state.conn, place_id)
    return {"ok": True}


@route("GET", "/api/history")
def get_history(h: "LunchHandler", q, body):
    return {"items": service.history(h.state.conn, limit=_int_param(q, "limit", 20))}


@route("GET", "/api/stats")
def get_stats(h: "LunchHandler", q, body):
    return service.stats(h.state.conn, h.state.radius_m)


@route("GET", "/api/sync")
def get_sync(h: "LunchHandler", q, body):
    return h.state.sync_state.snapshot()


@route("POST", "/api/sync")
def post_sync(h: "LunchHandler", q, body):
    s = h.state
    if s.sync_state.running:
        raise ApiError(409, "이미 수집이 진행 중입니다.")
    area = (body.get("area") or s.area_keyword or "").strip()
    if not area:
        raise ApiError(400, "지역 키워드가 필요합니다. 예: '역삼동', '판교역'")
    dbm.set_setting(s.conn, "area_keyword", area)
    radius = int(body.get("radius") or s.radius_m)
    try:
        provider = _naver_provider(s)
    except ProviderError as exc:
        raise ApiError(400, str(exc)) from exc
    lat, lng = s.office
    s.sync_state = SyncState()
    run_in_thread(
        partial(sync_places, s.conn, provider, lat, lng, area, radius,
                state=s.sync_state, lock=s.lock),
        s.sync_state,
    )
    return {"started": True, "area": area, "radius": radius}


@route("POST", "/api/import")
def post_import(h: "LunchHandler", q, body):
    """네이버 지도 [영업중 · 12시] 필터 목록을 붙여넣어 한 번에 등록."""
    s = h.state
    if s.sync_state.running:
        raise ApiError(409, "이미 작업이 진행 중입니다.")
    keep, _dropped, _truncated = extract_entries(
        body.get("text") or "",
        drop_cafe=body.get("drop_cafe", True),
        drop_pricey=body.get("drop_pricey", True),
    )
    if not keep:
        raise ApiError(400, "상호명을 찾지 못했습니다. 네이버 지도 목록을 그대로 붙여넣어 보세요.")

    # 키가 있으면 좌표·거리까지 채우고, 없으면 상호명·업종만으로 등록한다.
    # (네이버 검색 API 신규 발급이 개발자센터에서 막혀 키가 없는 경우가 많다)
    try:
        provider = _naver_provider(s)
    except ProviderError:
        provider = None

    lat, lng = s.office
    s.sync_state = SyncState()
    run_in_thread(
        partial(import_named_places, s.conn, provider, keep, lat, lng,
                s.area_keyword, bool(body.get("mark_others_no_lunch")), s.radius_m,
                state=s.sync_state, lock=s.lock),
        s.sync_state,
    )
    return {"started": True, "count": len(keep), "with_coords": provider is not None}


@route("POST", "/api/import/preview")
def post_import_preview(h: "LunchHandler", q, body):
    """붙여넣은 덩어리에서 상호명을 뽑아 보여만 준다. 등록하지 않는다.

    카페·주점과 비싼 곳은 걸러 내고, 왜 걸렀는지도 같이 돌려준다.
    """
    keep, dropped, truncated = extract_entries(
        body.get("text") or "",
        drop_cafe=body.get("drop_cafe", True),
        drop_pricey=body.get("drop_pricey", True),
    )
    return {
        "names": [e["name"] for e in keep],
        "entries": keep,
        "dropped": dropped,
        "count": len(keep),
        "dropped_count": len(dropped),
        "truncated": truncated,
        "max_entries": MAX_ENTRIES,
    }


@route("POST", "/api/lunch-open/reset")
def post_lunch_open_reset(h: "LunchHandler", q, body):
    """'점심 안 함' 표시를 한꺼번에 지운다.

    붙여넣기 등록에서 '목록에 없는 곳은 점심 안 함' 을 켠 채 두 번째 묶음을
    올리면 첫 번째 묶음이 통째로 잠긴다. 그때 되돌릴 길이 필요하다.
    식당 자체는 지워지지 않으므로 표시만 풀면 그대로 살아난다.
    """
    s = h.state
    with s.lock:
        cur = s.conn.execute(
            "UPDATE places SET lunch_open = NULL, lunch_source = NULL "
            " WHERE is_active = 1 AND lunch_open = 0"
        )
        s.conn.commit()
    return {"ok": True, "restored": cur.rowcount}


@route("POST", "/api/enrich")
def post_enrich(h: "LunchHandler", q, body):
    s = h.state
    if not s.config.enable_place_review_scrape and not s.config.has_google_key:
        raise ApiError(
            400,
            "채울 소스가 없습니다. .env 에 GOOGLE_MAPS_API_KEY 를 넣으면 영업시간을 공식 API 로 "
            "가져옵니다. '맛있어요' 비율까지 원하면 ENABLE_PLACE_REVIEW_SCRAPE=1 도 켜세요 "
            "(이쪽은 비공식 경로라 언제든 막힐 수 있습니다).",
        )
    if s.sync_state.running:
        raise ApiError(409, "이미 작업이 진행 중입니다.")
    limit = int(body.get("limit") or 50)
    reviewer = NaverPlaceReviewProvider() if s.config.enable_place_review_scrape else None
    google = GooglePlacesProvider(s.config.google_maps_api_key) if s.config.has_google_key else None
    s.sync_state = SyncState()
    run_in_thread(
        partial(enrich_places, s.conn, reviewer, google, limit,
                s.config.google_monthly_call_limit,
                state=s.sync_state, lock=s.lock),
        s.sync_state,
    )
    return {"started": True, "limit": limit,
            "hours_source": "google" if google else "naver",
            "taste": bool(reviewer),
            "google_budget": budget_mod.status(s.conn, "google", s.config.google_monthly_call_limit)}


def _naver_provider(s: "AppState") -> NaverLocalProvider:
    """호출 한도 가드를 물린 네이버 프로바이더.

    가드는 search() 직전마다 불린다. 한도가 남아 있으면 1건을 먼저 적어 두고
    통과시킨다. (실패한 호출도 네이버 쪽에서는 한 번 센 것으로 잡히므로
    성공했을 때만 세면 실제보다 적게 잡힌다.)
    """
    def guard() -> None:
        left = budget_mod.remaining(
            s.conn, "naver", s.naver_monthly_call_limit, s.naver_daily_call_limit,
        )
        if left <= 0:
            raise BudgetExhausted(
                f"이 앱에 걸어 둔 네이버 호출 한도를 다 썼습니다 "
                f"(월 {s.naver_monthly_call_limit:,}건 / 일 "
                f"{s.naver_daily_call_limit:,}건). "
                "설정 탭의 '호출 상한' 을 올리면 바로 다시 쓸 수 있습니다. "
                "네이버 쪽 무료 한도는 월 775,000 / 일 25,000 건이라 훨씬 넉넉합니다."
            )
        with s.lock:
            budget_mod.consume(s.conn, "naver", 1)

    return NaverLocalProvider(s.naver_client_id, s.naver_client_secret, guard=guard)


# ── 유틸 ────────────────────────────────────────────────────────────
def _int_param(q: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(q.get(key, [default])[0])
    except (TypeError, ValueError):
        return default


def _csv_param(q: dict[str, list[str]], key: str) -> set[str]:
    """?exclude=a,b,c -> {'a','b','c'}"""
    raw = q.get(key, [""])[0]
    return {v.strip() for v in raw.split(",") if v.strip()}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class LunchHandler(BaseHTTPRequestHandler):
    server_version = "LunchPicker/1.0"
    state: AppState  # partial 로 주입

    def __init__(self, *args, state: AppState, **kwargs) -> None:
        self.state = state
        super().__init__(*args, **kwargs)

    def log_message(self, fmt: str, *args) -> None:  # 접근 로그를 조용하게
        if self.path.startswith("/api/"):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    # HTTP 메서드 ---------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(parsed.query)

        if not path.startswith("/api"):
            self._serve_static(path)
            return

        handler = ROUTES.get((method, path))
        if handler is None:
            self._json({"error": "없는 경로입니다."}, status=404)
            return
        try:
            body = self._read_json() if method in {"POST", "DELETE"} else {}
            self._json(handler(self, query, body))
        except ApiError as exc:
            self._json({"error": exc.message}, status=exc.status)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "JSON 본문이 올바르지 않습니다.") from exc
        return parsed if isinstance(parsed, dict) else {}

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self.send_error(404, "Not Found")
            return
        ctype, _ = mimetypes.guess_type(str(target))
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def serve(config: Config = CONFIG) -> None:
    state = AppState(config)
    handler = partial(LunchHandler, state=state)
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    shown = "localhost" if config.host in {"0.0.0.0", ""} else config.host
    print(f"🍚 점심 뽑기 서버 시작: http://{shown}:{config.port}")
    print(f"   DB: {config.db_path}")
    if not state.has_naver_keys:
        print("   ℹ 네이버 API 키가 없습니다. 붙여넣기 등록은 그대로 되고 거리만 빕니다.")
        print("     키가 있으면 설정 탭에 붙여넣으세요 (.env 를 안 열어도 됩니다).")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
        state.conn.close()
