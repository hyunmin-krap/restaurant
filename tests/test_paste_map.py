"""네이버 지도에서 실제로 복사되는 마크다운 링크 형식 파싱."""
import unittest

from app.categories import is_meal, is_pricey, paste_exclusion_reason
from app.paste import _split_label, extract_entries, extract_names

PC = "https://pcmap.place.naver.com/restaurant/list?query=x#"


def bullet(label: str, url: str = PC) -> str:
    return f"* [{label}]({url})"


REAL = "\n".join([
    bullet("역전회관 마포본점예약톡톡쿠폰한식"),
    "[마포에서 먹기좋은 든든한 한식](%s)" % PC,
    "[영업 중별점4.41리뷰 4,220](%s)" % PC,
    "[광고](https://help.naver.com/support/alias/NSP/NSP_53.naver)",
    bullet("스키당 대흥점예약일식당"),
    bullet("마마반 본점마라탕"),
    "* 새로 오픈했어요",
    "[내 업체 등록하기](https://new.smartplace.naver.com/introduction/solution-market/newOpen)",
    "   * 플레이스 플러스",
    bullet("온오프커피 효창카페"),
    bullet("롯데시티호텔 마포 씨카페예약쿠폰뷔페"),
    "[27,900원](https://m.booking.naver.com/order/bizes/1/items/2/menus/3)",
    "* ",
])


class SplitLabelTestCase(unittest.TestCase):
    def test_뱃지와_업종을_뒤에서_떼어_낸다(self):
        self.assertEqual(
            _split_label("역전회관 마포본점예약톡톡쿠폰한식"),
            ("역전회관 마포본점", "한식"),
        )

    def test_네이버페이_같은_긴_뱃지도_떼어_낸다(self):
        self.assertEqual(
            _split_label("천년닭강정 마포점네이버페이예약주문배달톡톡쿠폰치킨,닭강정"),
            ("천년닭강정 마포점", "치킨,닭강정"),
        )

    def test_업종은_한_번만_떼어_낸다(self):
        # '을밀대 평양냉면' 의 '냉면' 까지 잘리면 안 된다
        self.assertEqual(_split_label("을밀대 평양냉면냉면"), ("을밀대 평양냉면", "냉면"))

    def test_상호명에_카페가_들어가도_업종만_떼어_낸다(self):
        self.assertEqual(
            _split_label("롯데시티호텔 마포 씨카페예약쿠폰뷔페"),
            ("롯데시티호텔 마포 씨카페", "뷔페"),
        )
        self.assertEqual(_split_label("온오프커피 효창카페"), ("온오프커피 효창", "카페"))

    def test_긴_업종을_먼저_잡는다(self):
        self.assertEqual(_split_label("우수예약톡톡쿠폰육류,고기요리"), ("우수", "육류,고기요리"))
        self.assertEqual(
            _split_label("아이엠베이글 공덕점네이버페이주문톡톡브런치카페"),
            ("아이엠베이글 공덕점", "브런치카페"),
        )

    def test_업종이_없으면_상호명만_남는다(self):
        self.assertEqual(_split_label("마마반 본점"), ("마마반 본점", ""))


class ExtractFromMapTestCase(unittest.TestCase):
    def setUp(self):
        self.keep, self.dropped, self.truncated = extract_entries(REAL)

    def test_불릿에_달린_place_링크만_식당으로_본다(self):
        self.assertEqual(
            [e["name"] for e in self.keep],
            ["역전회관 마포본점", "스키당 대흥점", "마마반 본점"],
        )

    def test_업종을_같이_돌려준다(self):
        self.assertEqual(self.keep[1]["category"], "일식당")

    def test_광고_업체등록_주문_링크는_버린다(self):
        names = [e["name"] for e in self.keep] + [e["name"] for e in self.dropped]
        for junk in ("광고", "내 업체 등록하기", "27,900원", "플레이스 플러스", "새로 오픈했어요"):
            self.assertNotIn(junk, names)

    def test_리뷰_영업상태_줄은_버린다(self):
        names = [e["name"] for e in self.keep]
        self.assertNotIn("마포에서 먹기좋은 든든한 한식", names)
        self.assertNotIn("영업 중별점4.41리뷰 4,220", names)

    def test_카페와_비싼_곳은_이유와_함께_걸러진다(self):
        by_name = {e["name"]: e["reason"] for e in self.dropped}
        self.assertEqual(by_name["온오프커피 효창"], "카페·주점")
        self.assertIn("롯데시티호텔 마포 씨카페", by_name)

    def test_같은_곳이_두_번_나와도_한_번만(self):
        text = "\n".join([bullet("마마반 본점마라탕"), bullet("마마반 본점")])
        keep, _d, _t = extract_entries(text)
        self.assertEqual([e["name"] for e in keep], ["마마반 본점"])

    def test_필터를_끄면_카페도_남는다(self):
        keep, dropped, _t = extract_entries(REAL, drop_cafe=False, drop_pricey=False)
        self.assertIn("온오프커피 효창", [e["name"] for e in keep])
        self.assertEqual(dropped, [])

    def test_extract_names_는_등록할_이름만_돌려준다(self):
        self.assertEqual(
            extract_names(REAL), ["역전회관 마포본점", "스키당 대흥점", "마마반 본점"]
        )

    def test_링크가_없으면_줄_단위로_읽는다(self):
        plain = "1\n공덕 돈까스\n돈까스\n4.52 (120)\n서울 마포구 만리재로 15\n"
        self.assertEqual(extract_names(plain), ["공덕 돈까스"])


