import random
import tempfile
import unittest
from pathlib import Path

from app import db as dbm
from app import service
from app.categories import classify
from app.geo import haversine_m

OFFICE = (37.4979, 127.0276)

FIXTURES = [
    ("가돈가스", "음식점>일식>돈까스", 37.4985, 127.0280),
    ("나돈까스", "음식점>일식>돈까스", 37.4975, 127.0270),
    ("다국밥", "음식점>한식>국밥", 37.4990, 127.0290),
    ("라순대국밥", "음식점>한식", 37.4970, 127.0260),
    ("마짬뽕", "음식점>중식", 37.4995, 127.0300),
    ("바피자", "음식점>양식>피자", 37.4960, 127.0250),
    ("사쌀국수", "음식점>아시아음식>베트남음식", 37.4999, 127.0310),
    ("아멀리있는집", "음식점>한식>백반,가정식", 37.5200, 127.0600),  # 반경 밖
]


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(Path(self.tmp.name) / "t.db")
        dbm.init_db(self.conn)
        for name, cat, lat, lng in FIXTURES:
            major, detail = classify(cat, name)
            dbm.upsert_place(self.conn, {
                "id": f"t:{name}", "name": name, "road_address": "주소", "address": "주소",
                "lat": lat, "lng": lng, "raw_category": cat,
                "major_category": major, "detail_category": detail,
                "phone": "", "link": "", "naver_place_id": None,
                "distance_m": round(haversine_m(*OFFICE, lat, lng), 1), "source": "test",
            })
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_radius_filter_excludes_far_places(self):
        names = {c["name"] for c in service.load_candidates(self.conn, 500)}
        self.assertNotIn("아멀리있는집", names)
        self.assertIn("가돈가스", names)

    def test_recommendation_has_no_duplicate_details(self):
        for seed in range(80):
            result = service.make_recommendation(
                self.conn, radius_m=500, count=3, record=False, rng=random.Random(seed)
            )
            details = [i["detail_category"] for i in result["items"]]
            self.assertEqual(len(details), len(set(details)), details)

    def test_recording_writes_history(self):
        result = service.make_recommendation(self.conn, radius_m=500, count=3, record=True)
        rows = self.conn.execute("SELECT COUNT(*) c FROM recommendations").fetchone()["c"]
        self.assertEqual(rows, len(result["items"]))
        self.assertEqual(len(service.history(self.conn)), 1)

    def test_blocked_place_never_recommended(self):
        service.block_place(self.conn, "t:다국밥", reason="문 닫음", by="현민")
        for seed in range(60):
            result = service.make_recommendation(
                self.conn, radius_m=500, count=3, record=False, rng=random.Random(seed)
            )
            self.assertNotIn("다국밥", [i["name"] for i in result["items"]])
        self.assertEqual(len(service.list_blocks(self.conn)), 1)

        service.unblock_place(self.conn, "t:다국밥")
        self.assertEqual(service.list_blocks(self.conn), [])

    def test_rating_upsert_is_per_person(self):
        service.upsert_rating(self.conn, "t:마짬뽕", "현민", 5, "괜찮음")
        summary = service.upsert_rating(self.conn, "t:마짬뽕", "지수", 3)
        self.assertEqual(summary["team_count"], 2)
        self.assertEqual(summary["team_avg"], 4.0)

        # 같은 사람이 다시 매기면 덮어쓴다
        summary = service.upsert_rating(self.conn, "t:마짬뽕", "현민", 1)
        self.assertEqual(summary["team_count"], 2)
        self.assertEqual(summary["team_avg"], 2.0)

    def test_rating_validates_input(self):
        with self.assertRaises(ValueError):
            service.upsert_rating(self.conn, "t:마짬뽕", "현민", 9)
        with self.assertRaises(KeyError):
            service.upsert_rating(self.conn, "t:없는집", "현민", 3)

    def test_pool_note_when_variety_runs_out(self):
        # 돈가스 두 곳만 남기고 전부 제외
        keep = {"t:가돈가스", "t:나돈까스"}
        for name, *_ in FIXTURES:
            pid = f"t:{name}"
            if pid not in keep:
                service.block_place(self.conn, pid)
        result = service.make_recommendation(self.conn, radius_m=500, count=3, record=False)
        self.assertEqual(len(result["items"]), 1)
        self.assertIn("겹치지 않게", result["note"])

    def test_manual_place_and_map_url(self):
        pid = service.add_manual_place(
            self.conn, "우리단골", OFFICE, address="서울 강남구", raw_category="음식점>한식>백반"
        )
        row = dict(self.conn.execute("SELECT * FROM places WHERE id = ?", (pid,)).fetchone())
        self.assertEqual(row["detail_category"], "백반")
        self.assertIn("map.naver.com", service.naver_map_url(row))

    def test_stats(self):
        s = service.stats(self.conn, 500)
        self.assertEqual(s["places_total"], len(FIXTURES))
        self.assertEqual(s["places_in_radius"], len(FIXTURES) - 1)


