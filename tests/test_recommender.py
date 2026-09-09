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


class TestDistanceWeighting(unittest.TestCase):
    """거리는 가까울수록 유리하되, 코앞 몇 집으로 쏠리지 않아야 한다."""

    def test_monotonically_decreasing(self):
        values = [distance_factor(d, 500) for d in range(0, 501, 50)]
        self.assertEqual(values, sorted(values, reverse=True))

    def test_near_far_gap_stays_moderate(self):
        ratio = distance_factor(0, 500) / distance_factor(500, 500)
        # 맛있어요(최대 4배)·별점(최대 4.9배)보다 확실히 작아야 거리가 지배하지 않는다
        self.assertGreater(ratio, 1.4)
        self.assertLess(ratio, 1.8)

    def test_curve_is_flat_near_the_office(self):
        # 가까운 구간(50m 차이)의 낙폭이 먼 구간보다 작아야 코앞 쏠림이 없다
        near_drop = distance_factor(0, 500) - distance_factor(100, 500)
        far_drop = distance_factor(400, 500) - distance_factor(500, 500)
        self.assertLess(near_drop, far_drop / 2)

    def test_never_drops_below_floor(self):
        for d in (500, 1000, 99999):
            self.assertGreaterEqual(distance_factor(d, 500), 0.7)

    def test_missing_distance_is_neutral(self):
        self.assertEqual(distance_factor(None, 500), 1.0)
        self.assertEqual(distance_factor(300, 0), 1.0)

    def test_closer_place_wins_more_often_but_not_overwhelmingly(self):
        near = place("near", "한식", "국밥", distance_m=60)
        far = place("far", "중식", "짬뽕", distance_m=480)
        wins = sum(
            1 for seed in range(600)
            if recommend([near, far], count=1, radius_m=500,
                         rng=random.Random(seed))[0].place["id"] == "near"
        )
        # 60% 안팎이면 '조금 더 자주'. 80% 를 넘으면 쏠린 것.
        self.assertGreater(wins, 330)
        self.assertLess(wins, 480)


class TestPerDrawDedup(unittest.TestCase):
    """중복 금지는 '한 번의 추천 안에서'만. 다시 뽑으면 다른 돈가스가 나와야 한다."""

    def _pool(self):
        return [place(f"{n}돈가스", "일식", "돈가스") for n in "ABCD"] + [
            place("국밥집", "한식", "국밥"), place("피자집", "양식", "피자"),
            place("짬뽕집", "중식", "짬뽕"), place("김밥집", "분식", "김밥"),
        ]

    def test_never_two_of_the_same_food_in_one_draw(self):
        pool = self._pool()
        for seed in range(200):
            picks = recommend(pool, count=3, rng=random.Random(seed))
            details = [p.place["detail_category"] for p in picks]
            self.assertEqual(len(details), len(set(details)), details)

    def test_different_shops_of_the_same_food_appear_across_draws(self):
        pool = self._pool()
        seen = set()
        for seed in range(120):
            for pick in recommend(pool, count=3, rng=random.Random(seed)):
                if pick.place["detail_category"] == "돈가스":
                    seen.add(pick.place["id"])
        # 4곳 전부 언젠가는 나와야 한다 - 한 곳이 자리를 독점하면 안 된다
        self.assertEqual(seen, {"A돈가스", "B돈가스", "C돈가스", "D돈가스"}, seen)

    def test_exclude_details_blocks_foods_already_on_screen(self):
        picks = recommend(self._pool(), count=3, rng=random.Random(3),
                          exclude_details={"돈가스", "국밥"})
        details = {p.place["detail_category"] for p in picks}
        self.assertFalse(details & {"돈가스", "국밥"}, details)
