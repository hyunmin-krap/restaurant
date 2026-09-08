import unittest

from app.geo import haversine_m, parse_naver_coords, tm128_to_wgs84, walking_minutes


class TestGeo(unittest.TestCase):
    def test_haversine_known_distance(self):
        # 강남역 <-> 역삼역, 실제 약 840m
        d = haversine_m(37.4979, 127.0276, 37.5006, 127.0365)
        self.assertAlmostEqual(d, 840, delta=30)

    def test_haversine_zero(self):
        self.assertEqual(haversine_m(37.5, 127.0, 37.5, 127.0), 0.0)

    def test_parse_wgs84_scaled(self):
        lat, lng = parse_naver_coords("1270276620", "374979310")
        self.assertAlmostEqual(lat, 37.497931, places=5)
        self.assertAlmostEqual(lng, 127.027662, places=5)

    def test_parse_katech_falls_in_korea(self):
        lat, lng = parse_naver_coords("310507", "551551")
        self.assertTrue(33 < lat < 39, lat)
        self.assertTrue(124 < lng < 132, lng)

    def test_parse_rejects_garbage(self):
        self.assertIsNone(parse_naver_coords("", ""))
        self.assertIsNone(parse_naver_coords("0", "0"))
        self.assertIsNone(parse_naver_coords("abc", "def"))

    def test_parse_rejects_out_of_korea(self):
        # 도쿄 좌표는 반경 밖으로 걸러진다
        self.assertIsNone(parse_naver_coords("1397710000", "356800000"))

    def test_tm128_roundtrip_plausible(self):
        lat, lng = tm128_to_wgs84(310507, 551551)
        self.assertTrue(37.0 < lat < 38.0)
        self.assertTrue(126.0 < lng < 128.0)

    def test_walking_minutes(self):
        self.assertEqual(walking_minutes(0), 1)
        self.assertEqual(walking_minutes(750), 10)


if __name__ == "__main__":
    unittest.main()
