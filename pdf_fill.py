"""
pdf_fill.py — minimal, self-contained fillable-PDF helper.

Two jobs:
  list_fields(pdf)            -> what fillable fields a form has (for building a mapping)
  fill(pdf, values, out_pdf)  -> write a filled copy

Deterministic. No AI, no network. Works on any AcroForm (fillable) PDF.
Requires: pypdf  (pip install pypdf)
"""
from __future__ import annotations
from pypdf import PdfReader, PdfWriter


def _checkbox_on_value(field) -> str:
    """Return the 'checked' state name for a checkbox (e.g. '/Yes', '/1'), or '/On'."""
    states = field.get("/_States_", []) or []
    for s in states:
        if s != "/Off":
            return s
    return "/On"


def list_fields(pdf_path: str) -> list[dict]:
    """List fillable fields with their type and (for checkboxes) the on-value."""
    reader = PdfReader(pdf_path)
    fields = reader.get_fields() or {}
    out = []
    for name, f in fields.items():
        ft = f.get("/FT")
        if ft == "/Tx":
            kind = "text"
        elif ft == "/Btn":
            kind = "checkbox"
        elif ft == "/Ch":
            kind = "choice"
        else:
            kind = f"other({ft})"
        # pypdf hands back its own string object; plain str keeps these safe to
        # serialize into a mapping file and to compare against one.
        entry = {"id": str(name), "type": kind}
        if kind == "checkbox":
            entry["on_value"] = str(_checkbox_on_value(f))
        out.append(entry)
    return out


def fill(pdf_path: str, values: dict[str, str], out_path: str, report=print) -> None:
    """
    Fill `values` (field_id -> string) into the PDF and write to out_path.
    For checkboxes pass the field's on_value to check it, or '/Off' to leave it.
    Field ids not present in the form are ignored (reported via `report`).
    """
    reader = PdfReader(pdf_path)
    valid = {f["id"] for f in list_fields(pdf_path)}
    unknown = [k for k in values if k not in valid]

    writer = PdfWriter(clone_from=reader)
    clean = {k: v for k, v in values.items() if k in valid and v is not None}
    for page in writer.pages:
        writer.update_page_form_field_values(page, clean, auto_regenerate=False)

    # Make filled values render in all viewers.
    writer.set_need_appearances_writer(True)
    with open(out_path, "wb") as fh:
        writer.write(fh)

    if unknown:
        report(f"  ! ignored {len(unknown)} unmapped field id(s): {', '.join(unknown)}")


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) == 2:
        print(json.dumps(list_fields(sys.argv[1]), indent=2))
    else:
        print("Usage: python pdf_fill.py <form.pdf>   (lists fillable fields)")
