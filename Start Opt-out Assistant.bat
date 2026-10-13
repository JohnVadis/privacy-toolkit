@echo off
REM ===========================================================================
REM  Opt-out Assistant - double-click to start
REM
REM  Opens a small window beside your web browser. Pick the client, tick the
REM  sites, and it fills in each site's opt-out form for you. You check the
REM  form and click Submit yourself - nothing is ever sent for you.
REM
REM  Needs "Start Privacy Toolkit.bat" to have been run once (it sets up Python).
REM  The first time, this also downloads one extra part (needs internet).
REM ===========================================================================

title Opt-out Assistant
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
set "STAMP=%~dp0.venv\.optout-installed"
set "REQS=%~dp0requirements-optout.txt"

if not exist "%VENV_PY%" goto :no_toolkit

REM Paths go in as arguments, not pasted into the code: a folder name with an
REM apostrophe in it (O'Brien) would otherwise break the check every time.
"%VENV_PY%" -c "import hashlib,pathlib,sys; s=pathlib.Path(sys.argv[1]); r=pathlib.Path(sys.argv[2]); sys.exit(0 if s.is_file() and s.read_text().strip()==hashlib.sha256(r.read_bytes()).hexdigest() else 1)" "%STAMP%" "%REQS%" 2>nul
if %errorlevel% equ 0 goto :run

echo.
echo   Setting up the Opt-out Assistant (one time, needs internet)...
echo.
"%VENV_PY%" -m pip install --quiet -r "%REQS%"
if %errorlevel% neq 0 goto :install_failed
"%VENV_PY%" -c "import hashlib,pathlib,sys; pathlib.Path(sys.argv[1]).write_text(hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest())" "%STAMP%" "%REQS%"

:run
REM The window runs without a console (pythonw), where a start-up error would show
REM nothing at all. Load it here first, where an error can still be read.
"%VENV_PY%" -c "import tkinter, playwright.sync_api, optout_panel"
if %errorlevel% neq 0 goto :wont_start
start "" "%VENV_PYW%" optout_panel.py
exit /b 0

:wont_start
echo.
echo   PROBLEM: the Opt-out Assistant can't start - the message above says why.
echo   Delete the file .venv\.optout-installed and double-click this again to
echo   re-install its parts. If it still fails, send a picture of this window.
echo.
pause
exit /b 1

:no_toolkit
echo.
echo   Please double-click "Start Privacy Toolkit.bat" first - it sets up
echo   Python for both programs. Then double-click this file again.
echo.
pause
exit /b 1

:install_failed
echo.
echo   PROBLEM: could not download the part it needs.
echo   Check your internet connection and double-click this file again.
echo.
pause
exit /b 1
