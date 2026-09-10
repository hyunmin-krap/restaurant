@echo off
REM ---------------------------------------------------------------
REM  Lunch picker - pull the latest version. Just double-click this.
REM  Close the app (the black window) first.
REM
REM  Your data (data\lunch.db) and settings (.env) are NOT touched:
REM  the downloaded archive does not contain them, and they are
REM  excluded from the copy as well.
REM
REM  (ASCII on purpose - a UTF-8 .bat shows garbled text in the
REM   Korean Windows console.)
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0.."

set BRANCH=claude/lunch-menu-recommender-548c9v
set ZIPURL=https://github.com/hyunmin-krap/restaurant/archive/refs/heads/%BRANCH%.zip
set TMPDIR=%TEMP%\lunch-update

echo.
echo   Downloading the latest version...

if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
mkdir "%TMPDIR%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%ZIPURL%' -OutFile '%TMPDIR%\src.zip' -UseBasicParsing; Expand-Archive -Path '%TMPDIR%\src.zip' -DestinationPath '%TMPDIR%\out' -Force"
if errorlevel 1 goto :failed

REM  The archive unpacks into one folder; copy what is inside it.
set SRC=
for /d %%D in ("%TMPDIR%\out\*") do set SRC=%%D
if not defined SRC goto :failed

echo   Installing...

REM  /XD data  keeps the database. /XF .env keeps the saved settings.
REM  robocopy returns 0-7 on success, 8+ on real failure.
robocopy "%SRC%" "%CD%" /E /IS /IT /NFL /NDL /NJH /NJS /NP /XD data /XF .env >nul
if errorlevel 8 goto :failed

REM  Files from the internet carry a "downloaded" mark that Smart App
REM  Control blocks. Strip it so start.bat runs without being blocked.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%CD%' -Recurse -File | Unblock-File" >nul 2>&1

rmdir /s /q "%TMPDIR%"

echo.
echo   [OK] Updated. Your restaurants and settings were kept.
echo.
echo   Now double-click  scripts\start.bat
echo.
pause
exit /b 0

:failed
echo.
echo   [X] Update failed.
echo.
echo       Make sure the app is closed, check your connection,
echo       and try again. To do it by hand, download this ZIP
echo       and unpack it over this folder:
echo.
echo       %ZIPURL%
echo.
pause
exit /b 1
