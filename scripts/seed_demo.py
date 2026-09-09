#!/usr/bin/env python3
"""네이버 키 없이 바로 굴려볼 수 있는 데모 데이터.

    python3 scripts/seed_demo.py

강남역 근처 가상의 식당 24곳을 넣는다. 실제 수집을 하면 그대로 섞이므로
데모만 지우려면: DELETE FROM places WHERE source='demo';
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as dbm  # noqa: E402
from app.categories import classify  # noqa: E402
from app.config import CONFIG  # noqa: E402
from app.geo import haversine_m  # noqa: E402
from app.hours import parse_business_hours  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "demo_places.json"


def main() -> int:
    conn = dbm.connect(CONFIG.db_path)
    dbm.init_db(conn)
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    for row in rows:
        major, detail = classify(row["category"], row["name"])
        distance = haversine_m(CONFIG.office_lat, CONFIG.office_lng, row["lat"], row["lng"])
        dbm.upsert_place(conn, {
            "id": f"demo:{row['name']}", "name": row["name"],
            "road_address": row["address"], "address": row["address"],
            "lat": row["lat"], "lng": row["lng"], "raw_category": row["category"],
            "major_category": major, "detail_category": detail,
            "phone": "", "link": "", "naver_place_id": None,
            "distance_m": round(distance, 1), "source": "demo",
        })
        if row.get("taste") is not None:
            dbm.set_taste(conn, f"demo:{row['name']}", row["taste"], None, row.get("reviews"), "manual")
        # 영업시간에서 점심 영업 여부를 판정한다 (실제 수집 때와 같은 로직)
        parsed = parse_business_hours(row.get("hours"))
        if parsed:
            dbm.set_lunch_open(conn, f"demo:{row['name']}", parsed["lunch_open"],
                               "naver_place", parsed["text"])
    conn.commit()
    count = conn.execute("SELECT COUNT(*) c FROM places WHERE source='demo'").fetchone()["c"]
    no_lunch = conn.execute(
        "SELECT COUNT(*) c FROM places WHERE source='demo' AND lunch_open = 0"
    ).fetchone()["c"]
    print(f"데모 식당 {count}곳을 넣었습니다 "
          f"(영업시간 판정으로 {no_lunch}곳은 '점심 안 함'으로 제외).")
    print("python3 -m app 으로 서버를 켜 보세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
