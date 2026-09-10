#!/usr/bin/env bash
# 맥·리눅스에서 최신 버전으로 갱신한다. 앱(터미널 창)은 먼저 닫으세요.
# data/lunch.db 와 .env 는 건드리지 않는다 (받는 압축 파일에 들어 있지도 않다).
set -euo pipefail
cd "$(dirname "$0")/.."

BRANCH="claude/lunch-menu-recommender-548c9v"
ZIPURL="https://github.com/hyunmin-krap/restaurant/archive/refs/heads/${BRANCH}.zip"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "최신 버전을 받는 중..."
curl -fsSL "$ZIPURL" -o "$TMP/src.zip"
unzip -q "$TMP/src.zip" -d "$TMP/out"

SRC="$(find "$TMP/out" -mindepth 1 -maxdepth 1 -type d | head -1)"
[ -n "$SRC" ] || { echo "압축을 푸는 데 실패했습니다."; exit 1; }

# data/ 와 .env 는 빼고 덮어쓴다. rsync 가 없는 맥도 있어서 tar 로 옮긴다.
rm -rf "$SRC/data"
rm -f "$SRC/.env"
tar -C "$SRC" -cf - . | tar -xf - -C .
chmod +x scripts/*.command 2>/dev/null || true

echo "완료. 등록한 식당과 설정은 그대로입니다."
echo "scripts/start.command 를 다시 실행하세요."
