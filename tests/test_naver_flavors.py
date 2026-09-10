"""지역검색을 부를 수 있는 곳이 두 군데다 (API HUB / 개발자센터).

키만 보고는 어느 쪽인지 알 수 없어서 한 번 불러 보고 정한다.
"""
import unittest

import app.providers.naver_local as mod
from app.providers import NaverLocalProvider, ProviderError


class FakeHTTP:
    """어느 주소·헤더로 왔는지 기록하고, 정해 둔 곳에만 200 을 준다."""

    def __init__(self, ok_host: str | None, items=None):
        self.ok_host = ok_host
        self.items = items if items is not None else [{"title": "가게"}]
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers=None, **kw):
        self.calls.append((url, dict(headers or {})))
        if self.ok_host and self.ok_host in url:
            return {"items": self.items}
        raise ProviderError(f"HTTP 401 {url} :: 인증 실패")


class FlavorTestCase(unittest.TestCase):
    def setUp(self):
        self._original = mod.http_json
        self.addCleanup(lambda: setattr(mod, "http_json", self._original))

    def _provider(self, fake, **kw):
        mod.http_json = fake
        return NaverLocalProvider("myid", "mysecret", request_delay=0, **kw)

    def test_HUB_키면_HUB_주소로_붙는다(self):
        fake = FakeHTTP("naverapihub.apigw.ntruss.com")
        p = self._provider(fake)
        self.assertEqual(p.search("한식"), [{"title": "가게"}])
        url, headers = fake.calls[0]
        self.assertIn("naverapihub.apigw.ntruss.com/search/v1/local", url)
        self.assertEqual(headers["X-NCP-APIGW-API-KEY-ID"], "myid")
        self.assertEqual(headers["X-NCP-APIGW-API-KEY"], "mysecret")
        self.assertEqual(p.flavor, "hub")

    def test_예전_키면_예전_주소로_넘어간다(self):
        fake = FakeHTTP("openapi.naver.com")
        p = self._provider(fake)
        self.assertEqual(p.search("한식"), [{"title": "가게"}])
        self.assertEqual(len(fake.calls), 2)          # HUB 먼저 보고 넘어갔다
        url, headers = fake.calls[1]
        self.assertIn("openapi.naver.com", url)
        self.assertEqual(headers["X-Naver-Client-Id"], "myid")
        self.assertEqual(p.flavor, "legacy")

    def test_되는_쪽을_찾으면_다음부터_한_번만_부른다(self):
        fake = FakeHTTP("openapi.naver.com")
        p = self._provider(fake)
        p.search("한식")
        p.search("중식")
        p.search("일식")
        self.assertEqual(p.flavor, "legacy")
        self.assertEqual(len(fake.calls), 2 + 1 + 1)  # 첫 번만 두 번 시도

    def test_양쪽_다_막히면_사유를_알려_준다(self):
        fake = FakeHTTP(None)
        p = self._provider(fake)
        with self.assertRaises(ProviderError) as ctx:
            p.search("한식")
        msg = str(ctx.exception)
        self.assertIn("거절", msg)
        self.assertIn("Client ID", msg)

    def test_인증_말고_다른_실패는_바로_올린다(self):
        def boom(url, headers=None, **kw):
            raise ProviderError("연결 실패 :: 타임아웃")
        p = self._provider(boom)
        with self.assertRaises(ProviderError) as ctx:
            p.search("한식")
        self.assertIn("타임아웃", str(ctx.exception))

    def test_flavor_를_직접_지정할_수도_있다(self):
        fake = FakeHTTP("openapi.naver.com")
        p = self._provider(fake, flavor="legacy")
        p.search("한식")
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("openapi.naver.com", fake.calls[0][0])


class CollectFailureTestCase(unittest.TestCase):
    """전부 실패했는데 '완료 - 0곳' 으로 끝나면 안 된다."""

    def setUp(self):
        self._original = mod.http_json
        self.addCleanup(lambda: setattr(mod, "http_json", self._original))

    def test_계속_실패하면_일찍_멈춘다(self):
        fake = FakeHTTP(None)
        mod.http_json = fake
        p = NaverLocalProvider("id", "sec", request_delay=0)
        with self.assertRaises(ProviderError) as ctx:
            list(p.collect_nearby(37.5, 127.0, "공덕", 500))
        self.assertIn("실패", str(ctx.exception))
        # 76개 키워드를 다 돌지 않았다 (키워드당 2번씩 시도해도 10번 이내)
        self.assertLess(len(fake.calls), 10)

    def test_한_곳이라도_찾으면_중간_실패는_넘어간다(self):
        state = {"n": 0}

        def flaky(url, headers=None, **kw):
            state["n"] += 1
            if state["n"] == 1:
                return {"items": [{
                    "title": "공덕밥집", "category": "음식점>한식",
                    "mapx": "1269511100", "mapy": "375430600",
                    "roadAddress": "서울 마포구 만리재로 15", "address": "서울 마포구 공덕동",
                }]}
            raise ProviderError("HTTP 500 :: 일시 오류")

        mod.http_json = flaky
        p = NaverLocalProvider("id", "sec", request_delay=0)
        found = list(p.collect_nearby(37.54306, 126.95111, "공덕", 500))
        self.assertEqual([f.name for f in found], ["공덕밥집"])


if __name__ == "__main__":
    unittest.main()
