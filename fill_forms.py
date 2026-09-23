#!/usr/bin/env python3
"""
fill_forms.py — fill government PDF forms for one client. Zero AI, zero tokens.

    python fill_forms.py --client clients/example_client.yaml
    python fill_forms.py --client clients/example_client.yaml --person spouse
    python fill_forms.py --client clients/example_client.yaml --all-persons
    python fill_forms.py --client clients/example_client.yaml --forms hillsborough_appraiser_confidentiality

Each mapping in forms/mappings/*.map.yaml declares a mode:
  * fillable  -> the PDF has AcroForm fields; values are written into them.
  * overlay   -> the PDF is a flat scan; values are stamped on by coordinate/anchor.
A mapping is written ONCE per county form, then reused for every client. No model runs.

This module is the engine. The CLI below and webapp/ both call the same functions —
`fill_client()` / `fill_one()` — so there is exactly one fill path.

Output is a filled PDF for a HUMAN to review, notarize where required, and submit.
Nothing here submits anything.
"""
from __future__ import annotations
import argparse
import datetime as _dt
import os
from pathlib import Path

import yaml

import documents
import pdf_attachment
import pdf_fill
import pdf_overlay
from client_context import ClientDataError, build_context, load_client, person_roles
from paths import MAPPINGS_DIR, OUTPUT_DIR, ROOT, resolve, slugify

CATEGORIES = ("military", "law_enforcement", "judges")


