"""네이버 지도에서 복사한 덩어리 -> 상호명만 추출."""
import unittest

from app.paste import extract_names

NAVER_LIST = """지도 홈
음식점  카페  술집
이 지역 재검색
관련도순

1
공덕 돈까스
돈까스
4.52 (120)
서울 마포구 만리재로 15
영업 중 · 21:00에 영업 종료
길찾기  저장  공유

2
마포 돼지국밥
국밥
★4.31 (402)
서울 마포구 백범로 20
영업 중
전화  길찾기

3
염리동 손칼국수 4.1 (176)
칼국수
02-712-3456
350m
영업 전 · 11:00에 영업 시작
"""


class TestExtractNames(unittest.TestCase):
    def test_pulls_only_shop_names(self):
        self.assertEqual(
            extract_names(NAVER_LIST),
            ["공덕 돈까스", "마포 돼지국밥", "염리동 손칼국수"],
        )

    def test_plain_one_per_line_still_works(self):
        self.assertEqual(
            extract_names("마포 돼지국밥\n공덕 돈가스\n염리동 손칼국수"),
            ["마포 돼지국밥", "공덕 돈가스", "염리동 손칼국수"],
        )

    def test_drops_ui_words(self):
        for line in ("길찾기", "저장", "공유", "리뷰", "지도 홈", "길찾기  저장  공유", "광고"):
            with self.subTest(line=line):
                self.assertEqual(extract_names(line), [])

    def test_drops_category_only_lines(self):
        for line in ("한식", "돈까스", "육류,고기", "카페,디저트", "음식점  카페  술집"):
            with self.subTest(line=line):
                self.assertEqual(extract_names(line), [])

    def test_drops_addresses(self):
        for line in ("서울 마포구 백범로 20", "경기 성남시 분당구 판교역로 4", "마포대로 15"):
            with self.subTest(line=line):
                self.assertEqual(extract_names(line), [])

    def test_drops_ratings_counts_phones_distances(self):
        for line in ("4.52 (120)", "★4.3", "(402)", "02-712-3456", "350m", "1.2km", "11:00"):
            with self.subTest(line=line):
                self.assertEqual(extract_names(line), [])

    def test_drops_business_status(self):
        for line in ("영업 중", "영업 전 · 11:00에 영업 시작", "오늘 휴무", "24시간 영업"):
            with self.subTest(line=line):
                self.assertEqual(extract_names(line), [])

    def test_strips_rating_tail_from_name(self):
        self.assertEqual(extract_names("염리동 손칼국수 4.1 (176)"), ["염리동 손칼국수"])
        self.assertEqual(extract_names("공덕 돈까스 (120)"), ["공덕 돈까스"])
        self.assertEqual(extract_names("1. 마포 돼지국밥"), ["마포 돼지국밥"])

    def test_keeps_names_that_end_in_digits(self):
        # '김밥천국 24' 는 상호명이지 별점이 아니다
        self.assertEqual(extract_names("김밥천국 24"), ["김밥천국 24"])
        self.assertEqual(extract_names("홍콩반점0410"), ["홍콩반점0410"])

    def test_deduplicates_ignoring_spaces(self):
        self.assertEqual(
            extract_names("마포 돼지국밥\n마포돼지국밥\n마포 돼지국밥"), ["마포 돼지국밥"]
        )

    def test_drops_too_short_and_too_long(self):
        self.assertEqual(extract_names("가"), [])
        self.assertEqual(extract_names("가" * 60), [])

    def test_respects_limit(self):
        many = "\n".join(f"가게{i}" for i in range(500))
        self.assertEqual(len(extract_names(many, limit=50)), 50)

    def test_empty_input(self):
        for text in ("", "   \n\n  ", None):
            with self.subTest(text=text):
                self.assertEqual(extract_names(text), [])


if __name__ == "__main__":
    unittest.main()
