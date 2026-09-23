"""
paths.py — one place that knows where the repo lives.

The CLI is always run from the repo root, so relative paths used to be fine.
The web app is a long-lived server process whose CWD is not guaranteed, so every
path is anchored to this file's directory instead. Import these rather than
writing "output/..." or "forms/mappings/..." by hand.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
import re

# Point the toolkit at a different data directory. The test suite sets this so it
# can exercise the real save/delete/fill paths against a sandbox — never against
# someone's actual clients/ and output/.
HOME_ENV = "PRIVACY_TOOLKIT_HOME"


def _root() -> Path:
    """Where the toolkit's own files live.

    Frozen into an .exe, that is the folder holding the .exe — NOT the temp folder
    PyInstaller unpacks into, which is deleted on exit and would take every client
    file with it. Running from source it is this file's folder, as before.
    """
    override = os.environ.get(HOME_ENV)
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled() -> Path:
    """Where read-only files that ship inside the .exe live (templates, static)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else _root()


ROOT = _root()
BUNDLED = _bundled()
CLIENTS_DIR = ROOT / "clients"
OUTPUT_DIR = ROOT / "output"
MAPPINGS_DIR = ROOT / "forms" / "mappings"
BLANK_PDF_DIR = ROOT / "forms" / "_blank_pdfs"
SITES_FILE = ROOT / "sites.yaml"

# Deleting a client moves their file and generated PDFs here rather than unlinking.
# A misclick shouldn't destroy an engagement's paperwork; emptying this folder is a
# deliberate, separate act. Gitignored, like clients/ and output/.
TRASH_DIR = ROOT / "trash"

# Client files the toolkit should never treat as a real client.
RESERVED_CLIENT_STEMS = {"_TEMPLATE"}


def resolve(p) -> Path:
    """Resolve a possibly-relative path (e.g. a mapping's `pdf:`) against the repo root."""
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


def slugify(s: str) -> str:
    """Lowercase a-z0-9 with single underscores.

    Can return "" — "..." has nothing to keep. Callers that build a PATH from this
    must use `safe_dir_name()` instead, which never does.
    """
    return re.sub(r"[^a-z0-9]+", "_", (s or "client").lower()).strip("_")


# Names Windows refuses to use for a directory, whatever the extension.
_RESERVED_NAMES = ({"con", "prn", "aux", "nul"}
                   | {f"com{i}" for i in range(1, 10)}
                   | {f"lpt{i}" for i in range(1, 10)})

# Characters that are safe in a folder name on every platform we target.
_SAFE_DIR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")


def safe_dir_name(name: str) -> str:
    """A folder name that is non-empty, legal, and never a reserved device name.

    Used for the per-client output folder. It must never return "" — an empty
    segment makes `OUTPUT_DIR / name` resolve to OUTPUT_DIR ITSELF, which turned a
    single client's delete into a move of every client's paperwork.
    """
    name = (name or "").strip().strip(". ")          # Windows rejects both trailing
    if not _SAFE_DIR.match(name):
        name = slugify(name)
    if not name:
        name = "client"
    if name.lower() in _RESERVED_NAMES:
        name += "_"
    return name


def client_output_dir(name: str) -> Path:
    """output/<name>/ for one client, guaranteed to be a real subfolder of output/.

    Raises rather than returning something surprising: every caller either writes
    files here, deletes the folder, or moves it to trash, and each of those is
    destructive if the path is wrong.
    """
    path = OUTPUT_DIR / safe_dir_name(name)
    if path.resolve() == OUTPUT_DIR.resolve() or not contains(OUTPUT_DIR, path):
        raise ValueError(f"refusing to use {path!s} as a client output folder")
    return path


def contains(parent: Path, child: Path) -> bool:
    """True if `child` resolves to something inside `parent`. Path-traversal guard."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False
