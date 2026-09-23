"""
forms.py — the form mappings: read-only overview, and an editor for one mapping.

A mapping is written once per county form and then used for EVERY client, so the
editor is built around seeing the result rather than trusting a number:

  * click the blank form to get a coordinate instead of guessing one,
  * render a live preview against a real client before saving,
  * every save is checked structurally and backs up the previous version.

None of that can tell you a coordinate is the RIGHT box — only your eyes can, which is
what the preview and docs/verification-checklist.md are for.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import yaml
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import form_import
import mapping_edit
import pdf_render
from client_context import (CONTEXT_VARIABLES, EXAMPLE_CLIENT, EXAMPLE_SLUG,
                            MARK_CONDITIONS, build_context, list_clients,
                            load_client)
from fill_forms import CATEGORIES, fill_one, list_mappings, output_dir
from paths import BLANK_PDF_DIR, resolve, slugify
from webapp.deps import templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer"}

# Display name used for the throwaway preview client, so its render lands in a
# folder of its own that is deleted immediately afterwards.
PREVIEW_NAME = "_mapping preview"


def _mapping_or_404(form_key: str) -> dict:
    for m in list_mappings():
        if m["form_key"] == form_key:
            return m
    raise HTTPException(status_code=404, detail="No such form.")


@router.get("/forms", response_class=HTMLResponse, name="forms_overview")
async def forms_overview(request: Request):
    mappings = list_mappings()
    return templates.TemplateResponse(
        request=request, name="forms.html",
        context={
            "nav": "forms",
            "mappings": mappings,
            "categories": CATEGORIES,
            "counties": sorted({m["county"] for m in mappings if m["county"]}),
        },
    )


# --------------------------------------------------------------------------
# editing one mapping
# --------------------------------------------------------------------------
@router.get("/forms/{form_key}/edit", response_class=HTMLResponse, name="mapping_edit_page")
async def mapping_edit_page(request: Request, form_key: str, page: int = 1,
                            client: str = "", text: str | None = None):
    meta = _mapping_or_404(form_key)
    try:
        current = text if text is not None else mapping_edit.read_mapping_text(form_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No such form.")

    pages = _page_count(meta)
    _, problems = mapping_edit.check_mapping(current, expect_key=form_key)
    return templates.TemplateResponse(
        request=request, name="mapping_edit.html",
        context={
            "nav": "forms",
            "meta": meta,
            "form_key": form_key,
            "text": current,
            "problems": problems,
            "page": max(1, min(page, pages)),
            "page_count": pages,
            "clients": [r for r in list_clients() if not r["error"]],
            "preview_client": client,
            "backups": [p.name for p in mapping_edit.list_backups(form_key)[:5]],
            "saved": request.query_params.get("saved") == "1",
            "imported": request.query_params.get("imported") == "1",
            "pins": _entry_pins(current, meta, EXAMPLE_CLIENT),
            "size": _page_size(meta, max(1, min(page, pages))),
            "variables": CONTEXT_VARIABLES,
            "conditions": MARK_CONDITIONS,
            "example_slug": EXAMPLE_SLUG,
        },
    )


def _page_size(meta: dict, page: int) -> tuple[float, float]:
    try:
        return pdf_render.page_size(meta["pdf"], page - 1)
    except Exception:
        return (612.0, 792.0)


DEFAULT_SIZE = 10


def _text_extent(value: str, size: float) -> tuple[float, float]:
    """(width, height) in points that `value` will occupy when drawn.

    Measured with the same font the overlay engine uses, so a marker can be drawn as
    the box the text will fill rather than a dot near it.
    """
    try:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        width = stringWidth(value or "", "Helvetica", size)
    except Exception:
        width = len(value or "") * size * 0.5
    return max(width, size * 0.6), size


def _entry_pins(text: str, meta: dict, client: dict | None = None) -> list[dict]:
    """Where every entry in this mapping lands, and how much room it takes.

    Positions are the point the engine draws from: for text that is the start of the
    BASELINE, with the glyphs going up and to the right of it. A marker centred on
    that point sits low and to the left of the value it stands for, which is exactly
    the mismatch this reports honestly instead.
    """
    import pdf_overlay

    ctx = None
    if client is not None:
        try:
            ctx = build_context(client, None)
        except Exception:
            ctx = None

    parsed, _ = mapping_edit.check_mapping(text)
    if not isinstance(parsed, dict) or parsed.get("mode") != "overlay":
        return []
    pdf = parsed.get("pdf")
    if not pdf or not resolve(pdf).is_file():
        return []

    pins = []
    for i, e in enumerate(parsed.get("entries") or []):
        if not isinstance(e, dict):
            continue
        spot = pdf_overlay.entry_position(str(resolve(pdf)), e, report=lambda *_: None)
        if spot is None:
            continue
        page, x, y = spot
        when = e.get("when") if isinstance(e.get("when"), dict) else {}
        size = float(e.get("size", DEFAULT_SIZE))
        if "mark" in e:
            shown = str(e.get("mark") or "X")
        elif ctx is not None:
            shown = str(ctx.get(e["from"], "")) if "from" in e else str(e.get("literal", ""))
        else:
            shown = str(e.get("from") or e.get("literal") or "")
        w_pt, h_pt = _text_extent(shown or "—", size)
        pins.append({
            "index": i, "page": page + 1, "x": float(x), "y": float(y),
            "w": round(w_pt, 1), "h": round(h_pt, 1), "shown": shown,
            "anchored": "anchor" in e,
            "kind": "mark" if "mark" in e else "text",
            "label": _pin_label(e),
            # The whole entry travels to the browser so the properties panel can
            # edit it and send the result back, without the UI guessing its shape.
            "data": {k: v for k, v in e.items() if k in ALLOWED_ENTRY_KEYS},
            "source": ("variable" if "from" in e else
                       "mark" if "mark" in e else "literal"),
            "when_field": str(when.get("field", "")),
            "when_equals": str(when.get("equals", "")),
            "always": e.get("check") is True,
        })
    return pins


def _pin_label(entry: dict) -> str:
    """Short name for a pin: what it writes, or what turns the tick on."""
    from client_context import variable_label

    if "from" in entry:
        return variable_label(str(entry["from"]))
    if "literal" in entry:
        return f"“{str(entry['literal'])[:20]}”"
    when = entry.get("when")
    if isinstance(when, dict):
        return f"{str(when.get('equals', '')).replace('_', ' ')}"
    if entry.get("check") is True:
        return "always ticked"
    return "tick"


def _to_relative(text: str, moves: dict) -> dict:
    """Convert dragged ABSOLUTE positions into the offsets an anchored entry stores.

    An anchored entry keeps its anchor and moves via dx/dy, so dragging one has to be
    expressed relative to wherever that anchor sits on the page.
    """
    import pdf_overlay

    parsed, _ = mapping_edit.check_mapping(text)
    if not isinstance(parsed, dict):
        return moves
    pdf = parsed.get("pdf")
    entries = parsed.get("entries") or []
    if not pdf or not resolve(pdf).is_file():
        return moves

    out = {}
    for i, (x, y) in moves.items():
        e = entries[i] if 0 <= i < len(entries) and isinstance(entries[i], dict) else None
        if not e or "anchor" not in e:
            out[i] = (x, y)
            continue
        base = pdf_overlay.entry_position(
            str(resolve(pdf)), {**e, "dx": 0, "dy": 0}, report=lambda *_: None)
        if base is None:
            # Its anchor text isn't on the page, so there is nothing to be relative
            # to. Writing the absolute position into dx/dy would be nonsense.
            continue
        out[i] = (round(x - base[1], 1), round(y - base[2], 1))
    return out


def _page_count(meta: dict) -> int:
    try:
        return pdf_render.page_count(meta["pdf"]) if meta["pdf_present"] else 1
    except Exception:
        return 1


@router.post("/forms/{form_key}/edit", response_class=HTMLResponse, name="mapping_save")
async def mapping_save(request: Request, form_key: str):
    meta = _mapping_or_404(form_key)
    data = parse_nested((await request.form()).multi_items())
    text = str(data.get("text") or "")

    # Everything the editor changed arrives as one JSON list of operations, so a
    # save is atomic and the order they are applied in is ours to control.
    text, op_problems = _apply_ops(text, str(data.get("ops") or ""))
    if op_problems:
        return _editor_response(request, meta, form_key, text, op_problems, data)

    path, backup, problems = mapping_edit.save_mapping(form_key, text)
    if problems:
        # Nothing was written — hand the text back with the problems listed.
        return _editor_response(request, meta, form_key, text, problems, data)
    page = str(data.get("page") or 1)
    return RedirectResponse(
        url_for(request, "mapping_edit_page", form_key=form_key) + f"?saved=1&page={page}",
        status_code=303)


def _editor_response(request, meta, form_key, text, problems, data):
    """Re-render the editor with the work intact when a save can't go through."""
    return templates.TemplateResponse(
        request=request, name="mapping_edit.html",
        context={
            "nav": "forms", "meta": meta, "form_key": form_key, "text": text,
            "problems": problems, "page": 1, "page_count": _page_count(meta),
            "clients": [r for r in list_clients() if not r["error"]],
            "preview_client": str(data.get("client") or ""),
            "backups": [p.name for p in mapping_edit.list_backups(form_key)[:5]],
            "saved": False, "imported": False,
            "pins": _entry_pins(text, meta, EXAMPLE_CLIENT),
            "size": _page_size(meta, 1),
            "variables": CONTEXT_VARIABLES, "conditions": MARK_CONDITIONS,
            # The template feeds this to |tojson, and Jinja's Undefined is not
            # serializable — leaving it out turned every refused save into a 500,
            # which is precisely when the worker needs the page back with their
            # work and the reason on it.
            "example_slug": EXAMPLE_SLUG,
        },
        status_code=422,
    )


