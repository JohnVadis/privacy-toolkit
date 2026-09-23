"""
pdf_render.py — render a PDF page to a PNG, and report its size in points.

Used by the coordinate picker (click a spot on the page, get the PDF coordinate) and
by the inline previews. Kept out of webapp/ so the CLI can use it too — rendering a
page and eyeballing it is exactly the verification step docs/adding-forms.md describes,
and it removes the dependency on having pdftoppm installed.

    python pdf_render.py forms/_blank_pdfs/Hillsborough_Redaction.pdf 1 out.png

Deterministic, local, no network. Requires pypdfium2 (already a pdfplumber dependency).
"""
from __future__ import annotations

import io
from pathlib import Path

from paths import resolve

# A PDF point is 1/72". Rendering at 2x gives a crisp enough image to click
# accurately without producing a huge PNG.
DEFAULT_SCALE = 2.0
MAX_SCALE = 4.0


def page_count(pdf_path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(resolve(pdf_path)))
    try:
        return len(pdf)
    finally:
        pdf.close()


def page_size(pdf_path, page: int = 0) -> tuple[float, float]:
    """(width, height) of a page in PDF points. Page is 0-based."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(resolve(pdf_path)))
    try:
        return tuple(pdf[page].get_size())
    finally:
        pdf.close()


def page_png(pdf_path, page: int = 0, scale: float = DEFAULT_SCALE) -> tuple[bytes, float, float]:
    """Render one page. Returns (png_bytes, width_pt, height_pt).

    The point size travels with the image so a caller can convert a click back to a
    PDF coordinate without re-opening the file:

        x_pt = click_x / (img_width / width_pt)
        y_pt = height_pt - click_y / (img_height / height_pt)    # origin bottom-left
    """
    import pypdfium2 as pdfium

    scale = max(0.25, min(float(scale), MAX_SCALE))
    pdf = pdfium.PdfDocument(str(resolve(pdf_path)))
    try:
        page = max(0, min(int(page), len(pdf) - 1))
        target = pdf[page]
        w_pt, h_pt = target.get_size()
        image = target.render(scale=scale).to_pil()
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), float(w_pt), float(h_pt)
    finally:
        pdf.close()


def main():
    import sys

    if len(sys.argv) not in (3, 4):
        print("Usage: python pdf_render.py <file.pdf> <page-number> [out.png]")
        raise SystemExit(1)
    src, page = sys.argv[1], int(sys.argv[2]) - 1
    out = Path(sys.argv[3]) if len(sys.argv) == 4 else Path(f"page{page + 1}.png")
    data, w, h = page_png(src, page, DEFAULT_SCALE)
    out.write_bytes(data)
    print(f"wrote {out} — page is {w:.0f} x {h:.0f} pt "
          f"({page_count(src)} page(s) in the document)")


if __name__ == "__main__":
    main()
