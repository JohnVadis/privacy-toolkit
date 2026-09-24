"""
feedback.py — take a comment about whatever is on screen, with a marked-up screenshot.

The app captures, annotates and saves locally. It then OPENS the user's mail client
with the comment filled in; it never sends anything itself, and there is no route here
that talks to a mail server. That is the same human gate the rest of the toolkit uses:
prepare it, a person presses send.
"""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

import feedback as feedback_engine
from webapp.deps import reveal, templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_COMMENT = 4000

# A full-screen Retina capture, base64'd, runs to tens of megabytes. This is a
# local app talking to itself, so the number only needs to be bigger than a
# screenshot can plausibly be.
MAX_IMAGE_BYTES = 64 * 1024 * 1024

# The capture waiting to be annotated. One at a time, in memory, never on disk until
# the user saves it — a screenshot of this app is client data.
_pending: dict[str, bytes] = {}


@router.post("/feedback/capture", name="feedback_capture")
async def feedback_capture(request: Request):
    """Grab the window as it looks right now, before navigating away from it."""
    try:
        _pending["png"] = feedback_engine.capture()
    except feedback_engine.ScreenPermissionNeeded:
        # Better to say this than to attach a picture of the desktop and let
        # someone believe they sent a picture of the app.
        raise HTTPException(
            status_code=503,
            detail="macOS needs permission before this app can photograph its own "
                   "window. Allow it under System Settings > Privacy & Security > "
                   "Screen Recording, then quit and reopen the app and try again.")
    except Exception as exc:
        raise HTTPException(status_code=503,
                            detail=f"Could not take a screenshot: {exc}")
    return Response(status_code=204, headers=NO_STORE)


@router.get("/feedback", response_class=HTMLResponse, name="feedback_page")
async def feedback_page(request: Request, sent: str = ""):
    return templates.TemplateResponse(
        request=request, name="feedback.html",
        context={
            "nav": "",
            "has_capture": "png" in _pending,
            "to": feedback_engine.FEEDBACK_TO,
            "sent": sent,
        },
    )


@router.get("/feedback/image", name="feedback_image")
async def feedback_image():
    png = _pending.get("png")
    if not png:
        raise HTTPException(status_code=404, detail="No screenshot has been taken yet.")
    return Response(content=png, media_type="image/png", headers=NO_STORE)


@router.post("/feedback/send", response_class=HTMLResponse, name="feedback_send")
async def feedback_send(request: Request):
    """Save the marked-up picture, then open the mail client with the comment in it."""
    # A screenshot arrives as one base64 field, and Starlette caps a field at 1 MB
    # by default — so every real capture was rejected before this function ran.
    # Raised only here; every other form in the app keeps the default.
    async with request.form(max_part_size=MAX_IMAGE_BYTES) as form:
        data = parse_nested(form.multi_items())
    comment = str(data.get("comment") or "").strip()[:MAX_COMMENT]
    if not comment:
        raise HTTPException(status_code=400, detail="Write a comment first.")

    png = _decode_image(str(data.get("image") or "")) or _pending.get("png")
    saved = feedback_engine.save(png, comment) if png else None

    url = feedback_engine.compose_url(comment, saved)
    opened = _open_externally(url)
    if saved:
        reveal(saved)
    _pending.pop("png", None)

    return templates.TemplateResponse(
        request=request, name="feedback.html",
        context={
            "nav": "", "has_capture": False, "to": feedback_engine.FEEDBACK_TO,
            "sent": "ok" if opened else "manual",
            "saved_name": saved.name if saved else "",
            "saved_dir": str(saved.parent) if saved else "",
            "compose_url": url,
        },
    )


def _decode_image(raw: str) -> bytes | None:
    """The annotated PNG comes back as a data URL from the canvas."""
    if not raw.startswith("data:image/png;base64,"):
        return None
    try:
        png = base64.b64decode(raw.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not png.startswith(b"\x89PNG") or len(png) > MAX_IMAGE_BYTES:
        return None
    return png


def _open_externally(url: str) -> bool:
    """Hand the URL to the user's browser. The app makes no request itself."""
    try:
        import webbrowser

        return bool(webbrowser.open(url))
    except Exception:
        return False
