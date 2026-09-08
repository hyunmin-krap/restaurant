"""외부 데이터 소스 공통 부분."""
from __future__ import annotations

import gzip
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

_TAG = re.compile(r"<[^>]+>")


class ProviderError(RuntimeError):
    """외부 소스 호출 실패."""


@dataclass
class Place:
    """수집한 식당 하나."""

    id: str
    name: str
    raw_category: str = ""
    road_address: str = ""
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    phone: str = ""
    link: str = ""
    naver_place_id: str | None = None
    distance_m: float | None = None
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def strip_tags(text: str | None) -> str:
    """네이버 검색 결과의 <b> 하이라이트 태그를 제거한다."""
    if not text:
        return ""
    return _TAG.sub("", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def http_json(
    url: str,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> Any:
    """JSON 응답을 받아온다. 실패하면 ProviderError."""
    req = urllib.request.Request(url, data=data)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "gzip")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise ProviderError(f"HTTP {exc.code} {url} :: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"연결 실패 {url} :: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError(f"JSON 파싱 실패 {url}") from exc
