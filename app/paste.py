"""네이버 지도 화면에서 복사한 덩어리에서 상호명만 뽑아낸다.

네이버 지도 목록을 드래그해서 복사하면 상호명 사이에 분류·별점·리뷰수·주소·
영업상태·버튼 이름이 잔뜩 섞여 나온다. 사용자가 그걸 손으로 정리하게 두지 않고,
그대로 붙여넣어도 상호명만 골라내도록 한다.

예시 입력:
    1
    공덕 돈까스
    돈까스
    4.52 (120)
    서울 마포구 만리재로 15
    영업 중 · 21:00에 영업 종료
    길찾기  저장  공유
    2
    마포 돼지국밥
    ...
"""
from __future__ import annotations

import re

# 한 줄 전체가 이것들 중 하나면 버린다.
_NOISE_EXACT = {
    "길찾기", "저장", "공유", "전화", "예약", "주문", "리뷰", "사진", "지도", "상세",
    "메뉴", "정보", "블로그리뷰", "방문자리뷰", "블로그", "영수증", "쿠폰", "광고",
    "더보기", "펼치기", "접기", "홈", "위치", "찾아가는길", "place+", "네이버예약",
    "톡톡", "포장", "배달", "매장", "주차", "무선인터넷", "단체석", "새로고침",
}

# 줄 어딘가에 이게 있으면 그 줄은 데이터가 아니라 상태/안내다.
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

# 이 단어들만 홀로 있는 줄은 상호명이 아니라 업종 표시다.
_CATEGORY_WORDS = {
    "한식", "중식", "일식", "양식", "분식", "아시아음식", "뷔페", "치킨", "패스트푸드",
    "카페", "술집", "음식점", "돈까스", "돈가스", "국밥", "국수", "칼국수", "냉면",
    "삼겹살", "고깃집", "곱창", "족발", "보쌈", "찌개", "전골", "초밥", "회", "라멘",
    "우동", "소바", "피자", "파스타", "스테이크", "햄버거", "샌드위치", "샐러드",
    "김밥", "떡볶이", "만두", "죽", "도시락", "카레", "쌀국수", "마라탕", "양꼬치",
    "육류,고기", "해물,생선", "카페,디저트", "구내식당", "백반,가정식", "닭요리",
}

MAX_NAME_LEN = 40


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


def extract_names(text: str, limit: int = 300) -> list[str]:
    """붙여넣은 덩어리에서 상호명 후보만 순서대로 뽑는다. 중복은 제거."""
    seen: set[str] = set()
    names: list[str] = []
    for raw in (text or "").splitlines():
        if _is_noise(raw):
            continue
        name = _clean(raw)
        if not name or len(name) < 2 or len(name) > MAX_NAME_LEN:
            continue
        if _is_noise(name):
            continue
        key = name.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= limit:
            break
    return names