if __name__ == "__main__":
    unittest.main()


class LunchFilterTestCase(ServiceTestCase):
    def test_no_lunch_places_are_excluded(self):
        service.set_lunch_open(self.conn, "t:마짬뽕", False)
        names = {c["name"] for c in service.load_candidates(self.conn, 500)}
        self.assertNotIn("마짬뽕", names)

        # 되돌리면 다시 후보로 돌아온다
        service.set_lunch_open(self.conn, "t:마짬뽕", True)
        self.assertIn("마짬뽕", {c["name"] for c in service.load_candidates(self.conn, 500)})

    def test_unknown_lunch_hours_are_kept(self):
        row = self.conn.execute("SELECT lunch_open FROM places WHERE id='t:마짬뽕'").fetchone()
        self.assertIsNone(row["lunch_open"])
        self.assertIn("마짬뽕", {c["name"] for c in service.load_candidates(self.conn, 500)})

    def test_set_lunch_open_validates_place(self):
        with self.assertRaises(KeyError):
            service.set_lunch_open(self.conn, "t:없는집", False)

    def test_stats_counts_no_lunch(self):
        service.set_lunch_open(self.conn, "t:마짬뽕", False)
        self.assertEqual(service.stats(self.conn, 500)["no_lunch"], 1)


class MapUrlTestCase(ServiceTestCase):
    def test_place_id_goes_straight_to_detail_page(self):
        url = service.naver_map_url({"name": "가게", "naver_place_id": "1234567890"})
        self.assertEqual(url, "https://map.naver.com/p/entry/place/1234567890")

    def test_search_url_is_encoded(self):
        url = service.naver_map_url({"name": "한터 돼지국밥", "road_address": "서울 강남구"})
        self.assertTrue(url.startswith("https://map.naver.com/p/search/"))
        self.assertNotIn(" ", url)

    def test_app_scheme_for_mobile(self):
        self.assertTrue(
            service.naver_app_url({"name": "가게", "naver_place_id": "77"}).startswith("nmap://place?id=77")
        )
        self.assertTrue(service.naver_app_url({"name": "가게"}).startswith("nmap://search?"))

    def test_directions_url_uses_lng_lat_order(self):
        url = service.naver_directions_url(
            {"name": "국밥집", "lat": 37.4985, "lng": 127.028}, (37.4979, 127.0276), "우리 회사"
        )
        self.assertIn("/directions/127.0276,37.4979,", url)
        self.assertIn("/127.028,37.4985,", url)
        self.assertTrue(url.endswith("/-/walk"))

    def test_directions_url_needs_coordinates(self):
        self.assertIsNone(service.naver_directions_url({"name": "가게"}, (37.5, 127.0)))
        self.assertIsNone(service.naver_directions_url({"name": "가게", "lat": 37.5, "lng": 127.0}, None))

    def test_serialize_exposes_all_links(self):
        row = dict(self.conn.execute("SELECT * FROM places WHERE id='t:다국밥'").fetchone())
        item = service.serialize_place(row, (37.4979, 127.0276), "우리 회사")
        self.assertTrue(item["map_url"].startswith("https://map.naver.com"))
        self.assertTrue(item["app_url"].startswith("nmap://"))
        self.assertTrue(item["directions_url"].startswith("https://map.naver.com/p/directions/"))
