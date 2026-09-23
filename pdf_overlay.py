"""
pdf_overlay.py — stamp text / X-marks onto a FLAT (non-fillable) PDF.

Many county forms are flat scans with no form fields. This engine places values
by coordinate. Two ways to locate a value, per entry in an overlay mapping:

  anchor-based (preferred, resilient):  { anchor: "Home Address:", dx: 8, dy: -1, from: home_full }
      -> finds that label's text on the page and stamps just after it.
  absolute (for image-only scans):      { page: 1, x: 250, y: 470, mark: "X" }
      -> stamps at an exact PDF coordinate (origin bottom-left).

Coordinates are PDF points, origin bottom-left. For anchors, the engine computes
the label position with pdfplumber; dx shifts right, dy shifts up.

Requires: pypdf, reportlab, pdfplumber
"""
from __future__ import annotations
import io
import pdfplumber
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter


def _anchor_box(pdf_path, page_index, text, occurrence=1):
    """Return (x0, x1, top, bottom, page_height) for the Nth occurrence of `text`."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        ph = page.height
        words = page.extract_words(use_text_flow=True)
        # Try to match a run of consecutive words equal to the anchor string.
        target = text.strip()
        hits = []
        # single-word fast path
        for w in words:
            if w["text"].strip() == target:
                hits.append((w["x0"], w["x1"], w["top"], w["bottom"]))
        # multi-word contiguous match on the same line
        if not hits:
            by_line = {}
            for w in words:
                by_line.setdefault(round(w["top"]), []).append(w)
            for _, ws in by_line.items():
                ws.sort(key=lambda w: w["x0"])
                joined = " ".join(w["text"] for w in ws)
                idx = joined.find(target)
                if idx != -1:
                    # approximate box: from first to last word of the run
                    run = []
                    acc = ""
                    for w in ws:
                        if acc:
                            acc += " "
                        acc += w["text"]
                        run.append(w)
                        if target in acc:
                            break
                    x0 = min(w["x0"] for w in run if w["text"] in target or True)
                    x1 = max(w["x1"] for w in run)
                    top = min(w["top"] for w in run)
                    bottom = max(w["bottom"] for w in run)
                    hits.append((x0, x1, top, bottom))
        if len(hits) < occurrence:
            return None
        x0, x1, top, bottom = hits[occurrence - 1]
        return (x0, x1, top, bottom, ph)


def entry_position(pdf_path, entry, report=print):
    """Where an entry lands: (page_index, x, y) in PDF points, or None.

    Pulled out of resolve_entries so the form editor can position a control over the
    rendered page at exactly the spot the engine will draw — including for anchored
    entries, whose coordinate is only known after looking the label up on the page.
    """
    page = entry.get("page", 1) - 1
    if "anchor" in entry:
        box = _anchor_box(pdf_path, page, entry["anchor"], entry.get("occurrence", 1))
        if not box:
            report(f"  ! anchor not found: {entry['anchor']!r} (entry skipped)")
            return None
        _x0, x1, _top, bottom, ph = box
        return page, x1 + entry.get("dx", 6), (ph - bottom) + entry.get("dy", 0)
    if "x" not in entry or "y" not in entry:
        return None
    return page, entry["x"], entry["y"]


def resolve_entries(pdf_path, entries, report=print):
    """
    Turn mapping entries (already value-resolved to `text`/`mark`) into absolute
    draw ops: list of {page, x, y, text, size}. Anchors are looked up here.
    """
    ops = []
    for e in entries:
        text = e.get("text", "")
        if text is None:
            text = ""
        if text == "" and not e.get("mark"):
            continue
        draw = e.get("mark") or text
        size = e.get("size", 10)
        spot = entry_position(pdf_path, e, report=report)
        if spot is None:
            continue
        page, x, y = spot
        ops.append({"page": page, "x": x, "y": y, "text": draw, "size": size})
    return ops


def fit_text(text, width, size, min_size=6.5, font="Helvetica"):
    """Shrink, then truncate, so `text` fits `width` points. Returns (text, size).

    A ruled cell on a county form is as wide as it is; a document title that runs past
    it would print over the next column. Shrinking first keeps the value whole where
    that is enough, and only a genuinely oversized one is cut — where the list has
    overflowed, the untruncated value is still on the attachment, and in the YAML
    either way.
    """
    text = str(text or "")
    if not text or not width or width <= 0:
        return text, size
    while size > min_size and stringWidth(text, font, size) > width:
        size -= 0.5
    if stringWidth(text, font, size) <= width:
        return text, size
    while text and stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return (text + "..." if text else ""), size


def table_entries(table, rows, note=""):
    """A repeating block -> overlay entries, one per filled cell.

    `table` is a mapping's `tables:` item: a page, the baseline of its first ruled
    row, the pitch between rows, and a column list of {from, x, width}. `rows` are
    dicts keyed by those `from` names. `note`, if given, is written across the row
    after the last one — that is where "Continued on Attachment A" goes.

    Geometry lives here with the rest of the coordinate maths; what the rows MEAN is
    the caller's business.
    """
    # `page:` is 1-based in a mapping, like an entry's; a draw op is a 0-based index.
    page = int(table.get("page", 1)) - 1
    size = float(table.get("size", 9))
    first_y = float(table.get("first_y", 0))
    pitch = float(table.get("row_pitch", 0))
    columns = [c for c in (table.get("columns") or []) if isinstance(c, dict)]
    ops = []

    for i, row in enumerate(rows):
        y = first_y - (i * pitch)
        for col in columns:
            value = str(row.get(col.get("from", ""), "") or "")
            if not value:
                continue
            text, s = fit_text(value, float(col.get("width", 0) or 0),
                               float(col.get("size", size)))
            if text:
                ops.append({"page": page, "x": float(col["x"]), "y": y,
                            "text": text, "size": s})

    if note and columns:
        span = (float(columns[-1]["x"]) + float(columns[-1].get("width", 0) or 0)
                - float(columns[0]["x"]))
        text, s = fit_text(note, span, float(table.get("note_size", size - 0.5)))
        ops.append({"page": page, "x": float(columns[0]["x"]),
                    "y": first_y - (len(rows) * pitch), "text": text, "size": s})
    return ops


def stamp(pdf_path, ops, out_path, append=()):
    """Draw ops onto the PDF and write out_path (preserves all base pages).

    `append` is an optional list of PDF byte strings whose pages are added after the
    form's own — the continuation sheets for a list too long for its ruled rows.
    """
    reader = PdfReader(pdf_path)
    # Clone the whole document first so every base page + its resources survive.
    writer = PdfWriter(clone_from=reader)
    pages_with_ops = sorted({op["page"] for op in ops})
    for i in pages_with_ops:
        page = writer.pages[i]
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        for op in ops:
            if op["page"] != i:
                continue
            c.setFont("Helvetica", op["size"])
            c.drawString(op["x"], op["y"], str(op["text"]))
        c.save()
        buf.seek(0)
        overlay = PdfReader(buf).pages[0]
        page.merge_page(overlay)
    for blob in append or ():
        for extra in PdfReader(io.BytesIO(blob)).pages:
            writer.add_page(extra)
    with open(out_path, "wb") as fh:
        writer.write(fh)
