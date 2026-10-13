"""
deps.py — shared helpers for the routers: templates, slug guards, safe file lookup.

Every path that comes off the wire goes through here. A slug is a client file stem
and an output filename is a leaf name; both are checked against a strict pattern AND
re-checked for containment after resolution, so a crafted URL cannot read or write
outside clients/ and output/.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from client_context import client_path, load_client
from paths import (BUNDLED, CLIENTS_DIR, OUTPUT_DIR, RESERVED_CLIENT_STEMS, ROOT,
                   contains)

# Read-only and shipped inside the executable when frozen.
TEMPLATES_DIR = BUNDLED / "webapp" / "templates"
STATIC_DIR = BUNDLED / "webapp" / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def url_for(request, name: str, **params) -> str:
    """url_for that percent-encodes its path params, and versions static files.

    Starlette substitutes params raw, which produces a malformed URL for an existing
    client file whose stem contains a space (e.g. "Jane D.yaml"). Routing decodes
    on the way back in, so the slug arrives unchanged. Slashes stay safe so
    url_for("static", path="/app.css") still works.
    """
    encoded = {k: quote(str(v), safe="/") if isinstance(v, str) else v
               for k, v in params.items()}
    url = str(request.url_for(name, **encoded))
    if name == "static":
        url += _asset_version(str(params.get("path") or ""))
    return url


def _asset_version(path: str) -> str:
    """?v=<mtime> so an edited stylesheet or script is never served from cache.

    Without it the app can keep running a stale copy of its own JS after an update —
    which looks exactly like a bug that won't die.
    """
    try:
        stat = (STATIC_DIR / path.lstrip("/")).stat()
        return f"?v={int(stat.st_mtime)}"
    except OSError:
        return ""


@pass_context
def _template_url_for(context, name: str, **params) -> str:
    return url_for(context["request"], name, **params)


# Override Starlette's non-encoding default for every template.
templates.env.globals["url_for"] = _template_url_for

# Client file stems. Permissive enough for a hand-named file like "Jane D",
# strict enough to exclude separators, "..", and leading dots.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")

# Generated artifacts we will hand back: "<form_key>__<role>.pdf" or the worklist.
OUTPUT_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}\.(pdf|xlsx)$")


def clean_slug(slug: str) -> str:
    """Validate a client slug from the URL, or 404."""
    slug = (slug or "").strip()
    if not SLUG_RE.match(slug) or ".." in slug or slug in RESERVED_CLIENT_STEMS:
        raise HTTPException(status_code=404, detail="No such client.")
    if not contains(CLIENTS_DIR, client_path(slug)):
        raise HTTPException(status_code=404, detail="No such client.")
    return slug


def get_client(slug: str) -> tuple[str, dict]:
    """Load clients/<slug>.yaml or 404. Returns (slug, client dict)."""
    slug = clean_slug(slug)
    path = client_path(slug)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such client.")
    try:
        return slug, load_client(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read that client file: {exc}")


def output_file(client: dict, filename: str) -> Path:
    """Resolve a generated file inside this client's output folder, or 404.

    Guards twice: the name must look like something we generated, and the resolved
    path must still sit inside output/.
    """
    from fill_forms import output_dir  # local import keeps deps import-light

    if not OUTPUT_FILE_RE.match(filename or ""):
        raise HTTPException(status_code=404, detail="No such file.")
    path = output_dir(client) / filename
    if not contains(OUTPUT_DIR, path) or not path.is_file():
        raise HTTPException(status_code=404, detail="No such file.")
    return path


def reveal(path) -> None:
    """Open the folder holding a file, with the file selected where possible.

    One implementation for both routers: a platform quirk fixed here is fixed
    everywhere. Nothing here is essential — a file manager that won't open must
    never break the page that called it.
    """
    import subprocess
    import sys

    try:
        path = Path(path)
        if sys.platform == "win32":
            # Explorer's /select, needs the path QUOTED, and a list argument gets
            # quoted in a way it won't parse — with a space anywhere in the path it
            # silently opens Documents instead. A single command-line string is the
            # form that works. No shell is involved, and the caller has already
            # confirmed the path; '"' can't appear in it.
            if '"' in str(path):
                return
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass


def optout_command(slug: str) -> list[str]:
    """How to start the Opt-out Assistant (optout_panel.py) for this client.

    Frozen, it is this same executable in another mode (webapp/desktop.py
    --optout), so the no-Python build ships one program, not two. From source it is
    the panel script, under pythonw where there is one so no console opens.
    """
    import sys

    client = f"{slug}.yaml"                    # the panel picks it by name
    if getattr(sys, "frozen", False):
        return [sys.executable, "--optout", "--client", client]
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    python = windowless if windowless.is_file() else exe
    return [str(python), str(ROOT / "optout_panel.py"), "--client", client]


def launch_optout(slug: str) -> bool:
    """Start the Opt-out Assistant beside the app. False if it couldn't be started.

    A separate process with its own window: it drives a web browser, which the app's
    own window must never do (see docs/decisions.md D4). It outlives this page and
    this app — closing the toolkit doesn't close a half-done opt-out session.
    """
    import subprocess

    try:
        subprocess.Popen(optout_command(slug), cwd=str(ROOT))
        return True
    except Exception:  # noqa: BLE001 — the page reports it; nothing else depends on it
        return False


def collector() -> tuple[list, callable]:
    """A `report` sink for the engine.

    The CLI passes print(); the web app passes this so progress lines — which carry
    client names and output paths — are shown once in the response and never written
    to a server log.
    """
    lines: list[str] = []
    return lines, lines.append
