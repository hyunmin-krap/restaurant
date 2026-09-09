"""앱 자체 호출 한도 — 네이버 API HUB 가 종량제라 마지막 방어선을 둔다."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app import budget
from app import db as dbm
from app.providers import BudgetExhausted, NaverLocalProvider, ProviderError


class BudgetTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(Path(self.tmp.name) / "t.db")
        dbm.init_db(self.conn)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_처음에는_아무것도_안_썼다(self):
        self.assertEqual(budget.used(self.conn, "naver"), 0)
        self.assertEqual(budget.used_today(self.conn, "naver"), 0)
        self.assertEqual(budget.remaining(self.conn, "naver", 5000, 1000), 1000)

    def test_한_번_쓰면_일별_월별에_같이_쌓인다(self):
        budget.consume(self.conn, "naver", 3)
        self.assertEqual(budget.used(self.conn, "naver"), 3)
        self.assertEqual(budget.used_today(self.conn, "naver"), 3)

    def test_더_빡빡한_한도를_따른다(self):
        budget.consume(self.conn, "naver", 995)
        # 월 5,000 은 4,005 남았지만 일 1,000 은 5 남았다
        self.assertEqual(budget.remaining(self.conn, "naver", 5000, 1000), 5)

    def test_일_한도가_0이면_월_한도만_본다(self):
        budget.consume(self.conn, "naver", 995)
        self.assertEqual(budget.remaining(self.conn, "naver", 5000, 0), 4005)

    def test_제공자끼리_섞이지_않는다(self):
        budget.consume(self.conn, "naver", 10)
        self.assertEqual(budget.used(self.conn, "google"), 0)

    def test_달이_바뀌면_0으로_돌아간다(self):
        sep = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        oct_ = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
        budget.consume(self.conn, "naver", 100, now=sep)
        self.assertEqual(budget.used(self.conn, "naver", now=sep), 100)
        self.assertEqual(budget.used(self.conn, "naver", now=oct_), 0)

    def test_날짜가_바뀌면_일_카운터만_0으로_돌아간다(self):
        d1 = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 9, 21, 3, 0, tzinfo=timezone.utc)
        budget.consume(self.conn, "naver", 40, now=d1)
        self.assertEqual(budget.used_today(self.conn, "naver", now=d2), 0)
        self.assertEqual(budget.used(self.conn, "naver", now=d2), 40)   # 같은 달

    def test_한국시간_기준으로_끊는다(self):
        # UTC 2026-09-30 16:00 = KST 2026-10-01 01:00 -> 10월로 잡혀야 한다
        utc = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(budget.month_key("naver", utc), "naver_calls_2026-10")

    def test_값이_깨져_있어도_0으로_읽는다(self):
        dbm.set_setting(self.conn, budget.month_key("naver"), "이상한값")
        self.assertEqual(budget.used(self.conn, "naver"), 0)

    def test_status_는_화면에_뿌릴_요약을_준다(self):
        budget.consume(self.conn, "naver", 1200)
        st = budget.status(self.conn, "naver", 5000, 1000)
        self.assertEqual(st["used"], 1200)
        self.assertEqual(st["remaining"], 0)      # 일 한도를 이미 넘겼다
        self.assertTrue(st["exhausted"])


class GuardTestCase(unittest.TestCase):
    """가드는 search() 한 곳에만 물려 있어 어느 경로로 들어와도 세어진다."""

    def setUp(self):
        self.calls = []
        self.allow = 2

        def guard():
            if len(self.calls) >= self.allow:
                raise BudgetExhausted("한도 초과")
            self.calls.append(1)

        self.provider = NaverLocalProvider("id", "secret", guard=guard)
        # 실제 HTTP 는 타지 않는다
        self.provider.search.__func__  # noqa: B018 - 존재 확인용

    def test_한도를_넘으면_호출_전에_막는다(self):
        import app.providers.naver_local as mod
        original = mod.http_json
        mod.http_json = lambda *a, **k: {"items": []}
        try:
            self.provider.search("한식")
            self.provider.search("중식")
            with self.assertRaises(BudgetExhausted):
                self.provider.search("일식")
        finally:
            mod.http_json = original
        self.assertEqual(len(self.calls), 2)

    def test_한도_초과는_ProviderError_로도_잡힌다(self):
        self.allow = 0
        with self.assertRaises(ProviderError):
            self.provider.search("한식")

    def test_가드가_없으면_그냥_호출된다(self):
        import app.providers.naver_local as mod
        original = mod.http_json
        mod.http_json = lambda *a, **k: {"items": [{"title": "x"}]}
        try:
            plain = NaverLocalProvider("id", "secret")
            self.assertEqual(plain.search("한식"), [{"title": "x"}])
        finally:
            mod.http_json = original

    def test_수집_중_한도를_만나면_남은_키워드를_돌지_않는다(self):
        import app.providers.naver_local as mod
        original = mod.http_json
        mod.http_json = lambda *a, **k: {"items": []}
        try:
            with self.assertRaises(BudgetExhausted):
                list(self.provider.collect_nearby(37.5, 127.0, "공덕", 500))
        finally:
            mod.http_json = original
        self.assertEqual(len(self.calls), 2)   # 76개 키워드를 다 돌지 않았다


if __name__ == "__main__":
    unittest.main()


class ServerGuardTestCase(unittest.TestCase):
    """서버가 만들어 주는 프로바이더에 한도 가드가 실제로 물려 있는지."""

    def setUp(self):
        from dataclasses import replace

        from app.config import CONFIG
        from app.server import AppState, _naver_provider

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = replace(
            CONFIG, db_path=Path(self.tmp.name) / "t.db",
            naver_client_id="id", naver_client_secret="secret",
            naver_monthly_call_limit=3, naver_daily_call_limit=2,
        )
        self.state = AppState(cfg)
        self.addCleanup(self.state.conn.close)
        self.provider = _naver_provider(self.state)

    def test_호출할_때마다_카운터가_올라간다(self):
        import app.providers.naver_local as mod
        original = mod.http_json
        mod.http_json = lambda *a, **k: {"items": []}
        try:
            self.provider.search("한식")
            self.assertEqual(budget.used(self.state.conn, "naver"), 1)
            self.provider.search("중식")
            self.assertEqual(budget.used(self.state.conn, "naver"), 2)
            # 일 한도 2건을 다 썼다
            with self.assertRaises(BudgetExhausted) as ctx:
                self.provider.search("일식")
        finally:
            mod.http_json = original
        self.assertIn("한도", str(ctx.exception))
        self.assertEqual(budget.used(self.state.conn, "naver"), 2)

    def test_한도를_넘어도_붙여넣기_등록은_된다(self):
        from app.sync import import_named_places
        budget.consume(self.state.conn, "naver", 999)
        import_named_places(
            self.state.conn, None, [{"name": "역전회관 마포본점", "category": "한식"}],
            37.54306, 126.95111,
        )
        n = self.state.conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        self.assertEqual(n, 1)