def _apply_ops(text: str, raw: str) -> tuple[str, list[str]]:
    """Apply the editor's operations to the mapping text. Returns (text, problems).

    Order matters and is fixed here rather than trusting the browser: edit and move
    existing entries first (their indexes are still valid), then delete bottom-up,
    then append. Anything malformed stops the save rather than half-applying.
    """
    if not raw.strip():
        return text, []
    try:
        ops = json.loads(raw)
    except ValueError:
        return text, ["The editor sent something unreadable. Reload the page and try again."]
    if not isinstance(ops, list):
        return text, ["The editor sent something unexpected. Reload the page and try again."]

    updates, moves, removes, adds = {}, {}, [], []
    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            if kind == "update":
                updates[int(op["entry"])] = _clean_entry(op.get("entry_data") or {})
            elif kind == "move":
                moves[int(op["entry"])] = (float(op["x"]), float(op["y"]))
            elif kind == "remove":
                removes.append(int(op["entry"]))
            elif kind == "add":
                adds.append(_clean_entry(op.get("entry_data") or {}))
        except (KeyError, TypeError, ValueError):
            return text, ["One of the changes was incomplete, so nothing was saved. "
                          "Reload the page and try again."]

    notes = []
    if updates:
        text, n = mapping_edit.update_entries(text, updates)
        notes += n
    if moves:
        text, n = mapping_edit.move_entries(text, _to_relative(text, moves))
        notes += n
    if removes:
        text, n = mapping_edit.remove_entries(text, removes)
        notes += n
    if adds:
        text, n = mapping_edit.add_entries(text, adds)
        notes += n
    return text, []


