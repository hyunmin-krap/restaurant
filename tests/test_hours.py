"""영업시간 -> 점심 영업 판정. 네이버 지도의 [영업중 · 12시] 필터와 같은 판정."""
import unittest

from app.hours import covers_lunch, parse_business_hours, to_minutes
from app.providers import NaverPlaceReviewProvider


def week(start, end, breaks=None, days="월화수목금"):
    return [{"day": d, "startTime": start, "endTime": end,
             "breakHours": breaks or []} for d in days]


class TestToMinutes(unittest.TestCase):
    def test_formats(self):
        for raw in ("11:30", "1130", "11시 30분", "11:30:00"):
            with self.subTest(raw=raw):
                self.assertEqual(to_minutes(raw), 690)

    def test_midnight_and_edges(self):
        self.assertEqual(to_minutes("00:00"), 0)
        self.assertEqual(to_minutes("24:00"), 1440)

    def test_unreadable(self):
        for raw in ("", None, "영업시간 미정", "abc"):
            self.assertIsNone(to_minutes(raw))


class TestCoversLunch(unittest.TestCase):
    def test_lunch_place(self):
        self.assertTrue(covers_lunch("11:00", "21:00"))
        self.assertTrue(covers_lunch("11:30", "14:00"))

    def test_dinner_only(self):
        self.assertFalse(covers_lunch("17:00", "23:00"))
        self.assertFalse(covers_lunch("16:00", "24:00"))

    def test_late_night_wrapping_past_midnight(self):
        self.assertFalse(covers_lunch("18:00", "02:00"))
        self.assertTrue(covers_lunch("10:00", "01:00"))

    def test_break_over_lunch_disqualifies(self):
        self.assertFalse(covers_lunch("11:00", "22:00", [("12:00", "14:00")]))
        self.assertFalse(covers_lunch("09:00", "22:00", [("11:00", "15:00")]))

    def test_afternoon_break_is_fine(self):
        self.assertTrue(covers_lunch("11:00", "22:00", [("15:00", "17:00")]))

    def test_unreadable_returns_none(self):
        self.assertIsNone(covers_lunch("", "21:00"))
        self.assertIsNone(covers_lunch(None, None))


class TestParseBusinessHours(unittest.TestCase):
    def test_weekday_lunch_place(self):
        got = parse_business_hours(week("11:00", "21:00", [{"start": "15:00", "end": "17:00"}]))
        self.assertTrue(got["lunch_open"])
        self.assertIn("11:00-21:00", got["text"])

    def test_dinner_only_place_is_excluded(self):
        self.assertFalse(parse_business_hours(week("17:00", "23:00"))["lunch_open"])

    def test_one_odd_day_does_not_flip_the_verdict(self):
        entries = week("11:00", "21:00", days="화수목금")
        entries.append({"day": "월", "startTime": "17:00", "endTime": "23:00"})
        self.assertTrue(parse_business_hours(entries)["lunch_open"])

    def test_mostly_dinner_is_excluded(self):
        entries = week("17:00", "23:00", days="월화수목")
        entries.append({"day": "금", "startTime": "11:00", "endTime": "21:00"})
        self.assertFalse(parse_business_hours(entries)["lunch_open"])

    def test_closed_days_are_skipped_not_counted(self):
        entries = week("11:00", "21:00", days="월화수목")
        entries.append({"day": "금", "startTime": "", "endTime": "", "description": "정기휴무"})
        self.assertTrue(parse_business_hours(entries)["lunch_open"])

    def test_everyday_entry(self):
        got = parse_business_hours([{"day": "매일", "startTime": "10:30", "endTime": "22:00"}])
        self.assertTrue(got["lunch_open"])

    def test_english_day_names(self):
        entries = [{"day": d, "startTime": "17:00", "endTime": "23:00"}
                   for d in ("mon", "tue", "wed", "thu", "fri")]
        self.assertFalse(parse_business_hours(entries)["lunch_open"])

    def test_free_text(self):
        self.assertTrue(parse_business_hours("매일 11:00 - 21:00")["lunch_open"])
        self.assertFalse(parse_business_hours("매일 17:30 - 24:00")["lunch_open"])
        self.assertFalse(
            parse_business_hours("11:00 - 22:00 · 브레이크타임 11:30 - 16:00")["lunch_open"]
        )

    def test_nested_payload_shape(self):
        got = parse_business_hours({"businessHours": week("11:00", "20:00")})
        self.assertTrue(got["lunch_open"])

    def test_unknown_stays_unknown(self):
        for payload in (None, {}, [], "영업시간 정보 없음", [{"day": "월"}]):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_business_hours(payload))


class TestProviderHourParsing(unittest.TestCase):
    def _wrap(self, restaurant):
        return [{"data": {"restaurant": restaurant}}]

    def test_reads_graphql_response(self):
        got = NaverPlaceReviewProvider.parse_hours(
            self._wrap({"businessHours": week("17:00", "23:00")})
        )
        self.assertFalse(got["lunch_open"])

    def test_falls_back_to_description_text(self):
        got = NaverPlaceReviewProvider.parse_hours(self._wrap(
            {"businessHours": [], "newBusinessHours": [{"description": "매일 11:00 - 21:00"}]}
        ))
        self.assertTrue(got["lunch_open"])

    def test_missing_data_is_none(self):
        for payload in ([{"data": {"restaurant": None}}], [{}], [], {}):
            with self.subTest(payload=payload):
                self.assertIsNone(NaverPlaceReviewProvider.parse_hours(payload))


if __name__ == "__main__":
    unittest.main()
