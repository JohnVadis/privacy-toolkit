"""
pdf_attachment.py — build the continuation sheet a county form has no room for.

The Clerk's redaction form rules four lines for documents to be redacted and two for
prior redactions being released. A real client has more than that, and the form's own
warning — "only the documents identified by the requestor will be redacted" — makes a
short list a filing defect rather than a formatting one.

So when a list overruns its ruled rows, the form says "Continued on Attachment A" and
these pages carry the WHOLE list, numbered, in the same four columns. Whole, not just
the overflow: a reader should never have to stitch two lists together to know what
was asked for.

Pages are generated here and appended to the filled PDF by fill_forms. Each one has
an initial/date line, because an attachment incorporated by reference into a notarized
form is initialled by the person signing it — one more human gate, consistent with
the rest of the toolkit.
"""
from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdf_overlay import fit_text

FONT = "Helvetica"
BOLD = "Helvetica-Bold"

PAGE_W, PAGE_H = letter
MARGIN = 72.0
TOP = PAGE_H - 72.0          # first baseline
BOTTOM = 108.0               # last row may not go below this (room for the initials)
ROW_PITCH = 16.0
SIZE = 9.0

# (field, heading, width). Widths total the printable 468pt. Document titles run long
# — "NOTICE OF COMMENCEMENT AND AFFIDAVIT OF OWNERSHIP" is a real one — so that column
# gets the room, and a value that still doesn't fit is set smaller before it is cut.
COLUMNS = (
    ("_n", "No.", 26.0),
    ("instrument_number", "Instrument Number", 104.0),
    ("book", "Book", 46.0),
    ("page", "Page", 46.0),
    ("title", "Document Title", 246.0),
)
MIN_SIZE = 6.0


def rows_per_page() -> int:
    """How many document rows fit on one sheet below the heading block."""
    return int((TOP - 96.0 - BOTTOM) // ROW_PITCH)


def paginate(rows: list[dict]) -> list[list[dict]]:
    per = max(rows_per_page(), 1)
    return [rows[i:i + per] for i in range(0, len(rows), per)] or [[]]


def build(rows: list[dict], *, label: str, heading: str, requestor: str = "",
          form_title: str = "", note: str = "") -> bytes:
    """Render the attachment as a PDF and return its bytes.

    label:     "A" / "B" — matches what the form's last ruled line points at.
    heading:   what this list is, e.g. "Documents to be Redacted".
    requestor: the person whose form this is attached to.
    note:      an extra line under the heading, if the list needs one.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    pages = paginate(list(rows))
    numbered = 0

    for index, page_rows in enumerate(pages, start=1):
        y = _heading(c, label, heading, requestor, form_title, note, index, len(pages))
        for row in page_rows:
            numbered += 1
            _row(c, y, {**row, "_n": f"{numbered}."})
            y -= ROW_PITCH
        _initials(c)
        c.showPage()

    c.save()
    return buf.getvalue()


def _heading(c, label, heading, requestor, form_title, note, index, total) -> float:
    y = TOP
    c.setFont(BOLD, 12)
    c.drawString(MARGIN, y, f"ATTACHMENT {label} — {heading.upper()}")
    y -= 16
    c.setFont(FONT, 8.5)
    if form_title:
        c.drawString(MARGIN, y, form_title)
        y -= 11
    line = " · ".join(x for x in [f"Requestor: {requestor}" if requestor else "",
                                 f"Page {index} of {total}"] if x)
    c.drawString(MARGIN, y, line)
    y -= 18

    c.setFont(BOLD, SIZE)
    x = MARGIN
    for _field, title, width in COLUMNS:
        c.drawString(x + 2, y, title)
        x += width
    y -= 4
    c.setLineWidth(0.7)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    return y - 12


def _row(c, y: float, row: dict) -> None:
    x = MARGIN
    for field, _title, width in COLUMNS:
        text, size = fit_text(row.get(field, ""), width - 6, SIZE, min_size=MIN_SIZE)
        c.setFont(FONT, size)
        c.drawString(x + 2, y, text)
        x += width
    c.setStrokeColorRGB(0.78, 0.78, 0.78)
    c.setLineWidth(0.4)
    c.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
    c.setStrokeColorRGB(0, 0, 0)


def _initials(c) -> None:
    """The human gate: an attachment to a notarized form gets initialled."""
    c.setFont(FONT, 8.5)
    c.drawString(MARGIN, 72, "Initials of requestor: ______________________"
                             "          Date: ______________________")