ALLOWED_ENTRY_KEYS = {"page", "x", "y", "anchor", "occurrence", "dx", "dy", "size",
                      "from", "literal", "mark", "check", "when"}


def _clean_entry(raw: dict) -> dict:
    """Keep only the keys a mapping entry may carry, with sane types."""
    out = {}
    for key, value in raw.items():
        if key not in ALLOWED_ENTRY_KEYS or value in (None, ""):
            continue
        if key in ("page", "occurrence"):
            out[key] = max(1, int(value))
        elif key in ("x", "y", "dx", "dy"):
            out[key] = round(float(value), 1)
        elif key == "size":
            out[key] = max(4, min(int(value), 36))
        elif key == "check":
            out[key] = bool(value)
        elif key == "when":
            if isinstance(value, dict) and value.get("field") in MARK_CONDITIONS:
                out[key] = {"field": str(value["field"]), "equals": str(value.get("equals", ""))}
        else:
            out[key] = str(value)[:200]
    return out


# --------------------------------------------------------------------------
# images: the blank form to click on, and a live preview of the result
# --------------------------------------------------------------------------
@router.get("/forms/{form_key}/blank/{page}", name="blank_page_png")
async def blank_page_png(form_key: str, page: int, scale: float = 2.0):
    """A page of the BLANK form — what you click to pick a coordinate."""
    meta = _mapping_or_404(form_key)
    if not meta["pdf_present"]:
        raise HTTPException(status_code=404, detail="The blank PDF for this form is missing.")
    try:
        data, w_pt, h_pt = pdf_render.page_png(meta["pdf"], max(0, page - 1), scale)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Could not render that page: {exc}")
    headers = dict(NO_STORE)
    headers["X-Page-Width-Pt"] = f"{w_pt:.2f}"
    headers["X-Page-Height-Pt"] = f"{h_pt:.2f}"
    return Response(content=data, media_type="image/png", headers=headers)


