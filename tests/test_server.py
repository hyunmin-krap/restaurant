import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import db as dbm
from app.categories import classify
from app.config import CONFIG
from app.server import AppState, LunchHandler

FIXTURES = [
    ("가돈가스", "음식점>일식>돈까스", 120.0),
    ("나국밥", "음식점>한식>국밥", 200.0),
    ("다짬뽕", "음식점>중식", 330.0),
    ("라피자", "음식점>양식>피자", 410.0),
]


def request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        config = replace(CONFIG, db_path=Path(cls.tmp.name) / "t.db", host="127.0.0.1", port=0)
        cls.state = AppState(config)
        for name, cat, dist in FIXTURES:
            major, detail = classify(cat, name)
            dbm.upsert_place(cls.state.conn, {
                "id": f"t:{name}", "name": name, "road_address": "주소", "address": "주소",
                "lat": 37.5, "lng": 127.03, "raw_category": cat,
                "major_category": major, "detail_category": detail,
                "phone": "", "link": "", "naver_place_id": None,
                "distance_m": dist, "source": "test",
            })
        cls.state.conn.commit()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(LunchHandler, state=cls.state))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.state.conn.close()
        cls.tmp.cleanup()

    def test_index_and_static_assets(self):
        for path in ("/", "/app.js", "/styles.css"):
            with urllib.request.urlopen(f"{self.base}{path}", timeout=5) as resp:
                self.assertEqual(resp.status, 200, path)
                self.assertTrue(resp.read())

    def test_static_path_traversal_blocked(self):
        req = urllib.request.Request(f"{self.base}/../config.py")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_config_roundtrip(self):
        status, cfg = request(f"{self.base}/api/config")
        self.assertEqual(status, 200)
        status, cfg = request(f"{self.base}/api/config", "POST", {"radius_m": 777, "office_name": "테스트사"})
        self.assertEqual(status, 200)
        self.assertEqual(cfg["radius_m"], 777)
        self.assertEqual(cfg["office_name"], "테스트사")

    def test_recommend_returns_distinct_details(self):
        status, data = request(f"{self.base}/api/recommend?count=3&radius=500&record=0")
        self.assertEqual(status, 200)
        details = [i["detail_category"] for i in data["items"]]
        self.assertEqual(len(details), len(set(details)))
        self.assertTrue(all(i["map_url"].startswith("https://map.naver.com") for i in data["items"]))

    def test_rating_flow(self):
        status, summary = request(
            f"{self.base}/api/ratings", "POST",
            {"place_id": "t:가돈가스", "rater": "현민", "stars": 4, "comment": "좋았음"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(summary["team_avg"], 4.0)
        status, fetched = request(f"{self.base}/api/ratings?place_id=" + urllib.parse.quote("t:가돈가스"))
        self.assertEqual(fetched["ratings"][0]["rater"], "현민")

    def test_rating_rejects_bad_stars(self):
        status, data = request(
            f"{self.base}/api/ratings", "POST", {"place_id": "t:가돈가스", "rater": "x", "stars": 7}
        )
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_block_and_unblock(self):
        status, _ = request(f"{self.base}/api/blocks", "POST", {"place_id": "t:라피자", "reason": "닫음"})
        self.assertEqual(status, 200)
        _, blocks = request(f"{self.base}/api/blocks")
        self.assertIn("t:라피자", [b["place_id"] for b in blocks["items"]])
        for _ in range(20):
            _, data = request(f"{self.base}/api/recommend?count=4&record=0")
            self.assertNotIn("라피자", [i["name"] for i in data["items"]])
        status, _ = request(f"{self.base}/api/blocks?place_id=" + urllib.parse.quote("t:라피자"), "DELETE")
        self.assertEqual(status, 200)
        _, blocks = request(f"{self.base}/api/blocks")
        self.assertEqual(blocks["items"], [])

    def test_manual_taste_input(self):
        status, data = request(f"{self.base}/api/taste", "POST", {"place_id": "t:나국밥", "ratio": 87})
        self.assertEqual(status, 200)
        self.assertEqual(data["ratio"], 0.87)
        _, places = request(f"{self.base}/api/places?radius=all")
        row = next(p for p in places["items"] if p["id"] == "t:나국밥")
        self.assertEqual(row["taste_ratio"], 0.87)

    def test_taste_rejects_out_of_range(self):
        status, _ = request(f"{self.base}/api/taste", "POST", {"place_id": "t:나국밥", "ratio": 900})
        self.assertEqual(status, 400)

    def test_sync_requires_keys_or_area(self):
        status, data = request(f"{self.base}/api/sync", "POST", {"area": ""})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_unknown_route(self):
        status, _ = request(f"{self.base}/api/nope")
        self.assertEqual(status, 404)

    def test_history_and_stats(self):
        request(f"{self.base}/api/recommend?count=2")
        _, history = request(f"{self.base}/api/history")
        self.assertGreaterEqual(len(history["items"]), 1)
        _, stats = request(f"{self.base}/api/stats")
        self.assertEqual(stats["places_total"], len(FIXTURES))


if __name__ == "__main__":
    unittest.main()


class ReplaceCardTestCase(ServerTestCase):
    """카드 한 장만 교체할 때: 화면에 있는 분류와 겹치지 않는 다른 곳이 와야 한다."""

    def test_exclude_skips_named_places(self):
        _, data = request(
            f"{self.base}/api/recommend?count=1&record=0&exclude="
            + urllib.parse.quote("t:가돈가스,t:나국밥")
        )
        self.assertTrue(data["items"])
        self.assertNotIn(data["items"][0]["id"], {"t:가돈가스", "t:나국밥"})

    def test_exclude_detail_avoids_same_food(self):
        for _ in range(15):
            _, data = request(
                f"{self.base}/api/recommend?count=1&record=0&exclude_detail="
                + urllib.parse.quote("돈가스,국밥")
            )
            for item in data["items"]:
                self.assertNotIn(item["detail_category"], {"돈가스", "국밥"})

    def test_exclude_everything_returns_empty(self):
        ids = ",".join(p[0] and f"t:{p[0]}" for p in FIXTURES)
        _, data = request(
            f"{self.base}/api/recommend?count=1&record=0&exclude=" + urllib.parse.quote(ids)
        )
        self.assertEqual(data["items"], [])

    def test_larger_counts_are_accepted(self):
        _, data = request(f"{self.base}/api/recommend?count=10&record=0")
        details = [i["detail_category"] for i in data["items"]]
        self.assertEqual(len(details), len(set(details)))
        self.assertLessEqual(len(details), len(FIXTURES))


class ImportPreviewTestCase(ServerTestCase):
    def test_preview_extracts_names_without_saving(self):
        text = "1\n공덕 돈까스\n돈까스\n4.5 (12)\n서울 마포구 만리재로 15\n영업 중\n"
        status, data = request(f"{self.base}/api/import/preview", "POST", {"text": text})
        self.assertEqual(status, 200)
        self.assertEqual(data["names"], ["공덕 돈까스"])
        _, places = request(f"{self.base}/api/places?radius=all")
        self.assertNotIn("공덕 돈까스", [p["name"] for p in places["items"]])

    def test_preview_of_junk_returns_empty(self):
        status, data = request(f"{self.base}/api/import/preview", "POST",
                               {"text": "길찾기\n저장\n영업 중\n한식"})
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 0)

    def test_import_rejects_text_with_no_names(self):
        status, data = request(f"{self.base}/api/import", "POST", {"text": "길찾기\n저장"})
        self.assertEqual(status, 400)
        self.assertIn("상호명", data["error"])

    def test_preview_reads_naver_map_markdown(self):
        pc = "https://pcmap.place.naver.com/restaurant/list?query=x#"
        text = "\n".join([
            f"* [역전회관 마포본점예약톡톡쿠폰한식]({pc})",
            f"[영업 중별점4.41리뷰 4,220]({pc})",
            f"* [투썸플레이스 마포대로점배달카페]({pc})",
            f"* [마키노차야 마포점네이버페이예약쿠폰해산물뷔페]({pc})",
            "[광고](https://help.naver.com/support/alias/NSP/NSP_53.naver)",
        ])
        status, data = request(f"{self.base}/api/import/preview", "POST", {"text": text})
        self.assertEqual(status, 200)
        self.assertEqual(data["names"], ["역전회관 마포본점"])
        self.assertEqual(data["entries"][0]["category"], "한식")
        self.assertEqual(data["dropped_count"], 2)
        reasons = {d["name"]: d["reason"] for d in data["dropped"]}
        self.assertEqual(reasons["투썸플레이스 마포대로점"], "카페·주점")
        self.assertEqual(reasons["마키노차야 마포점"], "비싼 편")

    def test_config_exposes_naver_budget_only_with_keys(self):
        status, cfg = request(f"{self.base}/api/config")
        self.assertEqual(status, 200)
        self.assertIn("naver_budget", cfg)
        # 이 테스트 환경엔 키가 없으므로 사용량도 없다
        self.assertIsNone(cfg["naver_budget"])

    def test_preview_filters_can_be_turned_off(self):
        pc = "https://pcmap.place.naver.com/restaurant/list?query=x#"
        text = f"* [투썸플레이스 마포대로점배달카페]({pc})"
        status, data = request(f"{self.base}/api/import/preview", "POST",
                               {"text": text, "drop_cafe": False})
        self.assertEqual(status, 200)
        self.assertEqual(data["names"], ["투썸플레이스 마포대로점"])
        self.assertEqual(data["dropped_count"], 0)


class NaverKeyFromScreenTestCase(ServerTestCase):
    """API 키를 .env 대신 설정 화면에서 넣을 수 있어야 한다."""

    def tearDown(self):
        for key in ("naver_client_id", "naver_client_secret"):
            self.state.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.state.conn.commit()

    def test_키를_저장하면_has_naver_keys_가_켜진다(self):
        _, before = request(f"{self.base}/api/config")
        self.assertFalse(before["has_naver_keys"])
        status, cfg = request(f"{self.base}/api/config", "POST",
                              {"naver_client_id": "abc", "naver_client_secret": "xyz"})
        self.assertEqual(status, 200)
        self.assertTrue(cfg["has_naver_keys"])

    def test_키_값은_화면으로_돌려주지_않는다(self):
        request(f"{self.base}/api/config", "POST",
                {"naver_client_id": "abc", "naver_client_secret": "xyz"})
        _, cfg = request(f"{self.base}/api/config")
        self.assertNotIn("naver_client_id", cfg)
        self.assertNotIn("naver_client_secret", cfg)
        self.assertNotIn("xyz", json.dumps(cfg))

    def test_앞뒤_공백은_떼고_저장한다(self):
        request(f"{self.base}/api/config", "POST",
                {"naver_client_id": "  abc \n", "naver_client_secret": " xyz "})
        self.assertEqual(self.state.naver_client_id, "abc")
        self.assertEqual(self.state.naver_client_secret, "xyz")

    def test_빈_값으로_저장하면_키가_없는_상태로_돌아간다(self):
        request(f"{self.base}/api/config", "POST",
                {"naver_client_id": "abc", "naver_client_secret": "xyz"})
        status, cfg = request(f"{self.base}/api/config", "POST",
                              {"naver_client_id": "", "naver_client_secret": ""})
        self.assertEqual(status, 200)
        self.assertFalse(cfg["has_naver_keys"])

    def test_키를_넣으면_사용량_표시가_생긴다(self):
        _, before = request(f"{self.base}/api/config")
        self.assertIsNone(before["naver_budget"])
        _, cfg = request(f"{self.base}/api/config", "POST",
                         {"naver_client_id": "abc", "naver_client_secret": "xyz"})
        self.assertIsNotNone(cfg["naver_budget"])
        self.assertEqual(cfg["naver_budget"]["used"], 0)


class LunchOpenResetTestCase(ServerTestCase):
    """'목록에 없는 곳은 점심 안 함' 을 잘못 켰을 때의 탈출구."""

    def tearDown(self):
        self.state.conn.execute(
            "UPDATE places SET lunch_open = NULL WHERE id LIKE 't:%'")
        self.state.conn.commit()

    def test_점심_안_함_표시를_한꺼번에_푼다(self):
        for pid in ("t:가돈가스", "t:나국밥"):
            dbm.set_lunch_open(self.state.conn, pid, False, "manual")
        self.state.conn.commit()
        status, data = request(f"{self.base}/api/lunch-open/reset", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["restored"], 2)
        rows = self.state.conn.execute(
            "SELECT lunch_open FROM places WHERE id IN ('t:가돈가스','t:나국밥')").fetchall()
        self.assertTrue(all(r["lunch_open"] is None for r in rows))

    def test_풀_것이_없으면_0을_돌려준다(self):
        status, data = request(f"{self.base}/api/lunch-open/reset", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(data["restored"], 0)

    def test_점심_영업_표시는_건드리지_않는다(self):
        dbm.set_lunch_open(self.state.conn, "t:다짬뽕", True, "manual")
        dbm.set_lunch_open(self.state.conn, "t:라피자", False, "manual")
        self.state.conn.commit()
        request(f"{self.base}/api/lunch-open/reset", "POST", {})
        row = self.state.conn.execute(
            "SELECT lunch_open FROM places WHERE id = 't:다짬뽕'").fetchone()
        self.assertEqual(row["lunch_open"], 1)

    def test_되돌리면_다시_추천_후보가_된다(self):
        from app import service
        dbm.set_lunch_open(self.state.conn, "t:가돈가스", False, "manual")
        self.state.conn.commit()
        names = [c["name"] for c in service.load_candidates(self.state.conn, 500)]
        self.assertNotIn("가돈가스", names)
        request(f"{self.base}/api/lunch-open/reset", "POST", {})
        names = [c["name"] for c in service.load_candidates(self.state.conn, 500)]
        self.assertIn("가돈가스", names)


class PasteLimitTestCase(ServerTestCase):
    def test_상한을_넘으면_잘린_수를_알려_준다(self):
        from app.paste import MAX_ENTRIES
        pc = "https://pcmap.place.naver.com/restaurant/list?query=x#"
        text = "\n".join(f"* [식당{i}한식]({pc})" for i in range(MAX_ENTRIES + 25))
        status, data = request(f"{self.base}/api/import/preview", "POST", {"text": text})
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], MAX_ENTRIES)
        self.assertEqual(data["truncated"], 25)
        self.assertEqual(data["max_entries"], MAX_ENTRIES)

    def test_상한_안이면_잘리지_않는다(self):
        pc = "https://pcmap.place.naver.com/restaurant/list?query=x#"
        text = "\n".join(f"* [식당{i}한식]({pc})" for i in range(500))
        _, data = request(f"{self.base}/api/import/preview", "POST", {"text": text})
        self.assertEqual(data["count"], 500)
        self.assertEqual(data["truncated"], 0)


