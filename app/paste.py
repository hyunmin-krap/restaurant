"""네이버 지도 화면에서 복사한 덩어리에서 상호명과 분류를 뽑아낸다.

네이버 지도 목록을 드래그해서 복사하면 대부분 이런 마크다운 링크로 붙는다.

    * [역전회관 마포본점예약톡톡쿠폰한식](https://pcmap.place.naver.com/...)
    [영업 중별점4.41리뷰 4,220](https://pcmap.place.naver.com/...)
    * [마마반 본점마라탕](https://pcmap.place.naver.com/...)

상호명 뒤에 예약/톡톡/쿠폰 같은 뱃지와 업종이 띄어쓰기 없이 그대로 붙어 있다.
그래서 뒤에서부터 업종 하나를 떼고, 남은 뱃지를 계속 떼어 내면 상호명만 남는다.

    역전회관 마포본점예약톡톡쿠폰한식
      -> (업종) 한식      -> 역전회관 마포본점예약톡톡쿠폰
      -> (뱃지) 쿠폰/톡톡/예약 -> 역전회관 마포본점

떼어 낸 업종은 버리지 않는다. 카페·베이커리·주점처럼 점심이 아닌 곳과
호텔 뷔페처럼 너무 비싼 곳을 걸러 내는 데 쓴다.

링크가 하나도 없는(= 손으로 정리한) 덩어리를 붙여넣는 경우도 있어서,
그럴 때는 예전처럼 줄 단위로 읽는 방식으로 돌아간다.
"""
from __future__ import annotations

import re

from .categories import paste_exclusion_reason

MAX_NAME_LEN = 40

# --------------------------------------------------------------------------
# 마크다운 링크 형식
# --------------------------------------------------------------------------

_LINK = re.compile(r"\[([^\]]*)\]\(\s*([^)\s]+)\s*\)")
_BULLET = re.compile(r"^[ \t]*[*\-•]\s+")

# 이 호스트의 링크만 식당으로 본다. 광고 안내(help), 업체 등록(smartplace),
# 메뉴 주문(booking) 링크는 상호명이 아니다.
_PLACE_HOSTS = ("place.naver.com",)

# 상호명 뒤에 붙는 뱃지. 긴 것부터 떼어 낸다.
_BADGES = (
    "네이버페이", "새로오픈", "새로 오픈", "네이버예약", "바로예약",
    "예약", "톡톡", "쿠폰", "주문", "배달", "포장", "광고", "방송",
)

# 상호명 뒤에 붙는 업종. 네이버 지도가 실제로 쓰는 표기 그대로 적는다.
# 긴 것부터 매칭하므로('치킨,닭강정' 이 '치킨' 보다 먼저) 순서는 신경 쓰지 않아도 된다.
_CATEGORIES = (
    # 한식
    "한식", "백반,가정식", "한정식", "국밥", "곰탕,설렁탕", "해장국", "순대,순댓국",
    "감자탕", "추어탕", "삼계탕", "백숙,삼계탕", "찌개,전골", "샤브샤브", "두부요리",
    "칼국수,만두", "국수", "냉면", "막국수", "이북음식", "쌈밥", "보리밥",
    "족발,보쌈", "곱창,막창,양", "육류,고기요리", "돼지고기구이", "소고기구이",
    "정육식당", "닭갈비", "닭요리", "찜닭", "오리요리", "치킨,닭강정", "치킨",
    "생선구이", "생선회", "아귀찜,해물찜", "해물,생선요리", "게요리", "복어요리",
    "장어,먹장어요리", "주꾸미요리", "전,빈대떡", "떡,한과",
    # 중식
    "중식당", "중국요리", "딤섬,중식만두", "양꼬치", "마라탕",
    # 일식
    "일식당", "초밥,롤", "돈가스", "돈까스", "우동,소바", "일본식라면", "일식튀김,꼬치",
    "덮밥", "장어요리",
    # 양식
    "양식", "이탈리아음식", "스파게티,파스타전문", "스테이크,립", "패밀리레스토랑",
    "햄버거", "피자", "샌드위치", "다이어트,샐러드", "브런치카페", "브런치",
    "멕시코,남미음식", "스페인음식", "프랑스음식",
    # 아시아
    "아시아음식", "태국음식", "베트남음식", "인도음식", "중동음식", "카레",
    # 분식·간식
    "분식", "종합분식", "김밥", "떡볶이", "호두과자", "핫도그", "만두",
    # 뷔페
    "해산물뷔페", "뷔페",
    # 카페·주점
    "카페,디저트", "카페", "베이커리", "케이크전문", "떡카페", "도넛", "아이스크림",
    "빙수", "차,전통찻집", "과일주스전문점", "테이크아웃커피",
    "요리주점", "이자카야", "맥주,호프", "칵테일바", "와인바", "포장마차", "술집",
    # 기타
    "음식점", "구내식당", "도시락", "죽", "종합음식점",
)