@router.post("/forms/{form_key}/preview/{page}", name="mapping_preview")
async def mapping_preview(request: Request, form_key: str, page: int):
    """Fill the form with the posted (unsaved) mapping for one client, and render it.

    Everything happens in a temp folder and is deleted before returning, so previewing
    never touches forms/mappings/ or output/.
    """
    _mapping_or_404(form_key)
    data = parse_nested((await request.form()).multi_items())
    text = str(data.get("text") or "")
    slug = str(data.get("client") or "")

    # Apply whatever is still unsaved, so the preview shows what Save would produce
    # rather than the last version written to disk.
    text, op_problems = _apply_ops(text, str(data.get("ops") or ""))
    if op_problems:
        raise HTTPException(status_code=400, detail=op_problems[0])

    parsed, problems = mapping_edit.check_mapping(text, expect_key=form_key)
    if problems:
        raise HTTPException(status_code=400, detail=problems[0])

    if slug == EXAMPLE_SLUG:
        client = EXAMPLE_CLIENT
    else:
        row = next((r for r in list_clients() if r["slug"] == slug and not r["error"]), None)
        if not row:
            raise HTTPException(status_code=400, detail="Pick a client to preview against.")
        client = load_client(row["path"])

    tmp = Path(tempfile.mkdtemp(prefix="mapping-preview-"))
    try:
        candidate = tmp / f"{form_key}.map.yaml"
        candidate.write_text(text, encoding="utf-8")
        # A throwaway display name keeps the render out of the client's real folder.
        preview_client = yaml.safe_load(yaml.dump(client))
        preview_client.setdefault("case", {})["display_name"] = PREVIEW_NAME
        out = fill_one(candidate, preview_client, None, report=lambda *_: None)
        if not out:
            raise HTTPException(status_code=400, detail="That mapping produced nothing.")
        png, w_pt, h_pt = pdf_render.page_png(out, max(0, page - 1), 2.0)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not render a preview: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        # Ask the engine where it put the file rather than guessing the slug —
        # guessing left an orphaned output/mapping_preview/ folder behind.
        shutil.rmtree(output_dir({"case": {"display_name": PREVIEW_NAME}}),
                      ignore_errors=True)

    headers = dict(NO_STORE)
    headers["X-Page-Width-Pt"] = f"{w_pt:.2f}"
    headers["X-Page-Height-Pt"] = f"{h_pt:.2f}"
    return Response(content=png, media_type="image/png", headers=headers)