class CallLimitSettingTestCase(ServerTestCase):
    """호출 상한은 앱이 스스로 지키는 값이라 화면에서 조절할 수 있어야 한다."""

    def setUp(self):
        request(f"{self.base}/api/config", "POST",
                {"naver_client_id": "abc", "naver_client_secret": "xyz"})

    def tearDown(self):
        for key in ("naver_client_id", "naver_client_secret",
                    "naver_monthly_call_limit", "naver_daily_call_limit"):
            self.state.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self.state.conn.commit()

    def test_상한을_올리면_바로_반영된다(self):
        _, cfg = request(f"{self.base}/api/config", "POST",
                         {"naver_daily_call_limit": 3000})
        self.assertEqual(cfg["naver_daily_call_limit"], 3000)
        self.assertEqual(cfg["naver_budget"]["daily_limit"], 3000)

    def test_상한을_올리면_다_쓴_상태가_풀린다(self):
        from app import budget
        budget.consume(self.state.conn, "naver", 1000)
        _, cfg = request(f"{self.base}/api/config", "POST",
                         {"naver_daily_call_limit": 1000})
        self.assertTrue(cfg["naver_budget"]["exhausted"])
        _, cfg = request(f"{self.base}/api/config", "POST",
                         {"naver_daily_call_limit": 5000})
        self.assertFalse(cfg["naver_budget"]["exhausted"])
        self.assertEqual(cfg["naver_budget"]["remaining"], 4000)
        self.state.conn.execute(
            "DELETE FROM settings WHERE key LIKE 'naver_calls_%'")
        self.state.conn.commit()