_CATEGORIES_BY_LEN = tuple(sorted(set(_CATEGORIES), key=len, reverse=True))
_BADGES_BY_LEN = tuple(sorted(set(_BADGES), key=len, reverse=True))

# 링크 글씨지만 상호명이 아닌 것들
_LINK_NOISE_EXACT = {
    "더보기", "저장", "공유", "길찾기", "전화", "지도", "홈", "광고", "인기",
    "플레이스 플러스", "새로 오픈했어요", "내 업체 등록하기", "리뷰", "메뉴",
    "펼치기", "접기", "목록", "필터", "지도 홈",
}
_LINK_NOISE_CONTAINS = (
    "영업 중", "영업중", "영업 전", "영업종료", "24시간 영업", "별점", "리뷰 ",
    "인기 많은 메뉴", "휠체어", "미쉐린", "블루리본", "주차", "예약금", "원)",
)


def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower()


def _is_place_link(url: str) -> bool:
    host = _host(url)
    return any(host == h or host.endswith("." + h) for h in _PLACE_HOSTS)


def _strip_category(text: str) -> tuple[str, str]:
    """뒤에 붙은 업종 하나를 떼어 낸다. (남은 글자, 업종)"""
    for cat in _CATEGORIES_BY_LEN:
        if len(text) > len(cat) and text.endswith(cat):
            return text[: -len(cat)].strip(), cat
    return text, ""


def _strip_badges(text: str) -> str:
    """뒤에 붙은 뱃지를 더 이상 없을 때까지 떼어 낸다."""
    changed = True
    while changed:
        changed = False
        for badge in _BADGES_BY_LEN:
            if len(text) > len(badge) and text.endswith(badge):
                text = text[: -len(badge)].strip()
                changed = True
                break
    return text


def _split_label(label: str) -> tuple[str, str]:
    """'역전회관 마포본점예약톡톡쿠폰한식' -> ('역전회관 마포본점', '한식')"""
    text = re.sub(r"\s+", " ", (label or "").strip())
    text, category = _strip_category(text)
    text = _strip_badges(text)
    return text.strip(" ·|-–—"), category


def _is_link_noise(label: str) -> bool:
    stripped = (label or "").strip()
    if not stripped or stripped in _LINK_NOISE_EXACT:
        return True
    if any(token in stripped for token in _LINK_NOISE_CONTAINS):
        return True
    # 리뷰 한 줄, 소개 문구처럼 긴 글은 상호명이 아니다
    return len(stripped) > 60


def _entries_from_links(text: str) -> list[dict[str, str]]:
    """마크다운 링크 형식에서 (상호명, 업종)을 뽑는다."""
    out: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        if not _BULLET.match(raw):
            continue
        m = _LINK.search(raw)
        if not m or not _is_place_link(m.group(2)):
            continue
        label = m.group(1)
        if _is_link_noise(label):
            continue
        name, category = _split_label(label)
        if len(name) < 2 or len(name) > MAX_NAME_LEN:
            continue
        out.append({"name": name, "category": category})
    return out


# --------------------------------------------------------------------------
# 링크 없이 붙여넣은 경우 (손으로 정리했거나 앱에서 텍스트만 복사된 경우)
# --------------------------------------------------------------------------

_NOISE_EXACT = {
    "길찾기", "저장", "공유", "전화", "예약", "주문", "리뷰", "사진", "지도", "상세",
    "메뉴", "정보", "블로그리뷰", "방문자리뷰", "블로그", "영수증", "쿠폰", "광고",
    "더보기", "펼치기", "접기", "홈", "위치", "찾아가는길", "place+", "네이버예약",
    "톡톡", "포장", "배달", "매장", "주차", "무선인터넷", "단체석", "새로고침",
}

_NOISE_CONTAINS = (
    "영업 중", "영업중", "영업 전", "영업전", "영업 종료", "영업종료", "영업 시작",
    "오늘 휴무", "정기휴무", "휴무일", "24시간 영업", "브레이크타임", "라스트오더",
    "곧 영업", "분 후 영업", "에 영업", "리뷰 ", "방문자 리뷰", "블로그 리뷰",
    "저장 ", "이 지역", "검색 결과", "지역 재검색", "필터", "정렬", "관련도순",
)

_ADDRESS = re.compile(r"(서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충[북남]|전[북남]|경[북남]|제주)\s")
_ADDRESS_TAIL = re.compile(r"(로|길|동|가)\s*\d+([-–]\d+)?\s*(번지|층|호)?$")
_PHONE = re.compile(r"^\+?\d{2,4}[-–.\s]\d{3,4}[-–.\s]\d{4}$")
_RATING = re.compile(r"^[★☆]?\s*\d(\.\d+)?\s*(\(\s*[\d,]+\s*\))?$")
_COUNT_ONLY = re.compile(r"^\(?\s*[\d,]+\s*\)?$")
_INDEX = re.compile(r"^\d{1,3}\s*[.)]?$")
_DISTANCE = re.compile(r"^\d+(\.\d+)?\s*(m|km|미터|킬로)$", re.I)
_TIME = re.compile(r"^\d{1,2}:\d{2}")

