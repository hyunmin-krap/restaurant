import random
import unittest
from datetime import datetime, timedelta, timezone

from app.recommender import (
    distance_factor, novelty_factor, recommend, taste_factor, team_factor,
)


def place(pid, major, detail, **kw):
    base = {
        "id": pid, "name": pid, "major_category": major, "detail_category": detail,
        "distance_m": 300.0, "taste_ratio": None, "review_total": None,
        "team_avg": None, "team_count": 0, "last_recommended_at": None,
    }
    base.update(kw)
    return base


class TestFactors(unittest.TestCase):
    def test_taste_factor_monotonic(self):
        low = taste_factor(0.3, 200)
        high = taste_factor(0.9, 200)
        self.assertLess(low, high)
        self.assertEqual(taste_factor(None, None), 1.0)

    def test_taste_factor_discounts_thin_review_counts(self):
        thin = taste_factor(0.95, 2)
        thick = taste_factor(0.95, 500)
        self.assertLess(thin, thick)

    def test_team_factor(self):
        self.assertEqual(team_factor(None, 0), 1.0)
        self.assertLess(team_factor(1.5, 10), team_factor(4.8, 10))

    def test_novelty_factor_penalises_recent(self):
        self.assertLess(novelty_factor(0.2), novelty_factor(10))
        self.assertGreater(novelty_factor(None), novelty_factor(30))

    def test_distance_factor_prefers_near(self):
        self.assertGreater(distance_factor(50, 500), distance_factor(490, 500))


class TestRecommend(unittest.TestCase):
    def test_never_repeats_detail_category(self):
        candidates = [
            place("a", "일식", "돈가스"), place("b", "일식", "돈가스"),
            place("c", "한식", "국밥"), place("d", "한식", "국밥"),
            place("e", "중식", "짬뽕"), place("f", "양식", "피자"),
        ]
        for seed in range(300):
            picks = recommend(candidates, count=3, rng=random.Random(seed))
            details = [p.place["detail_category"] for p in picks]
            self.assertEqual(len(details), len(set(details)), f"seed={seed} {details}")

    def test_returns_fewer_when_pool_lacks_variety(self):
        candidates = [place(f"p{i}", "일식", "돈가스") for i in range(8)]
        picks = recommend(candidates, count=3, rng=random.Random(1))
        self.assertEqual(len(picks), 1)

    def test_prefers_distinct_major_when_possible(self):
        candidates = [
            place("a", "한식", "국밥"), place("b", "한식", "냉면"),
            place("c", "중식", "짬뽕"), place("d", "일식", "초밥"),
        ]
        for seed in range(120):
            picks = recommend(candidates, count=3, rng=random.Random(seed))
            majors = [p.place["major_category"] for p in picks]
            self.assertEqual(len(majors), len(set(majors)), f"seed={seed} {majors}")

    def test_recently_recommended_is_rare(self):
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(hours=2)).isoformat()
        candidates = [
            place("fresh", "한식", "국밥"),
            place("stale", "중식", "짬뽕", last_recommended_at=recent),
        ]
        picks_with_stale = 0
        for seed in range(200):
            picks = recommend(candidates, count=1, rng=random.Random(seed))
            if picks and picks[0].place["id"] == "stale":
                picks_with_stale += 1
        self.assertLess(picks_with_stale, 40, picks_with_stale)

    def test_high_rating_wins_more_often(self):
        candidates = [
            place("good", "한식", "국밥", team_avg=4.9, team_count=12,
                  taste_ratio=0.92, review_total=300),
            place("bad", "중식", "짬뽕", team_avg=1.4, team_count=12,
                  taste_ratio=0.30, review_total=300),
        ]
        good = sum(
            1 for seed in range(300)
            if recommend(candidates, count=1, rng=random.Random(seed))[0].place["id"] == "good"
        )
        self.assertGreater(good, 200, good)

    def test_empty_inputs(self):
        self.assertEqual(recommend([], count=3), [])
        self.assertEqual(recommend([place("a", "한식", "국밥")], count=0), [])


if __name__ == "__main__":
    unittest.main()


class TestExplain(unittest.TestCase):
    def test_explain_mentions_high_taste_only_when_high(self):
        from app.recommender import Pick, explain
        low = explain(Pick(place(
            "a", "치킨", "치킨", taste_ratio=0.67, review_total=305, distance_m=400), 1.0, {}))
        high = explain(Pick(place(
            "b", "양식", "피자", taste_ratio=0.85, review_total=142, distance_m=400), 1.0, {}))
        self.assertNotIn("맛있어요", low)
        self.assertIn("맛있어요 85%", high)

    def test_explain_ignores_taste_from_thin_review_counts(self):
        from app.recommender import Pick, explain
        text = explain(Pick(place("c", "한식", "국밥", taste_ratio=0.99, review_total=3), 1.0, {}))
        self.assertNotIn("맛있어요", text)

    def test_explain_falls_back(self):
        from app.recommender import Pick, explain
        self.assertEqual(explain(Pick(place("d", "한식", "국밥", distance_m=400), 1.0, {})), "오늘의 랜덤")
