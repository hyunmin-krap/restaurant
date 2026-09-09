#!/usr/bin/env bash
# 맥에서 더블클릭으로 실행하는 시작 파일.
# 터미널을 몰라도 되게, 필요한 준비를 여기서 다 한다.
cd "$(dirname "$0")/.." || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 파이썬이 없습니다."
  echo "   https://www.python.org/downloads/ 에서 받아 설치한 뒤 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다."
  exit 1
fi

[ -f .env ] || cp .env.example .env

PORT="${PORT:-8000}"
echo "🍚 잠시 후 브라우저가 열립니다. 이 창은 켜 두세요 (닫으면 앱이 꺼집니다)."
( sleep 2; open "http://localhost:${PORT}" >/dev/null 2>&1 ) &
python3 -m app
