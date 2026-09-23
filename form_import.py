"""
form_import.py — take a blank PDF and propose a mapping for it.

    python form_import.py forms/_blank_pdfs/new_form.pdf my_form_key

Two cases, decided by the PDF itself:

  * It has AcroForm fields  -> a `fillable` mapping, one entry per field, with each
    field name matched to a client variable by name. This is the demo_redaction shape
    and it is the reliable one: the PDF already says where every field is.
  * It has none (most Florida county forms are flat scans) -> an `overlay` mapping,
    with an entry anchored to each label the text layer exposes.

What this CANNOT do, and says so rather than pretending:
  * Checkboxes on a flat scan. On the Hillsborough forms the boxes are neither text
    glyphs nor rectangles, so there is nothing to find — every mark has to be placed
    by hand.
  * Anything at all on an image-only scan, which has no text layer to read.

A proposal is a starting point, never a finished mapping. Every position must still be
looked at — see docs/verification-checklist.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdf_fill
from paths import resolve

# field/label wording -> client variable. First match wins, so the specific
# patterns come before the general ones.
VARIABLE_HINTS: list[tuple[str, str]] = [
    (r"date\s*of\s*birth|\bd\.?o\.?b\b|birth\s*date", "dob"),
    (r"first\s*name|given\s*name", "first_name"),
    (r"middle\s*(name|initial)", "middle_name"),
    (r"last\s*name|surname|family\s*name", "last_name"),
    (r"suffix", "suffix"),
    (r"owner\s*/?\s*relationship|relationship", "owner_relationship"),
    (r"printed\s*name|full\s*name|name\s*of\s*(applicant|requestor)|^name$|^name\b", "full_name"),
    (r"e-?mail", "email"),
    (r"tele|phone|mobile|cell", "phone"),
    (r"parcel|property\s*id|folio|strap", "home_parcel_id"),
    (r"home\s*address|street\s*address|mailing\s*address|^address", "home_full"),
    (r"\bcounty\b", "home_county"),
    (r"\bcity\b", "home_city"),
    (r"\bstate\b", "home_state"),
    (r"\bzip\b|postal", "home_zip"),
    (r"job\s*title|position|rank", "job_title"),
    (r"employing\s*agency|agency|employer|department", "employing_agency"),
    (r"case\s*(number|no)", "case_number"),
    (r"agent", "agent_name"),
    # Deliberately no rule for a bare "Date:". On these forms it is the date the
    # client signs in front of a notary, and pre-filling today's date onto a sworn
    # document would be wrong. It stays blank for a human.
]

# Labels that introduce something a human must fill in, not the client file.
SKIP_LABELS = re.compile(
    r"signature|notary|sworn|witness|commission|seal|initial|attn|fax|warning|note$|^date$",
    re.I)

LABEL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 /()'\-\.]{2,44}):")


def match_variable(name: str) -> str:
    """Best-guess client variable for a field or label name. '' when unsure."""
    text = re.sub(r"[_\-]+", " ", str(name or "")).strip().lower()
    if not text:
        return ""
    for pattern, var in VARIABLE_HINTS:
        if re.search(pattern, text):
            return var
    return ""


def analyze(pdf_path) -> dict:
    """What we can tell about a blank form without anyone looking at it."""
    path = resolve(pdf_path)
    try:
        fields = pdf_fill.list_fields(str(path))
    except Exception:
        fields = []

    pages, words, labels = 0, 0, []
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                found = page.extract_words()
                words += len(found)
                labels.extend(_labels_on_page(page, i, found))
    except Exception:
        pass
    labels = _dedupe(labels)

    return {
        "path": path,
        "pages": pages,
        "fields": fields,
        "has_fields": bool(fields),
        "word_count": words,
        "image_only": words == 0,
        "labels": labels,
    }


# How many words before the ':' to keep as the anchor. The anchor engine matches a
# substring of the line, so a short tail is both easier to read and less likely to
# break when a county reissues the form with slightly different wording.
ANCHOR_WORDS = 4


def _is_rule(text: str) -> bool:
    """True for a fill-in rule like '______' or '......' — not part of a label.

    Length matters: a lone '/' or '-' is punctuation inside a label, and dropping it
    built anchors like 'Name of Owner Relationship:' that match nothing on the page.
    """
    bare = (text or "").replace(":", "")
    return len(bare) >= 3 and bool(re.fullmatch(r"[_.\-–—]+", bare))


def _labels_on_page(page, page_no: int, words) -> list[dict]:
    """Labels ending in ':' with the point just after them — anchor candidates.

    Built from the word list rather than by scanning joined text, so a label always
    starts at a word boundary. Scanning the string produced fragments like
    'rity Number (do not list SSN) / Date of Birth:' by starting mid-word.
    """
    by_line: dict[int, list] = {}
    for w in words:
        by_line.setdefault(round(w["top"]), []).append(w)

    out = []
    for _top, line in sorted(by_line.items()):
        line.sort(key=lambda w: w["x0"])
        segment: list = []
        for w in line:
            segment.append(w)
            if not w["text"].endswith(":"):
                continue
            # A colon closes a label; the next one starts a fresh segment.
            # The blank rule before a label ("______") extracts as a word; an
            # anchor built from it would be both unreadable and ambiguous.
            meaningful = [x for x in segment if not _is_rule(x["text"])]
            tail = meaningful[-ANCHOR_WORDS:]
            label = " ".join(x["text"] for x in tail).strip()
            segment = []
            if not tail or len(label) < 4 or SKIP_LABELS.search(label):
                continue
            var = match_variable(label)
            if not var:
                continue
            out.append({
                "page": page_no,
                "label": label,
                "variable": var,
                "top": min(x["top"] for x in tail),
            })
    return out


def _dedupe(rows: list[dict]) -> list[dict]:
    """One entry per variable for the whole form, keeping the earliest.

    Deduping per page kept a second 'Email:' from the agency's contact block on the
    last page, which would have stamped the client's address into the county's own
    details. A form asks for each thing once.
    """
    seen, out = set(), []
    for r in rows:
        if r["variable"] in seen:
            continue
        seen.add(r["variable"])
        out.append(r)
    return out


def suggest(pdf_path, form_key: str, title: str = "", county: str = "") -> tuple[dict, list[str]]:
    """Propose a mapping. Returns (mapping dict, notes explaining the gaps)."""
    info = analyze(pdf_path)
    rel = _repo_relative(info["path"])
    notes: list[str] = []

    base = {"mode": "fillable" if info["has_fields"] else "overlay",
            "form_key": form_key,
            "title": title or form_key.replace("_", " "),
            "county": county or "",
            "pdf": rel}

    if info["has_fields"]:
        fields = {}
        matched = 0
        for f in info["fields"]:
            if f["type"] == "checkbox":
                fields[f["id"]] = {"checkbox": True, "check": False}
                continue
            var = match_variable(f["id"])
            if var:
                matched += 1
            fields[f["id"]] = {"from": var} if var else {"literal": ""}
        base["fields"] = fields
        notes.append(f"This PDF has {len(info['fields'])} form field(s); "
                     f"{matched} matched a client variable by name.")
        unmatched = [f['id'] for f in info["fields"]
                     if f["type"] != "checkbox" and not match_variable(f["id"])]
        if unmatched:
            notes.append("Set these by hand — the name gave no clue: "
                         + ", ".join(unmatched[:12]))
        if any(f["type"] == "checkbox" for f in info["fields"]):
            notes.append("Checkboxes are left unticked. Give each one a rule "
                         "(check: true, or when: {field: …, equals: …}).")
        return base, notes

    # ---- flat scan ----
    if info["image_only"]:
        notes.append("This PDF has no text layer at all — it is an image. Nothing can "
                     "be detected; every value has to be placed by hand on the page.")
        base["entries"] = []
        return base, notes

    entries = []
    for lab in info["labels"]:
        entries.append({"anchor": lab["label"], "page": lab["page"],
                        "dx": 6, "dy": 0, "from": lab["variable"]})
    base["entries"] = entries
    notes.append(f"No form fields, so this is an overlay mapping. "
                 f"{len(entries)} value(s) were placed against a label found in the text.")
    notes.append("CHECKBOXES WERE NOT DETECTED. On these forms the boxes are not text "
                 "or rectangles, so none can be found automatically — add each mark by "
                 "hand on the page.")
    notes.append("Every placement is a guess from a label. Preview the form and drag "
                 "anything that sits in the wrong spot before using it.")
    return base, notes


def _repo_relative(path: Path) -> str:
    from paths import ROOT

    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def main():
    import argparse

    import yaml

    ap = argparse.ArgumentParser(description="Propose a mapping for a blank form PDF.")
    ap.add_argument("pdf")
    ap.add_argument("form_key")
    ap.add_argument("--title", default="")
    ap.add_argument("--county", default="")
    ap.add_argument("--write", action="store_true",
                    help="write forms/mappings/<form_key>.map.yaml")
    args = ap.parse_args()

    mapping, notes = suggest(args.pdf, args.form_key, args.title, args.county)
    for n in notes:
        print(f"  * {n}")
    text = yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True, width=100)
    if args.write:
        import mapping_edit

        path, backup, problems = mapping_edit.save_mapping(args.form_key, text)
        if problems:
            print("\nNot written — the proposal has problems:")
            for p in problems:
                print(f"    {p}")
            raise SystemExit(1)
        print(f"\nWrote {path}" + (f" (previous version kept at {backup})" if backup else ""))
    else:
        print("\n" + text)


if __name__ == "__main__":
    main()
