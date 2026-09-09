"""구글 Places - 영업시간 전용 보조 소스."""
import unittest

from app.hours import from_google_periods
from app.providers import ProviderError
from app.providers.google_places import GooglePlacesProvider

MON_TO_FRI = (1, 2, 3, 4, 5)


def periods(*specs):
    """(요일, 여는시각, 닫는시각, 닫는요일) 목록을 구글 형식으로."""
    out = []
    for day, oh, ch, close_day in specs:
        out.append({
            "open": {"day": day, "hour": oh, "minute": 0},
            "close": {"day": close_day if close_day is not None else day, "hour": ch, "minute": 0},
        })
    return out


class TestGooglePeriods(unittest.TestCase):
    def test_lunch_and_dinner_split_into_two_periods(self):
        # 구글은 브레이크타임을 같은 요일 구간 2개로 준다
        data = periods(*[(d, 11, 15, None) for d in MON_TO_FRI],
                       *[(d, 17, 21, None) for d in MON_TO_FRI])
        got = from_google_periods(data)
        self.assertTrue(got["lunch_open"])
        self.assertIn("11:00-15:00/17:00-21:00", got["text"])

    def test_dinner_only(self):
        self.assertFalse(
            from_google_periods(periods(*[(d, 17, 23, None) for d in MON_TO_FRI]))["lunch_open"]
        )

    def test_closing_after_midnight(self):
        data = periods(*[(d, 17, 2, d + 1) for d in MON_TO_FRI])
        self.assertFalse(from_google_periods(data)["lunch_open"])

    def test_open_through_lunch_into_next_day(self):
        data = periods(*[(d, 10, 1, d + 1) for d in MON_TO_FRI])
        self.assertTrue(from_google_periods(data)["lunch_open"])

    def test_break_swallowing_lunch(self):
        # 09:00~11:30 그리고 16:00~21:00 => 점심시간엔 닫혀 있다
        data = periods(*[(d, 9, 11, None) for d in MON_TO_FRI],
                       *[(d, 16, 21, None) for d in MON_TO_FRI])
        self.assertFalse(from_google_periods(data)["lunch_open"])

    def test_twenty_four_hours_has_no_close(self):
        got = from_google_periods([{"open": {"day": 1, "hour": 0, "minute": 0}}])
        self.assertTrue(got["lunch_open"])
        self.assertIn("24시간", got["text"])

    def test_weekend_only_entries_still_decide(self):
        got = from_google_periods(periods((0, 11, 20, None), (6, 11, 20, None)))
        self.assertTrue(got["lunch_open"])

    def test_prefers_google_weekday_descriptions_for_text(self):
        got = from_google_periods(
            periods(*[(d, 11, 21, None) for d in MON_TO_FRI]),
            ["월요일: 오전 11:00 ~ 오후 9:00", "화요일: 오전 11:00 ~ 오후 9:00"],
        )
        self.assertIn("월요일", got["text"])

    def test_garbage_is_none(self):
        for payload in (None, [], {}, "매일", [{"open": {}}], [{"close": {"day": 1, "hour": 9}}]):
            with self.subTest(payload=payload):
                self.assertIsNone(from_google_periods(payload))


class TestProvider(unittest.TestCase):
    def setUp(self):
        self.provider = GooglePlacesProvider("test-key")

    def test_requires_api_key(self):
        with self.assertRaises(ProviderError):
            GooglePlacesProvider("")

    def test_parse_hours_from_place_payload(self):
        place = {"regularOpeningHours": {"periods": periods(*[(d, 17, 23, None) for d in MON_TO_FRI])}}
        self.assertFalse(GooglePlacesProvider.parse_hours(place)["lunch_open"])

    def test_parse_hours_without_hours_field(self):
        self.assertIsNone(GooglePlacesProvider.parse_hours({"id": "abc"}))

    def test_matches_by_exact_name(self):
        candidate = {"displayName": {"text": "마포 돼지국밥"}, "location": {"latitude": 0, "longitude": 0}}
        self.assertTrue(self.provider._same_place(candidate, "마포돼지국밥", 37.5, 127.0))

    def test_matches_by_nearby_coordinates(self):
        candidate = {"displayName": {"text": "다른 이름"},
                     "location": {"latitude": 37.5431, "longitude": 126.9511}}
        self.assertTrue(self.provider._same_place(candidate, "우리집", 37.5432, 126.9512))

    def test_rejects_far_away_same_name_branch(self):
        candidate = {"displayName": {"text": "다른 이름"},
                     "location": {"latitude": 37.4979, "longitude": 127.0276}}
        self.assertFalse(self.provider._same_place(candidate, "우리집", 37.5431, 126.9511))

    def test_rejects_when_coordinates_unknown(self):
        candidate = {"displayName": {"text": "다른 이름"}, "location": {}}
        self.assertFalse(self.provider._same_place(candidate, "우리집", None, None))

    def test_empty_query_short_circuits(self):
        self.assertIsNone(self.provider.fetch_hours(name="", address=""))