class FilterTestCase(unittest.TestCase):
    def test_카페_빵집_술집은_끼니가_아니다(self):
        for cat, name in [("카페", "투썸플레이스 마포대로점"), ("베이커리", "우스블랑"),
                          ("카페,디저트", "삼정카페"), ("케이크전문", "플디 케이크하우스"),
                          ("이자카야", "오롯"), ("요리주점", "작은문바이도쿄")]:
            self.assertFalse(is_meal(cat, name), f"{name}({cat})")

    def test_밥집은_끼니다(self):
        for cat, name in [("한식", "역전회관 마포본점"), ("국밥", "보승회관 마포역점"),
                          ("돼지고기구이", "마포갈매기 본점"), ("냉면", "을밀대 평양냉면"),
                          ("브런치카페", "아이엠베이글 공덕점")]:
            self.assertTrue(is_meal(cat, name), f"{name}({cat})")

    def test_호텔_뷔페_한우는_비싸다(self):
        for cat, name in [("해산물뷔페", "마키노차야 마포점"), ("뷔페", "서울가든호텔 라스텔라"),
                          ("한정식", "색동저고리"), ("소고기구이", "소울한우 마포역본점")]:
            self.assertTrue(is_pricey(cat, name), f"{name}({cat})")

    def test_한우국밥은_비싸지_않다(self):
        self.assertFalse(is_pricey("국밥", "한우국밥집"))

    def test_평범한_밥집은_비싸지_않다(self):
        for cat, name in [("한식", "옥된장 공덕점"), ("육류,고기요리", "궁전갈비"),
                          ("햄버거", "맥도날드 공덕점"), ("칼국수,만두", "봉평옹심이메밀칼국수")]:
            self.assertFalse(is_pricey(cat, name), f"{name}({cat})")

    def test_이유를_말해_준다(self):
        self.assertEqual(paste_exclusion_reason("카페", "투썸"), "카페·주점")
        self.assertEqual(paste_exclusion_reason("해산물뷔페", "마키노차야"), "비싼 편")
        self.assertEqual(paste_exclusion_reason("한식", "역전회관"), "")

    def test_필터를_끄면_이유가_없다(self):
        self.assertEqual(
            paste_exclusion_reason("카페", "투썸", drop_cafe=False), ""
        )
        self.assertEqual(
            paste_exclusion_reason("해산물뷔페", "마키노차야", drop_pricey=False), ""
        )


if __name__ == "__main__":
    unittest.main()