# --------------------------------------------------------------------------
# mapping discovery
# --------------------------------------------------------------------------
def load_mapping(mapping_path) -> dict:
    with open(resolve(mapping_path), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def mapping_paths() -> list[Path]:
    return sorted(MAPPINGS_DIR.glob("*.map.yaml"))


def form_key_for(mapping_path) -> str:
    return Path(mapping_path).name.split(".")[0]


def list_mappings() -> list[dict]:
    """Summarize every mapping — what the /forms overview and the generate picker show.

    Read-only metadata; touching this never changes how a form is filled.
    """
    out = []
    for p in mapping_paths():
        key = form_key_for(p)
        try:
            m = load_mapping(p)
        except Exception as exc:
            out.append({"form_key": key, "path": p, "error": str(exc), "mode": "",
                        "county": "", "title": "", "categories": [], "pdf": "",
                        "pdf_present": False, "entry_count": 0, "tables": []})
            continue
        pdf_rel = m.get("pdf", "")
        out.append({
            "form_key": m.get("form_key") or key,
            "path": p,
            "error": None,
            "mode": m.get("mode", "fillable"),
            "county": m.get("county") or _county_guess(key),
            "title": m.get("title") or "",
            "categories": _categories_covered(m),
            "pdf": pdf_rel,
            "pdf_present": bool(pdf_rel) and resolve(pdf_rel).exists(),
            "entry_count": len(m.get("entries") or m.get("fields") or []),
            "tables": [t for t in (m.get("tables") or []) if isinstance(t, dict)],
        })
    return out


def _county_guess(form_key: str) -> str:
    """Fallback when a mapping has no `county:` key — infer from the form_key prefix."""
    head = form_key.split("_", 1)[0]
    if head in ("fl", "demo"):
        return "Statewide" if head == "fl" else ""
    return head.capitalize()


def _categories_covered(mapping: dict) -> list[str]:
    """Which statutory categories this mapping has a checkbox rule for.

    Scans `when: {field: exemption_category, equals: X}` across both modes. This is
    descriptive only — it never decides whether a client qualifies.
    """
    found = set()

    def scan(entry):
        w = entry.get("when") if isinstance(entry, dict) else None
        if isinstance(w, dict) and w.get("field") == "exemption_category":
            found.add(str(w.get("equals", "")))

    for e in mapping.get("entries") or []:
        scan(e)
    for e in (mapping.get("fields") or {}).values():
        scan(e)
    return [c for c in CATEGORIES if c in found] + sorted(found - set(CATEGORIES))


# --------------------------------------------------------------------------
# value resolution
# --------------------------------------------------------------------------
def _value(entry: dict, ctx: dict) -> str:
    if "from" in entry:
        return str(ctx.get(entry["from"], ""))
    if "literal" in entry:
        return str(entry["literal"])
    return ""


def _marked(entry: dict, ctx: dict) -> bool:
    if entry.get("check") is True:
        return True
    if "when" in entry:
        w = entry["when"]
        return str(ctx.get(w.get("field"), "")) == str(w.get("equals"))
    return False


# ---------- fillable (AcroForm) ----------
def _fill_fillable(mapping, client, person, form_key, report):
    pdf_path = str(resolve(mapping["pdf"]))
    ctx = build_context(client, person)
    meta = {f["id"]: f for f in pdf_fill.list_fields(pdf_path)}
    overrides = _overrides_for(client, form_key, person)
    values = {}
    for fid, entry in (mapping.get("fields") or {}).items():
        if fid not in meta:
            report(f"  ! mapping references unknown field '{fid}' in {form_key}")
            continue
        if entry.get("checkbox"):
            values[fid] = meta[fid].get("on_value", "/On") if _marked(entry, ctx) else "/Off"
        else:
            values[fid] = _overridden(overrides, fid, entry, _value(entry, ctx), report)
    out = _out_path(client, form_key, person)
    pdf_fill.fill(pdf_path, values, str(out), report=report)
    return out


# ---------- overlay (flat scan) ----------
def _fill_overlay(mapping, client, person, form_key, report):
    pdf_path = str(resolve(mapping["pdf"]))
    ctx = build_context(client, person)
    overrides = _overrides_for(client, form_key, person)
    marks = _marks_for(client, form_key, person)
    resolved = []
    for i, e in enumerate(mapping.get("entries") or []):
        item = {k: e[k] for k in ("anchor", "occurrence", "page", "x", "y", "dx", "dy", "size") if k in e}
        if "mark" in e:
            # A box the worker set by hand wins over the mapping's rule, on or off.
            if not marks.get(i, _marked(e, ctx)):
                continue
            item["mark"] = e["mark"] or "X"
        else:
            item["text"] = _overridden(overrides, i, e, _value(e, ctx), report)
        resolved.append(item)
    ops = pdf_overlay.resolve_entries(pdf_path, resolved, report=report)
    table_ops, attachments = _table_ops(mapping, client, person, ctx, report)
    out = _out_path(client, form_key, person)
    pdf_overlay.stamp(pdf_path, ops + table_ops, str(out), append=attachments)
    return out


# ---------- repeating tables (the document list) ----------
def _table_ops(mapping, client, person, ctx, report):
    """Draw ops for every `tables:` block, plus any continuation sheets to append.

    A table is the one part of a form whose length the mapping can't know: a client
    has as many recorded documents as they have. Rows come from the client's
    `documents:` list (documents.py); the mapping supplies only the geometry.

    When the rows outrun the form's ruled lines, the last line says so and the WHOLE
    list goes onto an attachment. Printing only what fits would understate the
    request, and this form redacts exactly what it names.
    """
    ops, attachments = [], []
    for table in mapping.get("tables") or []:
        if not isinstance(table, dict):
            continue
        listed = table.get("list", documents.DEFAULT_LIST)
        rows = documents.for_form(client, listed, person)
        if not rows:
            continue

        capacity = int(table.get("rows") or len(rows))
        shown, note = rows, ""
        if len(rows) > capacity:
            label = str(table.get("attachment") or "A")
            heading = table.get("heading") or "Documents"
            shown = rows[:max(capacity - 1, 0)]
            note = str(table.get("continued") or
                       "Continued on Attachment {label} — all {count} documents listed there."
                       ).format(label=label, count=len(rows))
            attachments.append(pdf_attachment.build(
                rows, label=label, heading=heading,
                requestor=ctx.get("full_name", ""), form_title=mapping.get("title", "")))
            report(f"       + {len(rows)} {listed} document(s): {len(shown)} on the form, "
                   f"all {len(rows)} on Attachment {label}")
        else:
            report(f"       + {len(rows)} {listed} document(s) listed on the form")
        ops += pdf_overlay.table_entries(table, shown, note)
    return ops, attachments


def rel(path: Path) -> str:
    """Repo-relative string for display, so progress lines stay short."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def split_output_name(filename: str) -> tuple[str, str]:
    """'<form_key>__<person>.pdf' -> ('<form_key>', '<person>').

    The naming convention lives here, next to the code that writes it, so the UI
    never has to know how an output file is composed.
    """
    stem = Path(filename).stem
    form_key, sep, person = stem.partition("__")
    return (form_key, person) if sep else (stem, "")


def generated_files(client: dict) -> list[dict]:
    """Everything already generated for this client, newest first.

    Reads the output folder rather than any index — the files on disk are the
    record, and the CLI writes to the same place, so forms filled from a terminal
    show up here too.
    """
    out = output_dir(client)
    if not out.is_dir():
        return []

    rows = []
    for p in sorted(out.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        form_key, person = split_output_name(p.name)
        stat = p.stat()
        rows.append({
            "filename": p.name,
            "path": p,
            "form_key": form_key,
            "person": person,
            "kind": p.suffix.lower().lstrip("."),
            "is_pdf": p.suffix.lower() == ".pdf",
            "size": stat.st_size,
            "modified": _dt.datetime.fromtimestamp(stat.st_mtime),
        })
    rows.sort(key=lambda r: r["modified"], reverse=True)
    return rows


def output_dir(client: dict) -> Path:
    """output/<display_name slug>/ — the same folder the CLI has always used."""
    return OUTPUT_DIR / slugify((client.get("case") or {}).get("display_name"))


def _out_path(client, form_key, person) -> Path:
    out_dir = output_dir(client)
    out_dir.mkdir(parents=True, exist_ok=True)
    who = person or (client["persons"][0].get("role", "primary"))
    return out_dir / f"{form_key}__{who}.pdf"


def _overrides_for(client: dict, form_key: str, person: str | None = None) -> dict:
    """{entry id: replacement text} for one form and person.

    An override REPLACES what an entry would have written, rather than adding a second
    string on top of it — stamping over existing text would simply overlap and be
    unreadable. That is why this is consulted while filling, not afterwards.

    The id is the entry's position for an overlay mapping, or the field name for a
    fillable one. `from:` is stored alongside so a later mapping edit that reorders
    entries doesn't silently move someone's override onto a different field.
    """
    stored = (client.get("overrides") or {}).get(form_key) or []
    out = {}
    for e in stored:
        if not isinstance(e, dict) or "entry" not in e:
            continue
        who = e.get("person")
        if who and person and who != person:
            continue
        if who and not person and who != (person_roles(client) or [None])[0]:
            continue
        out[e["entry"]] = {"text": str(e.get("text", "")), "from": e.get("from")}
    return out


def _overridden(overrides: dict, key, entry: dict, fallback: str, report) -> str:
    """Use the override for this entry if it still points at the same thing."""
    o = overrides.get(key)
    if o is None:
        return fallback
    expected = o.get("from")
    if expected and entry.get("from") != expected:
        report(f"  ! an edit saved for '{expected}' no longer matches this entry "
               "— the mapping changed. Ignoring it.")
        return fallback
    return o["text"]


def _marks_for(client: dict, form_key: str, person: str | None = None) -> dict:
    """{entry index: True/False} — boxes the worker ticked or un-ticked by hand.

    A mapping decides most boxes from the client's data, but a form always has boxes
    only the person filling it can answer (which evidence is attached, which notary
    method). This lets them say so without touching the mapping, and it overrides the
    mapping's own decision either way.
    """
    stored = (client.get("marks") or {}).get(form_key) or []
    out = {}
    for e in stored:
        if not isinstance(e, dict) or "entry" not in e:
            continue
        who = e.get("person")
        if who and person and who != person:
            continue
        if who and not person and who != (person_roles(client) or [None])[0]:
            continue
        out[e["entry"]] = bool(e.get("on"))
    return out


def form_layout(mapping: dict, client: dict, person: str | None = None,
                report=print) -> list[dict]:
    """Every control the editor draws over the page, positioned where the engine writes.

    One item per mapping entry: text entries become editable boxes, mark entries become
    tick boxes. Anchored entries are resolved against the blank PDF so the control sits
    exactly where the value will land.
    """
    pdf_path = str(resolve(mapping["pdf"]))
    ctx = build_context(client, person)
    form_key = mapping.get("form_key", "")
    overrides = _overrides_for(client, form_key, person)
    marks = _marks_for(client, form_key, person)

    items = []
    for i, e in enumerate(mapping.get("entries") or []):
        if not isinstance(e, dict):
            continue
        spot = pdf_overlay.entry_position(pdf_path, e, report=report)
        if spot is None:
            continue
        page, x, y = spot
        is_mark = "mark" in e
        item = {
            "entry": i, "page": page + 1, "x": float(x), "y": float(y),
            "size": e.get("size", 10), "from": e.get("from", ""),
            "label": _humanize(e), "kind": "mark" if is_mark else "text",
        }
        if is_mark:
            item["by_rule"] = _marked(e, ctx)
            item["on"] = marks.get(i, item["by_rule"])
            item["forced"] = i in marks
        else:
            original = _value(e, ctx)
            o = overrides.get(i)
            edited = o is not None and (not o.get("from") or o["from"] == e.get("from"))
            item["original"] = original
            item["value"] = o["text"] if edited else original
            item["edited"] = edited
        items.append(item)
    return items


def form_fields(mapping: dict, client: dict, person: str | None = None) -> list[dict]:
    """Every piece of TEXT this form writes, with what it currently says.

    Drives the "edit the text on this form" screen: one row per value, showing the
    live value and whether it has been edited by hand.
    """
    ctx = build_context(client, person)
    form_key = mapping.get("form_key", "")
    overrides = _overrides_for(client, form_key, person)
    rows = []

    if mapping.get("mode") == "overlay":
        for i, e in enumerate(mapping.get("entries") or []):
            if not isinstance(e, dict) or "mark" in e:
                continue                      # marks are checkboxes, not text
            rows.append(_field_row(i, e, ctx, overrides))
    else:
        for fid, e in (mapping.get("fields") or {}).items():
            if not isinstance(e, dict) or e.get("checkbox"):
                continue
            rows.append(_field_row(fid, e, ctx, overrides))
    return rows


def _field_row(key, entry: dict, ctx: dict, overrides: dict) -> dict:
    original = _value(entry, ctx)
    o = overrides.get(key)
    edited = o is not None and (not o.get("from") or o["from"] == entry.get("from"))
    return {
        "entry": key,
        "from": entry.get("from", ""),
        "label": _humanize(entry),
        "original": original,
        "value": o["text"] if edited else original,
        "edited": edited,
        "page": entry.get("page", 1),
        "anchor": entry.get("anchor", ""),
    }


def _humanize(entry: dict) -> str:
    """A readable name for a field, from whatever the mapping gives us."""
    if entry.get("note"):
        return str(entry["note"])
    if entry.get("from"):
        return str(entry["from"]).replace("_", " ").capitalize()
    if "mark" in entry:
        # Describe the rule the box follows, so a tick isn't just an anonymous square.
        when = entry.get("when")
        if isinstance(when, dict):
            field = str(when.get("field", "")).replace("_", " ")
            return f"{field}: {str(when.get('equals', '')).replace('_', ' ')}".strip(": ")
        if entry.get("check") is True:
            return "Always ticked"
        return "Tick box"
    if entry.get("anchor"):
        return str(entry["anchor"]).rstrip(":")
    if "literal" in entry:
        return f"Fixed: {entry['literal']}"
    return "Text"


def client_corrections(client: dict, form_key: str, person: str | None = None) -> list[dict]:
    """Manual fixes this client has saved for this form.

    Corrections live on the client, not on the produced PDF, so they are re-applied
    every time the form is generated. Annotating the output file instead would be
    silently undone by the next Generate — which is exactly when you'd least notice.

    An entry is an overlay entry (page/x/y plus text, from or mark) with an optional
    `person:` to limit it to one person's copy.
    """
    by_form = (client.get("corrections") or {}).get(form_key) or []
    out = []
    for e in by_form:
        if not isinstance(e, dict):
            continue
        who = e.get("person")
        if who and person and who != person:
            continue
        if who and not person:
            # No explicit person means "the default (first) one".
            first = (person_roles(client) or [None])[0]
            if who != first:
                continue
        out.append(e)
    return out


def apply_corrections(client, form_key, person, pdf_path: Path, report=print) -> int:
    """Stamp this client's corrections onto an already-generated file. Returns how many.

    Runs after the mapping, so a correction always wins over what the mapping put
    there. Works for both modes: a fillable PDF is filled first, then stamped.
    """
    entries = client_corrections(client, form_key, person)
    if not entries:
        return 0

    ctx = build_context(client, person)
    resolved = []
    for e in entries:
        item = {k: e[k] for k in ("anchor", "occurrence", "page", "x", "y", "dx", "dy", "size")
                if k in e}
        if "mark" in e:
            item["mark"] = e["mark"] or "X"
        else:
            item["text"] = _value(e, ctx) if ("from" in e or "literal" in e) else str(e.get("text", ""))
        resolved.append(item)

    ops = pdf_overlay.resolve_entries(str(pdf_path), resolved, report=report)
    if not ops:
        return 0
    # Stamp via a temp file — pdf_overlay reads the source while writing the target,
    # so writing back over the input in place is not safe.
    tmp = pdf_path.with_suffix(".correcting.tmp")
    pdf_overlay.stamp(str(pdf_path), ops, str(tmp))
    os.replace(tmp, pdf_path)
    report(f"       + {len(ops)} manual correction(s) applied")
    return len(ops)


def fill_one(mapping_path, client, person, report=print):
    """Fill ONE form for ONE person. Returns the output Path, or None if skipped.

    `report` receives human-readable progress lines. The CLI passes print(); the web
    app passes a collector, so field values and client paths never reach a server log.
    """
    mapping = load_mapping(mapping_path)
    form_key = mapping.get("form_key") or form_key_for(mapping_path)
    if not mapping.get("pdf") or not resolve(mapping["pdf"]).exists():
        report(f"[skip] {form_key}: blank PDF not found at {mapping.get('pdf')}")
        return None
    mode = mapping.get("mode", "fillable")
    if mapping.get("tables") and mode != "overlay":
        report(f"  ! {form_key}: `tables:` only works in overlay mode — the document "
               "list will not be printed.")
    fill = _fill_overlay if mode == "overlay" else _fill_fillable
    out = fill(mapping, client, person, form_key, report)
    report(f"[ok]   {form_key} ({mode})  ->  {rel(out)}")
    apply_corrections(client, form_key, person, out, report=report)
    return out


def fill_client(client: dict, persons=None, form_keys=None, report=print) -> list[Path]:
    """Fill the chosen forms for the chosen persons. The one entry point both UIs use.

    persons:   list of role strings, or [None] for "the default (first) person".
    form_keys: list of form_key strings, or None for every mapping.
    """
    mappings = mapping_paths()
    if not mappings:
        raise ClientDataError("no mappings in forms/mappings/ — run scaffold_mapping.py first")
    if not persons:
        persons = [None]

    made = []
    for person in persons:
        for m in mappings:
            if form_keys and form_key_for(m) not in form_keys:
                continue
            out = fill_one(m, client, person, report=report)
            if out:
                made.append(out)
    return made


def main():
    ap = argparse.ArgumentParser(description="Fill government PDF forms for a client.")
    ap.add_argument("--client", required=True)
    ap.add_argument("--person", help="person role to use (default: first/primary)")
    ap.add_argument("--all-persons", action="store_true",
                    help="fill for every person in the client file")
    ap.add_argument("--forms", nargs="*", help="only these form_keys (default: all)")
    args = ap.parse_args()

    try:
        client = load_client(args.client)
        if args.all_persons:
            persons = person_roles(client)
            if args.person:
                ap.error("--all-persons and --person are mutually exclusive")
        else:
            persons = [args.person]
        made = fill_client(client, persons=persons, form_keys=args.forms)
    except ClientDataError as exc:
        raise SystemExit(str(exc))

    print(f"\nDone. {len(made)} form(s) filled. Review, notarize where required, then submit.")


if __name__ == "__main__":
    main()
