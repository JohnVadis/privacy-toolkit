#!/bin/bash
# ===========================================================================
#  Privacy Removal Toolkit — setup and launcher (macOS)
#
#  Double-click this the FIRST time. It will:
#    1. build a private Python environment (needs internet, once, ~40s)
#    2. open the app
#
#  Come back to this file only if something breaks; it repairs the
#  installation and shows any error.
#
#  The app never touches the internet. It runs entirely on this Mac, on a
#  private address with a key that changes every time it starts, so no web
#  page and no other program can reach it. Closing the app window shuts
#  everything down.
#
#  If macOS refuses to run this ("cannot be opened because it is from an
#  unidentified developer"), right-click it and choose Open, or run:
#      chmod +x "Start Privacy Toolkit.command"
# ===========================================================================
set -u

cd "$(dirname "$0")" || exit 1

VENV_PY=".venv/bin/python"
STAMP=".venv/.requirements-installed"

echo
echo "  Privacy Removal Toolkit"
echo "  ==============================================================="
echo

pause_and_exit() {
    echo
    echo "  Press Return to close this window."
    read -r _
    exit "${1:-1}"
}

# --- first run: find a Python to build the environment with ------------------
if [ ! -x "$VENV_PY" ]; then
    echo "  [1/3] Setting up Python (first run only)..."
    echo

    BOOTSTRAP=""
    for candidate in python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            BOOTSTRAP="$candidate"
            break
        fi
    done

    if [ -z "$BOOTSTRAP" ]; then
        echo "  Python 3 isn't installed on this Mac."
        echo
        echo "  The quickest fix is to install it from https://www.python.org/downloads/"
        echo "  (the standard installer is fine), then run this file again."
        echo
        echo "  If you have Homebrew, this also works:   brew install python@3.12"
        pause_and_exit 1
    fi

    "$BOOTSTRAP" -m venv .venv
    if [ ! -x "$VENV_PY" ]; then
        echo "  Could not create the private Python environment."
        echo "  Check that '$BOOTSTRAP -m venv' works, then run this file again."
        pause_and_exit 1
    fi
fi

# --- install dependencies, but only when they have actually changed ----------
NEEDED=$("$VENV_PY" - <<'PY'
import hashlib, pathlib, sys
stamp = pathlib.Path(".venv/.requirements-installed")
reqs = pathlib.Path("requirements.txt")
current = hashlib.sha256(reqs.read_bytes()).hexdigest()
print("no" if stamp.is_file() and stamp.read_text().strip() == current else "yes")
PY
)

if [ "$NEEDED" = "yes" ]; then
    echo "  [2/3] Installing what the app needs (once, needs internet)..."
    echo
    "$VENV_PY" -m pip install --quiet --upgrade pip
    if ! "$VENV_PY" -m pip install --quiet -r requirements.txt; then
        echo
        echo "  Could not install the app's dependencies."
        echo "  Check this Mac's internet connection and run this file again."
        pause_and_exit 1
    fi
    "$VENV_PY" -c "import hashlib,pathlib; pathlib.Path('.venv/.requirements-installed').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"
fi

# --- run it ------------------------------------------------------------------
echo "  [3/3] Opening the app..."
echo
echo "  Leave this window alone while you work — closing it closes the app."
echo

if ! "$VENV_PY" -m webapp.desktop; then
    echo
    echo "  The app stopped with an error (above)."
    echo
    echo "  If it mentions a screenshot or Screen Recording, that is only the"
    echo "  Comment feature: macOS asks for permission the first time, under"
    echo "  System Settings > Privacy & Security > Screen Recording."
    pause_and_exit 1
fi
