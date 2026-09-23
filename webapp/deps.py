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
from paths import (BUNDLED, CLIENTS_DIR, OUTPUT_DIR, RESERVED_CLIENT_STEMS,
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


def collector() -> tuple[list, callable]:
    """A `report` sink for the engine.

    The CLI passes print(); the web app passes this so progress lines — which carry
    client names and output paths — are shown once in the response and never written
    to a server log.
    """
    lines: list[str] = []
    return lines, lines.append
