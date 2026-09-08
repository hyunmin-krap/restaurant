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
