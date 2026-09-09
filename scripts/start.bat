@echo off
REM 윈도우에서 더블클릭으로 실행하는 시작 파일.
chcp 65001 >nul
cd /d "%~dp0.."

where py >nul 2>&1 && (set PY=py) || (
  where python >nul 2>&1 && (set PY=python) || (
    echo.
    echo  [X] 파이썬이 없습니다.
    echo      https://www.python.org/downloads/ 에서 받아 설치하세요.
    echo      설치할 때 "Add Python to PATH" 를 꼭 체크하세요.
    echo.
    pause
    exit /b 1
  )
)

if not exist .env copy .env.example .env >nul

if "%PORT%"=="" set PORT=8000
echo.
echo  [*] 잠시 후 브라우저가 열립니다. 이 창은 켜 두세요 (닫으면 앱이 꺼집니다).
echo.
start "" /b cmd /c "timeout /t 2 >nul & start http://localhost:%PORT%"
%PY% -m app
pause