# --------------------------------------------------------------------------
# importing a new form
# --------------------------------------------------------------------------
MAX_PDF_BYTES = 40 * 1024 * 1024


@router.get("/forms/new", response_class=HTMLResponse, name="form_import_page")
async def form_import_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="form_new.html",
        context={"nav": "forms", "stage": "upload", "error": ""},
    )


@router.post("/forms/new", response_class=HTMLResponse, name="form_import_upload")
async def form_import_upload(request: Request, pdf: UploadFile = File(...),
                             form_key: str = Form(""), title: str = Form(""),
                             county: str = Form("")):
    """Take a blank PDF, work out what can be mapped, and show the proposal."""
    raw = await pdf.read()

    def back(message: str):
        # Hand back everything they typed. A browser can't refill a file input, so
        # only the PDF needs choosing again — losing the rest was maddening.
        return templates.TemplateResponse(
            request=request, name="form_new.html",
            context={"nav": "forms", "stage": "upload", "error": message,
                     "form_key": form_key, "title": title, "county": county},
            status_code=400)

    error = _reject_pdf(pdf.filename or "", raw)
    if error:
        return back(error)

    key = slugify(form_key or Path(pdf.filename or "").stem)
    if not key:
        return back("Give this form a short name — the file name didn't provide one.")
    if mapping_edit.mapping_file(key).exists():
        return back(f"A form called '{key}' already exists. Pick a different short "
                    "name, or edit that one from the Forms list.")

    BLANK_PDF_DIR.mkdir(parents=True, exist_ok=True)
    dest = BLANK_PDF_DIR / f"{key}.pdf"
    dest.write_bytes(raw)

    info = form_import.analyze(dest)
    mapping, notes = form_import.suggest(dest, key, title, county)
    text = yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, width=100)
    _, problems = mapping_edit.check_mapping(text, expect_key=key)

    return templates.TemplateResponse(
        request=request, name="form_new.html",
        context={
            "nav": "forms", "stage": "review", "error": "",
            "form_key": key, "title": title, "county": county,
            "pdf_name": dest.name, "info": info, "notes": notes,
            "problems": problems, "text": text,
        },
    )


def _reject_pdf(filename: str, raw: bytes) -> str:
    """Why this upload can't be accepted, or '' if it's fine.

    Judged on CONTENTS, not on the name. The uploaded file name is never used as a
    path — the form is saved as <short name>.pdf and that name is slugified — so
    refusing a file for the characters in its name rejected perfectly good PDFs and
    made people rename things for no reason.
    """
    if not raw:
        return "That file is empty."
    if len(raw) > MAX_PDF_BYTES:
        return (f"That file is {len(raw) / (1024 * 1024):.0f} MB, over the "
                f"{MAX_PDF_BYTES // (1024 * 1024)} MB limit.")
    if not raw.startswith(b"%PDF"):
        return ("That file isn't a PDF. Blank forms have to be PDFs — this one doesn't "
                "start like one, whatever it's called.")
    return ""


@router.post("/forms/new/create", name="form_import_create")
async def form_import_create(request: Request):
    """Write the reviewed proposal as a real mapping, then open it for adjusting."""
    data = parse_nested((await request.form()).multi_items())
    key = slugify(str(data.get("form_key") or ""))
    text = str(data.get("text") or "")
    if not key:
        raise HTTPException(status_code=400, detail="That form needs a name.")

    path, backup, problems = mapping_edit.save_mapping(key, text)
    if problems:
        return templates.TemplateResponse(
            request=request, name="form_new.html",
            context={"nav": "forms", "stage": "review", "error": "",
                     "form_key": key, "title": "", "county": "",
                     "pdf_name": "", "info": None, "notes": [],
                     "problems": problems, "text": text},
            status_code=422)
    return RedirectResponse(
        url_for(request, "mapping_edit_page", form_key=key) + "?imported=1",
        status_code=303)
