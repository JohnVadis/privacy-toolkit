@echo off
REM ===========================================================================
REM  Privacy Removal Toolkit - setup and launcher
REM
REM  Double-click this the FIRST time. It will:
REM    1. build a private Python environment (needs internet, once, ~40s)
REM    2. put a "Privacy Toolkit" icon on the Desktop and in the Start menu
REM    3. open the app
REM
REM  After that, just use the Desktop icon - it opens straight into the app
REM  with no black window. Come back to this file only if something breaks;
REM  it repairs the installation and shows any error.
REM
REM  The app never touches the internet. It runs entirely on this PC, on a
REM  private address with a key that changes every time it starts, so no web
REM  page and no other program can reach it. Closing the app window shuts
REM  everything down.
REM ===========================================================================

title Privacy Removal Toolkit
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0.venv\Scripts\pythonw.exe"
set "STAMP=%~dp0.venv\.requirements-installed"
set "ICON=%~dp0webapp\static\toolkit.ico"

echo.
echo   Privacy Removal Toolkit
echo   ===============================================================
echo.

if exist "%VENV_PY%" goto :check_deps


REM --- first run: find a Python to build the environment with ----------------
echo   [1/3] Setting up Python (first run only)...
echo.

set "BOOTSTRAP="
where py >nul 2>&1
if %errorlevel% equ 0 (
    set "BOOTSTRAP=py -3"
    goto :build_venv
)
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "BOOTSTRAP=python"
    goto :build_venv
)
goto :no_python

:build_venv
%BOOTSTRAP% -m venv "%~dp0.venv"
if not exist "%VENV_PY%" goto :venv_failed


REM --- install dependencies, but only when they have actually changed --------
:check_deps
"%VENV_PY%" -c "import hashlib,pathlib,sys; s=pathlib.Path(r'%STAMP%'); r=pathlib.Path(r'%~dp0requirements.txt'); sys.exit(0 if s.is_file() and s.read_text().strip()==hashlib.sha256(r.read_bytes()).hexdigest() else 1)" 2>nul
if %errorlevel% equ 0 goto :shortcuts

echo   [2/3] Installing the parts it needs (needs internet, one time)...
echo.
"%VENV_PY%" -m pip install --quiet --upgrade pip
"%VENV_PY%" -m pip install --quiet -r "%~dp0requirements.txt"
if %errorlevel% neq 0 goto :install_failed

"%VENV_PY%" -c "import hashlib,pathlib; pathlib.Path(r'%STAMP%').write_text(hashlib.sha256(pathlib.Path(r'%~dp0requirements.txt').read_bytes()).hexdigest())"
echo   Done.
echo.


REM --- make / refresh the Desktop and Start menu shortcuts --------------------
:shortcuts
echo   [3/3] Putting a "Privacy Toolkit" icon on your Desktop...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @($w.SpecialFolders('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $dir 'Privacy Toolkit.lnk'));" ^
  "  $s.TargetPath = '%VENV_PYW%';" ^
  "  $s.Arguments = '-m webapp.desktop';" ^
  "  $s.WorkingDirectory = '%~dp0';" ^
  "  $s.IconLocation = '%ICON%';" ^
  "  $s.Description = 'Privacy Removal Toolkit';" ^
  "  $s.Save() }" >nul 2>&1
if %errorlevel% neq 0 echo   (Could not create the shortcut - you can still use this file.)
echo.


REM --- run it ---------------------------------------------------------------
echo   Starting... the app window will open in a moment.
echo   Next time, just double-click "Privacy Toolkit" on your Desktop.
echo.
"%VENV_PY%" -m webapp.desktop
if %errorlevel% neq 0 goto :run_failed
exit /b 0


REM --- failure paths --------------------------------------------------------
:run_failed
echo.
echo   PROBLEM: the app window could not open.
echo.
echo   You can still run it in a browser instead:
echo       .venv\Scripts\python.exe -m webapp.main --open-browser
echo.
pause
exit /b 1

:no_python
echo   PROBLEM: Python is not installed on this PC.
echo.
echo   1. Go to  https://www.python.org/downloads/
echo   2. Download and run the installer.
echo   3. IMPORTANT: tick "Add python.exe to PATH" on the first screen.
echo   4. Double-click this file again.
echo.
pause
exit /b 1

:venv_failed
echo   PROBLEM: could not set up Python for this app.
echo.
echo   Delete the ".venv" folder next to this file, then try again.
echo.
pause
exit /b 1

:install_failed
echo.
echo   PROBLEM: could not download the parts it needs.
echo.
echo   This step needs an internet connection. Check your connection and
echo   double-click this file again. If you are on a work network, ask IT
echo   for the proxy settings for "pip".
echo.
pause
exit /b 1
