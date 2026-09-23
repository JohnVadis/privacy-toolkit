"""
paths.py — one place that knows where the repo lives.

The CLI is always run from the repo root, so relative paths used to be fine.
The web app is a long-lived server process whose CWD is not guaranteed, so every
path is anchored to this file's directory instead. Import these rather than
writing "output/..." or "forms/mappings/..." by hand.
"""
from __future__ import annotations
import sys
from pathlib import Path
import re

def _root() -> Path:
    """Where the toolkit's own files live.

    Frozen into an .exe, that is the folder holding the .exe — NOT the temp folder
    PyInstaller unpacks into, which is deleted on exit and would take every client
    file with it. Running from source it is this file's folder, as before.
    """
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
    """Lowercase a-z0-9 with single underscores. Used for output dir names."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "client").lower()).strip("_")


def contains(parent: Path, child: Path) -> bool:
    """True if `child` resolves to something inside `parent`. Path-traversal guard."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False
