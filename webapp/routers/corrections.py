"""
corrections.py — fix a generated form by clicking the spot and typing the value.

A correction is stored on the CLIENT, not on the produced PDF, and re-applied every
time that form is generated (see fill_forms.apply_corrections). Annotating the output
file instead would be silently undone by the next Generate.

Same shape as a mapping entry — page, x, y, and text — so the engine that stamps a
mapping stamps these too. Nothing new gets invented, and nothing here submits anything.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import pdf_render
from client_context import save_client
from fill_forms import (client_corrections, fill_one, form_key_for, form_layout,
                        load_mapping, mapping_paths, split_output_name)
from webapp.deps import get_client, output_file, templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer"}
MAX_TEXT = 200


def _mapping_for(form_key: str):
    for m in mapping_paths():
        if form_key_for(m) == form_key:
            return m, load_mapping(m)
    raise HTTPException(status_code=404, detail="No mapping for that form.")


@router.get("/clients/{slug}/text/{filename}", response_class=HTMLResponse, name="edit_text")
async def edit_text(request: Request, slug: str, filename: str, page: int = 1,
                    saved: int = 0):
    """Edit the form on the form: every value and every tick box, in place.

    The background is the BLANK page, with a control drawn over each spot the engine
    writes to. Showing the filled page instead would put the original text underneath
    every input.
    """
    slug, client = get_client(slug)
    path = output_file(client, filename)
    form_key, person = split_output_name(path.name)
    _, mapping = _mapping_for(form_key)
    if mapping.get("mode") != "overlay":
        raise HTTPException(status_code=400,
                            detail="In-place editing is for overlay forms.")

    items = form_layout(mapping, client, person or None, report=lambda *_: None)
    try:
        pages = pdf_render.page_count(mapping["pdf"])
    except Exception:
        pages = max((i["page"] for i in items), default=1)
    page = max(1, min(page, pages))
    w_pt, h_pt = pdf_render.page_size(mapping["pdf"], page - 1)

    return templates.TemplateResponse(
        request=request, name="edit_text.html",
        context={
            "nav": "clients",
            "slug": slug,
            "display_name": (client.get("case") or {}).get("display_name") or slug,
            "filename": path.name,
            "form_key": form_key,
            "person": person,
            "title": mapping.get("title") or form_key,
            "items": items,
            "on_page": [i for i in items if i["page"] == page],
            "off_page": [i for i in items if i["page"] != page],
            "added": _numbered(client, form_key, person),
            "page": page,
            "page_count": pages,
            "w_pt": w_pt,
            "h_pt": h_pt,
            "saved": bool(saved),
        },
    )


@router.post("/clients/{slug}/text/{filename}", name="edit_text_save")
async def edit_text_save(request: Request, slug: str, filename: str):
    slug, client = get_client(slug)
    path = output_file(client, filename)
    form_key, person = split_output_name(path.name)
    _, mapping = _mapping_for(form_key)
    overlay = mapping.get("mode") == "overlay"

    data = parse_nested((await request.form()).multi_items())
    rows = data.get("rows") or []
    if isinstance(rows, dict):
        rows = list(rows.values())

    kept, ticks = [], []
    for row in rows:
        if not isinstance(row, dict) or "entry" not in row:
            continue
        try:
            entry = int(row["entry"]) if overlay else str(row["entry"])
        except (TypeError, ValueError):
            continue

        if str(row.get("kind")) == "mark":
            # Only record a tick that disagrees with what the mapping decided,
            # so the mapping keeps driving every box the worker didn't touch.
            on = str(row.get("on", "")).lower() in ("1", "true", "on", "yes")
            by_rule = str(row.get("by_rule", "")).lower() in ("1", "true", "on", "yes")
            if on != by_rule:
                item = {"entry": entry, "on": on}
                if person:
                    item["person"] = person
                ticks.append(item)
            continue

        value = str(row.get("value", ""))[:MAX_TEXT]
        if value == str(row.get("original", "")):
            continue                       # unchanged — don't store an override
        item = {"entry": entry, "text": value}
        if row.get("from"):
            # Remembered so a later mapping edit can't move this onto another field.
            item["from"] = str(row["from"])
        if person:
            item["person"] = person
        kept.append(item)

    _set_or_clear(client, "overrides", form_key, kept)
    _set_or_clear(client, "marks", form_key, ticks)

    # Anything the worker added on a blank part of the page.
    added = data.get("added") or []
    if isinstance(added, dict):
        added = list(added.values())
    extras = []
    for a in added:
        if not isinstance(a, dict):
            continue
        try:
            item = {"page": max(1, int(float(a.get("page", 1)))),
                    "x": round(float(a["x"]), 1), "y": round(float(a["y"]), 1)}
        except (KeyError, TypeError, ValueError):
            continue
        text = str(a.get("text", ""))[:MAX_TEXT]
        if str(a.get("kind")) == "mark":
            item["mark"] = "X"
        elif text.strip():
            item["text"] = text
        else:
            continue
        if person:
            item["person"] = person
        extras.append(item)
    _set_or_clear(client, "corrections", form_key, extras)

    save_client(slug, client)
    _regenerate(client, form_key, person)
    return RedirectResponse(
        url_for(request, "edit_text", slug=slug, filename=path.name)
        + f"?saved=1&page={data.get('page') or 1}",
        status_code=303)


def _set_or_clear(client: dict, key: str, form_key: str, values: list) -> None:
    """Store values under client[key][form_key], pruning the keys when empty."""
    bucket = client.setdefault(key, {})
    if values:
        bucket[form_key] = values
    else:
        bucket.pop(form_key, None)
    if not bucket:
        client.pop(key, None)


@router.get("/clients/{slug}/pagepng/{filename}/{page}", name="generated_page_png")
async def generated_page_png(slug: str, filename: str, page: int, scale: float = 2.0):
    """One page of a generated PDF as a PNG, for clicking on.

    The page's size in points rides along in headers so the browser can turn a click
    back into a PDF coordinate without shipping the whole document.
    """
    slug, client = get_client(slug)
    path = output_file(client, filename)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="Not a PDF.")
    try:
        data, w_pt, h_pt = pdf_render.page_png(path, max(0, page - 1), scale)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not render that page: {exc}")
    headers = dict(NO_STORE)
    headers["X-Page-Width-Pt"] = f"{w_pt:.2f}"
    headers["X-Page-Height-Pt"] = f"{h_pt:.2f}"
    return Response(content=data, media_type="image/png", headers=headers)


@router.get("/clients/{slug}/correct/{filename}", response_class=HTMLResponse,
            name="correct_form")
async def correct_form(request: Request, slug: str, filename: str, page: int = 1):
    slug, client = get_client(slug)
    path = output_file(client, filename)
    form_key, person = split_output_name(path.name)
    try:
        pages = pdf_render.page_count(path)
    except Exception:
        pages = 1
    page = max(1, min(page, pages))
    return templates.TemplateResponse(
        request=request, name="correct.html",
        context={
            "nav": "clients",
            "slug": slug,
            "display_name": (client.get("case") or {}).get("display_name") or slug,
            "filename": path.name,
            "form_key": form_key,
            "person": person,
            "page": page,
            "page_count": pages,
            "corrections": _numbered(client, form_key, person),
        },
    )


def _numbered(client: dict, form_key: str, person: str) -> list[dict]:
    """Corrections for this form with their index, so one can be removed by position."""
    stored = (client.get("corrections") or {}).get(form_key) or []
    rows = []
    for i, e in enumerate(stored):
        if not isinstance(e, dict):
            continue
        who = e.get("person")
        if who and person and who != person:
            continue
        rows.append({"index": i, "page": e.get("page", 1), "x": e.get("x"),
                     "y": e.get("y"), "text": e.get("text", ""),
                     "person": who or ""})
    return rows


@router.post("/clients/{slug}/correct/{filename}", name="correct_add")
async def correct_add(request: Request, slug: str, filename: str):
    slug, client = get_client(slug)
    path = output_file(client, filename)
    form_key, person = split_output_name(path.name)
    data = parse_nested((await request.form()).multi_items())

    text = str(data.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        raise HTTPException(status_code=400, detail="Type the text to add first.")
    try:
        page = max(1, int(float(data.get("page") or 1)))
        x = round(float(data.get("x")), 1)
        y = round(float(data.get("y")), 1)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Click the spot on the form first.")

    entry = {"page": page, "x": x, "y": y, "text": text}
    if person:
        entry["person"] = person          # keep it to this person's copy
    size = data.get("size")
    if size:
        try:
            entry["size"] = max(4, min(int(float(size)), 24))
        except ValueError:
            pass

    client.setdefault("corrections", {}).setdefault(form_key, []).append(entry)
    save_client(slug, client)
    _regenerate(client, form_key, person)
    return RedirectResponse(
        url_for(request, "correct_form", slug=slug, filename=path.name) + f"?page={page}",
        status_code=303)


@router.post("/clients/{slug}/correct/{filename}/remove", name="correct_remove")
async def correct_remove(request: Request, slug: str, filename: str):
    slug, client = get_client(slug)
    path = output_file(client, filename)
    form_key, person = split_output_name(path.name)
    data = parse_nested((await request.form()).multi_items())

    try:
        index = int(data.get("index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Nothing selected to remove.")

    stored = (client.get("corrections") or {}).get(form_key) or []
    if not 0 <= index < len(stored):
        raise HTTPException(status_code=404, detail="That correction is already gone.")
    stored.pop(index)
    if not stored:
        client["corrections"].pop(form_key, None)
        if not client["corrections"]:
            client.pop("corrections", None)
    save_client(slug, client)
    _regenerate(client, form_key, person)
    return RedirectResponse(
        url_for(request, "correct_form", slug=slug, filename=path.name), status_code=303)


def _regenerate(client: dict, form_key: str, person: str) -> None:
    """Re-run this one form so the page shows the corrected result immediately."""
    for m in mapping_paths():
        if form_key_for(m) == form_key:
            fill_one(m, client, person or None, report=lambda *_: None)
            return