if __name__ == "__main__":
    unittest.main()


class TestCallBudget(unittest.TestCase):
    """구글 무료 한도(월 1,000건)를 넘지 않도록 앱이 스스로 막는다."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app import db as dbm
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(Path(self.tmp.name) / "b.db")
        dbm.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_starts_empty(self):
        from app import budget
        self.assertEqual(budget.used(self.conn, "google"), 0)
        self.assertEqual(budget.remaining(self.conn, "google", 900), 900)

    def test_consume_accumulates(self):
        from app import budget
        budget.consume(self.conn, "google", 10)
        budget.consume(self.conn, "google", 5)
        self.assertEqual(budget.used(self.conn, "google"), 15)
        self.assertEqual(budget.remaining(self.conn, "google", 900), 885)

    def test_remaining_never_negative(self):
        from app import budget
        budget.consume(self.conn, "google", 5000)
        self.assertEqual(budget.remaining(self.conn, "google", 900), 0)
        self.assertTrue(budget.status(self.conn, "google", 900)["exhausted"])

    def test_counter_is_per_month(self):
        from datetime import datetime, timezone
        from app import budget
        sept = datetime(2026, 9, 15, tzinfo=timezone.utc)
        octo = datetime(2026, 10, 2, tzinfo=timezone.utc)
        budget.consume(self.conn, "google", 700, now=sept)
        self.assertEqual(budget.used(self.conn, "google", now=sept), 700)
        self.assertEqual(budget.used(self.conn, "google", now=octo), 0)   # 달이 바뀌면 초기화

    def test_corrupt_counter_is_treated_as_zero(self):
        from app import budget, db as dbm
        dbm.set_setting(self.conn, budget.month_key("google"), "이상한값")
        self.assertEqual(budget.used(self.conn, "google"), 0)


class FakeGoogle:
    """호출 횟수만 세는 가짜 구글 provider."""

    def __init__(self):
        self.calls = 0

    def fetch_hours(self, name, address="", lat=None, lng=None, google_place_id=None):
        self.calls += 1
        return {"lunch_open": False, "text": "매일 17:00-23:00", "google_place_id": f"g{self.calls}"}


class TestBudgetStopsCalls(unittest.TestCase):
    """한도에 걸리면 구글 호출을 실제로 멈춰야 한다."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from app import db as dbm
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(Path(self.tmp.name) / "e.db")
        dbm.init_db(self.conn)
        for i in range(6):
            dbm.upsert_place(self.conn, {
                "id": f"p{i}", "name": f"가게{i}", "road_address": "주소", "address": "주소",
                "lat": 37.543, "lng": 126.951, "raw_category": "음식점>한식",
                "major_category": "한식", "detail_category": "백반",
                "phone": "", "link": "", "naver_place_id": None,
                "distance_m": 100.0 + i, "source": "test",
            })
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_stops_at_the_limit(self):
        from app import budget
        from app.sync import enrich_places
        google = FakeGoogle()
        enrich_places(self.conn, reviewer=None, google=google, limit=10, google_call_limit=3)
        self.assertEqual(google.calls, 3, "한도를 넘겨 호출했다")
        self.assertEqual(budget.used(self.conn, "google"), 3)

    def test_counts_every_call(self):
        from app import budget
        from app.sync import enrich_places
        google = FakeGoogle()
        enrich_places(self.conn, reviewer=None, google=google, limit=10, google_call_limit=900)
        self.assertEqual(google.calls, 6)
        self.assertEqual(budget.used(self.conn, "google"), 6)

    def test_writes_hours_and_google_id(self):
        from app.sync import enrich_places
        enrich_places(self.conn, reviewer=None, google=FakeGoogle(), limit=2, google_call_limit=900)
        row = self.conn.execute(
            "SELECT lunch_open, lunch_source, business_hours, google_place_id "
            "FROM places WHERE id='p0'").fetchone()
        self.assertEqual(row["lunch_open"], 0)
        self.assertEqual(row["lunch_source"], "google_places")
        self.assertTrue(row["google_place_id"])

    def test_manual_marks_are_not_overwritten(self):
        from app import db as dbm
        from app.sync import enrich_places
        dbm.set_lunch_open(self.conn, "p0", True, "manual")
        self.conn.commit()
        enrich_places(self.conn, reviewer=None, google=FakeGoogle(), limit=10,
                      google_call_limit=900, only_missing=False)
        row = self.conn.execute(
            "SELECT lunch_open, lunch_source FROM places WHERE id='p0'").fetchone()
        self.assertEqual(row["lunch_open"], 1, "사람이 표시한 값을 자동 판정이 덮어썼다")
        self.assertEqual(row["lunch_source"], "manual")