_CATEGORY_WORDS = {
    "한식", "중식", "일식", "양식", "분식", "아시아음식", "뷔페", "치킨", "패스트푸드",
    "카페", "술집", "음식점", "돈까스", "돈가스", "국밥", "국수", "칼국수", "냉면",
    "삼겹살", "고깃집", "곱창", "족발", "보쌈", "찌개", "전골", "초밥", "회", "라멘",
    "우동", "소바", "피자", "파스타", "스테이크", "햄버거", "샌드위치", "샐러드",
    "김밥", "떡볶이", "만두", "죽", "도시락", "카레", "쌀국수", "마라탕", "양꼬치",
    "육류,고기", "해물,생선", "카페,디저트", "구내식당", "백반,가정식", "닭요리",
}


def _is_noise(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped in _NOISE_EXACT:
        return True
    lowered = stripped.lower()
    if lowered in {w.lower() for w in _NOISE_EXACT}:
        return True
    if stripped in _CATEGORY_WORDS:
        return True
    # 'ㅁ지도 홈', '길찾기 저장 공유' 처럼 UI 단어만 이어 붙은 줄
    tokens = stripped.split()
    if len(tokens) > 1 and all(
        t in _NOISE_EXACT or t in _CATEGORY_WORDS for t in tokens
    ):
        return True
    if any(token in stripped for token in _NOISE_CONTAINS):
        return True
    for pattern in (_INDEX, _RATING, _COUNT_ONLY, _PHONE, _DISTANCE, _TIME):
        if pattern.match(stripped):
            return True
    if _ADDRESS.match(stripped) or _ADDRESS_TAIL.search(stripped):
        return True
    if len(stripped) > MAX_NAME_LEN:
        return True
    return False


def _clean(line: str) -> str:
    """상호명 뒤에 붙어 온 별점·리뷰수 같은 꼬리를 떼어 낸다."""
    text = line.strip()
    text = re.sub(r"^\d{1,3}\s*[.)]\s*", "", text)          # 앞 순번
    text = re.sub(r"\s*[★☆]\s*\d(\.\d+)?.*$", "", text)       # ★4.5 부터 뒤로
    # '4.52 (120)' / '4.1' 같은 별점 꼬리. 소수점이나 괄호가 있어야 지운다
    # ('김밥천국 24' 처럼 숫자로 끝나는 상호명을 자르지 않기 위해)
    text = re.sub(r"\s+\d\.\d+\s*(\(\s*[\d,]+\s*\))?\s*$", "", text)
    text = re.sub(r"\s*\(\s*[\d,]+\s*\)\s*$", "", text)      # (120)
    text = re.sub(r"\s*·.*$", "", text)                      # 가운뎃점 뒤 설명
    text = re.sub(r"\s{2,}.*$", "", text)                    # 탭/여러 칸 뒤는 다른 열
    return text.strip(" ·|-–—\t")


def _entries_from_lines(text: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        if _is_noise(raw):
            continue
        name = _clean(raw)
        if not name or len(name) < 2 or len(name) > MAX_NAME_LEN:
            continue
        if _is_noise(name):
            continue
        out.append({"name": name, "category": ""})
    return out


# --------------------------------------------------------------------------
# 공개 API
# --------------------------------------------------------------------------


def extract_entries(
    text: str,
    limit: int = 400,
    *,
    drop_cafe: bool = True,
    drop_pricey: bool = True,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """붙여넣은 덩어리에서 (등록할 곳, 걸러 낸 곳)을 돌려준다.

    걸러 낸 항목에는 왜 걸렀는지(`reason`)가 붙는다. 화면에서 그대로 보여 준다.
    """
    entries = _entries_from_links(text)
    if not entries:
        entries = _entries_from_lines(text)

    keep: list[dict[str, str]] = []
    dropped: list[dict[str, str]] = []
    seen: set[str] = set()

    for entry in entries:
        key = entry["name"].replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        reason = paste_exclusion_reason(
            entry.get("category", ""), entry["name"],
            drop_cafe=drop_cafe, drop_pricey=drop_pricey,
        )
        if reason:
            dropped.append({**entry, "reason": reason})
            continue
        keep.append(entry)
        if len(keep) >= limit:
            break
    return keep, dropped


def extract_names(text: str, limit: int = 400, **kwargs) -> list[str]:
    """등록 대상 상호명만 순서대로 돌려준다."""
    keep, _ = extract_entries(text, limit, **kwargs)
    return [e["name"] for e in keep]
