import unittest

from app.categories import classify, dedupe_key, is_restaurant


class TestCategories(unittest.TestCase):
    def test_donkatsu_spellings_collapse(self):
        a = classify("음식점>일식>돈까스", "명동돈까스")
        b = classify("음식점>일식", "카츠바이콘반")
        self.assertEqual(a[1], "돈가스")
        self.assertEqual(dedupe_key(*a), dedupe_key(*b))

    def test_gukbap_variants_collapse(self):
        a = classify("음식점>한식>국밥", "부산돼지국밥")
        b = classify("음식점>한식", "장수순대국밥")
        self.assertEqual(dedupe_key(*a), "국밥")
        self.assertEqual(dedupe_key(*a), dedupe_key(*b))

    def test_distinct_foods_do_not_collapse(self):
        a = classify("음식점>한식>국밥", "돼지국밥")
        b = classify("음식점>한식>냉면", "평양면옥")
        self.assertNotEqual(dedupe_key(*a), dedupe_key(*b))

    def test_major_categories(self):
        cases = [
            ("음식점>중식", "홍콩반점", "중식"),
            ("음식점>일식>초밥,롤", "스시조", "일식"),
            ("음식점>양식>피자", "피자집", "양식"),
            ("음식점>아시아음식>베트남음식", "포메인", "아시아"),
            ("음식점>분식", "청년다방", "분식"),
        ]
        for cat, name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(classify(cat, name)[0], expected)

    def test_cafe_and_bar_excluded(self):
        self.assertFalse(is_restaurant("음식점>카페,디저트>커피전문점"))
        self.assertFalse(is_restaurant("음식점>술집>이자카야"))
        self.assertTrue(is_restaurant("음식점>한식>육류,고기"))

    def test_detail_never_empty(self):
        major, detail = classify("음식점", "이름없는집")
        self.assertTrue(dedupe_key(major, detail))


if __name__ == "__main__":
    unittest.main()
