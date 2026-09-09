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
