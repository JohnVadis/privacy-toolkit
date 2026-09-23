"""
documents.py — the Documents screen: import a Clerk record list, review it, fix it.

A thin wrapper, like the rest of webapp/: parsing, merging and storing all live in
the engine's documents.py, so a list imported here is the same `documents:` block
`python import_documents.py` writes, and the CLI fills from it identically.

Importing is a two-step — parse and show, then save — because the rows land on a
government form that says only the documents it names get redacted. The worker sees
what will be written before anything is written.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import documents as docs
from client_context import client_path, dump_client, person_roles
from fill_forms import list_mappings
from validate_client import validate_client, warnings
from webapp.deps import get_client, templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()

# How much pasted text to accept. A county search result is a few KB; anything
# hugely larger is a mistake, and refusing it is cheaper than parsing it.
MAX_IMPORT_BYTES = 2_000_000


def _page(request: Request, slug: str, client: dict, **extra):
    rows = docs.documents(client)
    issues = [i for i in warnings(validate_client(client)) if i.label == "Documents"]
    context = {
        "request": request,
        "nav": "clients",
        "slug": slug,
        "display_name": (client.get("case") or {}).get("display_name") or slug,
        "rows": list(enumerate(rows)),
        "counts": docs.counts(client),
        "lists": docs.LISTS,
        "roles": [r for r in person_roles(client) if r],
        "capacities": _capacities(),
        "warnings": issues,
        "preview": None,
        "error": "",
        "done": "",
    }
    context.update(extra)
    return templates.TemplateResponse(request=request, name="documents.html",
                                      context=context)


def _capacities() -> list[dict]:
    """What each mapped form's tables hold — so the screen can say 4, not 'some'.

    One entry per form, listing its tables, because a form with both a redaction and
    a release table would otherwise be named twice in the same sentence.
    """
    out = []
    for m in list_mappings():
        tables = [{"list": t.get("list", docs.DEFAULT_LIST),
                   "rows": int(t.get("rows") or 0),
                   "attachment": t.get("attachment", "A")}
                  for t in m.get("tables") or []]
        if tables:
            out.append({"form_key": m["form_key"], "tables": tables})
    return out


def _chosen(form, count: int) -> list[int]:
    """The ticked row positions, in order, ignoring anything out of range."""
    seen, out = set(), []
    for value in form.getlist("pick"):
        try:
            i = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= i < count and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _save(slug: str, client: dict, rows: list[dict]) -> None:
    """Write the client file with these documents. Same path save_client uses."""
    client = docs.set_documents(client, rows)
    client_path(slug).write_text(dump_client(client), encoding="utf-8")


@router.get("/clients/{slug}/documents", response_class=HTMLResponse, name="documents_page")
async def documents_page(request: Request, slug: str, added: int = 0, updated: int = 0,
                         removed: int = 0):
    slug, client = get_client(slug)
    done = []
    if added:
        done.append(f"{added} document(s) added")
    if updated:
        done.append(f"{updated} updated")
    if removed:
        done.append(f"{removed} removed")
    return _page(request, slug, client, done=", ".join(done))


@router.post("/clients/{slug}/documents/import", response_class=HTMLResponse,
             name="documents_import")
async def documents_import(request: Request, slug: str):
    """Step 1 parses and shows; step 2 (`save`) writes. Nothing is stored in between."""
    slug, client = get_client(slug)
    form = await request.form()

    listed = str(form.get("list") or docs.DEFAULT_LIST)
    if listed not in docs.LISTS:
        raise HTTPException(status_code=400, detail="Unknown document list.")
    persons = [p for p in form.getlist("persons") if p]
    replace = str(form.get("replace") or "") in ("1", "true", "on", "yes")
    text = str(form.get("text") or "")

    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        raw = await upload.read()
        if len(raw) > MAX_IMPORT_BYTES:
            return _page(request, slug, client,
                         error="That file is too big to be a record list.")
        try:
            text = docs.decode(raw)
        except docs.DocumentImportError as exc:
            return _page(request, slug, client, error=str(exc))

    if len(text.encode("utf-8", "ignore")) > MAX_IMPORT_BYTES:
        return _page(request, slug, client, error="That's too much text to import at once.")

    try:
        parsed = docs.parse(text, default_list=listed)
    except docs.DocumentImportError as exc:
        return _page(request, slug, client, error=str(exc))

    parsed_rows = parsed["rows"]
    if not parsed_rows:
        return _page(request, slug, client,
                     error="No row had an instrument number or a book and page.")

    saving = str(form.get("action") or "") == "save"
    # Step 2 posts back the same text plus the rows that were ticked. Re-parsing and
    # selecting by position keeps the chosen set exact without stashing rows in a
    # session — there isn't one, and the party names used to choose them are not
    # something to keep lying around.
    chosen = _chosen(form, len(parsed_rows)) if saving else None
    new = [parsed_rows[i] for i in chosen] if chosen is not None else parsed_rows
    if saving and not new:
        return _page(request, slug, client,
                     error="No documents were ticked, so nothing was imported.")
    for row in new:
        if persons:
            row["persons"] = persons

    existing = docs.documents(client)
    kept_other = [r for r in existing if r.get("list") != listed]
    kept_same = [] if replace else [r for r in existing if r.get("list") == listed]
    merged = docs.merge(kept_same, new)

    if not saving:
        on_file = {docs.key(r) for r in kept_same}
        return _page(request, slug, client, preview={
            "rows": [{"i": i, "row": r, "known": docs.key(r) in on_file}
                     for i, r in enumerate(parsed_rows)],
            "count": len(parsed_rows),
            "columns": parsed["columns"], "ignored": parsed["ignored"],
            "skipped": parsed["skipped"], "list": listed, "persons": persons,
            "replace": replace, "text": text,
            "has_parties": any(r.get("grantor") or r.get("grantee") for r in parsed_rows),
        })

    combined = sorted(kept_other + merged["rows"],
                      key=lambda r: docs.LISTS.index(r.get("list", docs.DEFAULT_LIST)))
    _save(slug, client, combined)
    return RedirectResponse(
        url_for(request, "documents_page", slug=slug)
        + f"?added={merged['added']}&updated={merged['updated']}", status_code=303)


@router.post("/clients/{slug}/documents", response_class=HTMLResponse, name="documents_save")
async def documents_save(request: Request, slug: str):
    """Save the edits made in the table: which list a row is on, and which rows to drop."""
    slug, client = get_client(slug)
    form = await request.form()
    posted = parse_nested(form.multi_items()).get("rows") or []
    stored = docs.documents(client)

    kept = []
    removed = 0
    for i, row in enumerate(stored):
        edit = posted[i] if i < len(posted) and isinstance(posted[i], dict) else {}
        if str(edit.get("remove") or "") in ("1", "true", "on", "yes"):
            removed += 1
            continue
        listed = str(edit.get("list") or row.get("list") or docs.DEFAULT_LIST)
        kept.append({**row, "list": listed if listed in docs.LISTS else docs.DEFAULT_LIST})

    kept.sort(key=lambda r: docs.LISTS.index(r.get("list", docs.DEFAULT_LIST)))
    _save(slug, client, kept)
    return RedirectResponse(
        url_for(request, "documents_page", slug=slug) + f"?removed={removed}",
        status_code=303)
