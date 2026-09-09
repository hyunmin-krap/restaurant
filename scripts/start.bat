@echo off
REM ---------------------------------------------------------------
REM  Lunch picker - Windows start file. Just double-click this.
REM  (Messages here are ASCII on purpose: a .bat file saved as UTF-8
REM   shows garbled text in the Korean Windows console.)
REM ---------------------------------------------------------------
setlocal
cd /d "%~dp0.."

if "%PORT%"=="" set PORT=8000
set PY=

REM  'py' is the Python launcher and only exists with a real install.
REM  Try it first: bare 'python' on Windows may be the Microsoft Store
REM  stub, which opens the Store instead of running anything.
where py >nul 2>&1 && set PY=py
if not defined PY (
  where python >nul 2>&1 && set PY=python
)

if not defined PY goto :nopython

REM  Make sure it really runs (catches the Store stub).
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython

if not exist .env copy .env.example .env >nul

echo.
echo   Lunch picker is starting.
echo.
echo   Open this in your browser:   http://localhost:%PORT%
echo.
echo   KEEP THIS WINDOW OPEN. Closing it stops the app.
echo   Press Ctrl+C to stop.
echo.

start "" /b powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:%PORT%'" >nul 2>&1

%PY% -m app
echo.
echo   The app has stopped.
pause
exit /b 0

:nopython
echo.
echo   [X] Python 3.10 or newer was not found.
echo.
echo       1. Go to  https://www.python.org/downloads/
echo       2. Download and run the installer
echo       3. IMPORTANT: tick "Add python.exe to PATH" on the first screen
echo       4. Then double-click this file again
echo.
pause
exit /b 1