class KeylessImportTestCase(unittest.TestCase):
    """네이버 검색 API 키 없이, 붙여넣기만으로 등록하는 경로."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from app import db as dbm
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = dbm.connect(Path(self.tmp.name) / "t.db")
        dbm.init_db(self.conn)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def test_좌표_없이_상호명과_업종으로_등록한다(self):
        from app.sync import SyncState, import_named_places
        entries = [
            {"name": "역전회관 마포본점", "category": "한식"},
            {"name": "을밀대 평양냉면", "category": "냉면"},
        ]
        state = SyncState()
        import_named_places(self.conn, None, entries, 37.54306, 126.95111, state=state)
        rows = {r["name"]: r for r in self.conn.execute("SELECT * FROM places").fetchall()}
        self.assertEqual(set(rows), {"역전회관 마포본점", "을밀대 평양냉면"})
        self.assertIsNone(rows["을밀대 평양냉면"]["distance_m"])
        self.assertEqual(rows["을밀대 평양냉면"]["detail_category"], "냉면")
        self.assertEqual(rows["역전회관 마포본점"]["source"], "naver_paste")

    def test_거리를_모르는_곳도_추천_후보에_들어간다(self):
        from app import service
        from app.sync import import_named_places
        import_named_places(
            self.conn, None, [{"name": "역전회관 마포본점", "category": "한식"}],
            37.54306, 126.95111,
        )
        names = [c["name"] for c in service.load_candidates(self.conn, 500)]
        self.assertIn("역전회관 마포본점", names)

    def test_두_번_등록해도_한_곳만_남는다(self):
        from app.sync import import_named_places
        entries = [{"name": "역전회관 마포본점", "category": "한식"}]
        import_named_places(self.conn, None, entries, 37.54306, 126.95111)
        import_named_places(self.conn, None, entries, 37.54306, 126.95111)
        count = self.conn.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        self.assertEqual(count, 1)

    def test_점심_영업으로_표시된다(self):
        from app.sync import import_named_places
        import_named_places(
            self.conn, None, [{"name": "역전회관 마포본점", "category": "한식"}],
            37.54306, 126.95111,
        )
        row = self.conn.execute("SELECT lunch_open FROM places").fetchone()
        self.assertEqual(row["lunch_open"], 1)

    def test_업종을_모르면_상호명으로_분류한다(self):
        from app.sync import import_named_places
        import_named_places(
            self.conn, None, [{"name": "마포양지설렁탕", "category": ""}],
            37.54306, 126.95111,
        )
        row = self.conn.execute("SELECT detail_category FROM places").fetchone()
        self.assertEqual(row["detail_category"], "설렁탕")


class ReviewerNicknameTestCase(unittest.TestCase):
    """목록을 드래그하면 리뷰 쓴 사람 닉네임이 같은 모양의 링크로 딸려 온다."""

    NICKS = ["체리4810", "사탕공장14", "NEWS WIN", "line3373", "남다른 CEO",
             "크로마 스토리", "기쁨감사", "freshjh124", "White45", "단아한김션",
             "긍정적인 뽀로로", "카모메41"]

    def test_닉네임은_식당으로_보지_않는다(self):
        text = "\n".join(bullet(n) for n in self.NICKS)
        keep, dropped, _ = extract_entries(text)
        self.assertEqual(keep, [])
        self.assertEqual(dropped, [])

    def test_닉네임_사이에_섞인_식당은_살린다(self):
        text = "\n".join([
            bullet("체리4810"),
            bullet("역전회관 마포본점예약톡톡쿠폰한식"),
            bullet("긍정적인 뽀로로"),
            bullet("을밀대 평양냉면냉면"),
            bullet("line3373"),
        ])
        self.assertEqual(
            extract_names(text), ["역전회관 마포본점", "을밀대 평양냉면"]
        )

    def test_업종이_안_붙은_항목은_거른다(self):
        # 지도 목록의 식당에는 업종이 반드시 붙는다. 안 붙었으면 닉네임 쪽으로 본다.
        for label in ("황태뚝배기", "긍정적인 뽀로로", "크로마 스토리"):
            self.assertEqual(extract_names(bullet(label)), [], label)

    def test_상호명이_업종으로_끝나도_하나만_뗀다(self):
        # 지도는 '황태뚝배기해장국' + 업종 '해장국' 을 붙여 보내므로 이렇게 온다.
        self.assertEqual(
            extract_names(bullet("황태뚝배기해장국해장국")), ["황태뚝배기해장국"]
        )
        # 업종이 한 번만 붙어 오면 상호명 끝인지 업종인지 구분할 길이 없다.
        # 이때는 식당 쪽으로 본다 (닉네임을 들이는 것보다 덜 나쁘다).
        self.assertEqual(extract_names(bullet("황태뚝배기해장국")), ["황태뚝배기"])

    def test_링크_없는_줄_단위_입력에는_적용하지_않는다(self):
        # 손으로 정리해 붙여넣는 경우는 업종이 없는 게 정상이다.
        plain = "1\n공덕 돈까스\n돈까스\n4.52 (120)\n"
        self.assertEqual(extract_names(plain), ["공덕 돈까스"])
