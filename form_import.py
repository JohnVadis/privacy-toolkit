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
# patterns come before the general ones. A rule whose variable is "" STOPS the
# search: the box is recognised as someone else's (an agent's, an agency's) and is
# left blank rather than falling through to a client rule further down.
VARIABLE_HINTS: list[tuple[str, str]] = [
    (r"date\s*of\s*birth|\bd\.?o\.?b\b|birth\s*date|birthdate", "dob"),
    # The agent has a name and an email in the client file — nothing else. "Agent
    # First Name" or "Agent Phone" must not get the client's.
    (r"agent.*e[-\s]?mail|e[-\s]?mail.*agent", "agent_email"),
    (r"^(name\s*of\s*)?agent(\s*full)?(\s*name)?$|^agent\s*name|name\s*of\s*agent", "agent_name"),
    (r"agent|representative|preparer|supervisor|officer", ""),
    # The agency the client works for has a name in the file; its address and phone
    # are not the client's.
    (r"employing\s*agency|^(agency|employer)(\s*name)?$|(agency|employer)\s*name", "employing_agency"),
    (r"agency|employer|employment|department", ""),
    # A box asking for more than one thing ("City/State/ZIP") is left for a person.
    (r"city\W*(state|zip)|state\W*zip", ""),
    (r"first\s*name|given\s*name|\bf\s?name\b|^first$", "first_name"),
    (r"middle\s*(name|initial)|\bm\s?name\b|^middle$", "middle_name"),
    (r"last\s*name|surname|family\s*name|\bl\s?name\b|^last$", "last_name"),
    (r"suffix", "suffix"),
    (r"owner\s*/?\s*relationship|relationship", "owner_relationship"),
    (r"printed\s*name|full\s*name|name\s*of\s*(applicant|requestor|requester|owner|petitioner)"
     r"|(applicant|requestor|requester|petitioner|owner|your|print)\s*name|^name$|^name\b",
     "full_name"),
    (r"e[-\s]?mail", "email"),
    (r"tele|phone|mobile|\bcell\b|\btel\b|\bph\b", "phone"),
    (r"parcel|property\s*id|folio|strap", "home_parcel_id"),
    (r"\bcounty\b", "home_county"),
    (r"\bcity\b|\btown\b", "home_city"),
    (r"\bstate\b|^st$", "home_state"),
    (r"\bzip|postal", "home_zip"),
    (r"address\s*(line\s*)?2|\bapt\b|apartment|suite|\bunit\b", "home_line2"),
    # Street on its own; analyze() widens it to the full address when the form has
    # no separate City box to carry the rest.
    (r"street|address\s*(line\s*)?1|\baddr(ess)?\s*1\b", "home_line1"),
    (r"home\s*address|mailing\s*address|residen(ce|tial)\s*address|physical\s*address"
     r"|property\s*address|^address|^addr\b", "home_full"),
    (r"job\s*title|position|rank|occupation", "job_title"),
    (r"case\s*(number|no|num|#)|^case$", "case_number"),
    # Deliberately no rule for a bare "Date:". On these forms it is the date the
    # client signs in front of a notary, and pre-filling today's date onto a sworn
    # document would be wrong. It stays blank for a human.
]

# Labels that introduce something a human must fill in, not the client file.
SKIP_LABELS = re.compile(
    r"signature|notary|sworn|witness|commission|seal|initial|attn|fax|warning|note$|^date$",
    re.I)

# A box about someone or something other than the client: filling it with the
# client's own details would put the wrong person's name on a sworn form. Checked
# against the WHOLE name, parents included ("Spouse[0].FirstName[0]"), and against
# the tooltip — if either says it's someone else's, it is.
NOT_THE_CLIENT = re.compile(
    r"signature|notary|witness|spouse|husband|wife|child|dependent|minor|parent|guardian"
    r"|mother|father|\bson\b|daughter|sibling|relative|co\s*applicant|joint|second|other"
    r"|additional|seal|commission|official|office\s*use|clerk|deputy|staff|received"
    r"|prior|previous|former|emergency|attorney|born|\b[2-9]\b", re.I)

# "Address Line 2" and "Street 2" are the client's; "Owner 2" is someone else.
_OWN_SECOND_LINE = re.compile(r"(address|addr|street|line)\s*2\b", re.I)

LABEL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 /()'\-\.]{2,44}):")


def _words(text: str) -> str:
    text = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)  # camelCase
    text = re.sub(r"[_\-#:.]+", " ", text)
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
    text = re.sub(r"^\s*(txt|fld|field|tb|text|input)\b\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def readable_name(name: str) -> str:
    """A field name the way a person would say it.

    PDF tools name boxes 'topmostSubform[0].Page1[0].txtFirstName[0]', 'Last_Name',
    'ZipCode2'. Matching on that raw text missed most of them.
    """
    text = re.sub(r"\[\d+\]", "", str(name or ""))      # array indexes
    if "." in text.strip("."):
        text = text.strip(".").rsplit(".", 1)[-1]       # the box, not its parents
    return _words(text)


def _someone_else(text: str) -> bool:
    # A tool's numbering ("Text3", "Page1.Field7") says nothing about whose box it
    # is; only a number after a real word ("Owner 2") does.
    text = re.sub(r"\b(text|field|fld|txt|page|form|subform|topmost)\s*\d+\b", " ", text, flags=re.I)
    if re.fullmatch(r"[\d\s]*", text):
        return False
    return bool(NOT_THE_CLIENT.search(_OWN_SECOND_LINE.sub("", text)))


def match_variable(name: str, label: str = "") -> str:
    """Best-guess client variable for a field or label name. '' when unsure.

    `label` is the box's tooltip, when the PDF has one. It is tried first, and if
    either it or the box's full name (parents included) is about someone else, the
    answer is '' — never a fall-through to whatever the other one says.
    """
    full = _words(re.sub(r"\[\d+\]", "", str(name or "")))
    texts = [readable_name(label).lower() if label else "", readable_name(name).lower()]
    if _someone_else(full) or any(_someone_else(t) for t in texts if t):
        return ""
    for text in texts:
        if not text:
            continue
        for pattern, var in VARIABLE_HINTS:
            if re.search(pattern, text):
                if var:
                    return var
                return ""                          # someone else's: stop here
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
    unmatched_labels = []
    for lab in labels:
        if not lab["variable"] and lab["label"] not in unmatched_labels:
            unmatched_labels.append(lab["label"])
    labels = _dedupe([lab for lab in labels if lab["variable"]])
    for f in fields:
        f["variable"] = "" if f["type"] == "checkbox" else match_variable(
            f["id"], f.get("label") or "")
    _widen_street(fields, labels)

    return {
        "path": path,
        "pages": pages,
        "fields": fields,
        "has_fields": bool(fields),
        "word_count": words,
        "image_only": words == 0,
        "labels": labels,
        # Every box no client detail was found for. Shown on the import page, so
        # nothing on a new form is left blank without anyone being told.
        "unmatched_fields": [f["id"] for f in fields
                             if f["type"] != "checkbox" and not f["variable"]],
        "checkbox_fields": [f["id"] for f in fields if f["type"] == "checkbox"],
        "unmatched_labels": unmatched_labels,
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
            out.append({
                "page": page_no,
                "label": label,
                "variable": var,
                "top": min(x["top"] for x in tail),
            })
    return out


def _widen_street(fields: list[dict], labels: list[dict]) -> None:
    """A Street box with no City box beside it is a one-line address: fill it whole.

    Otherwise a single "Street Address" line would get the street and lose the
    city, state and ZIP.
    """
    for rows in (fields, labels):
        found = {r.get("variable") for r in rows}
        if "home_line1" in found and "home_city" not in found:
            for r in rows:
                if r.get("variable") == "home_line1":
                    r["variable"] = "home_full"


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
            var = f["variable"]
            if var:
                matched += 1
            fields[f["id"]] = {"from": var} if var else {"literal": ""}
        base["fields"] = fields
        notes.append(f"This PDF has {len(info['fields'])} form field(s); "
                     f"{matched} matched a client detail.")
        if info["unmatched_fields"]:
            notes.append(f"{len(info['unmatched_fields'])} box(es) weren't recognised and "
                         "stay blank until you set them. They're listed below.")
        if any(f["type"] == "checkbox" for f in info["fields"]):
            notes.append("Checkboxes are left unticked. Give each one a rule "
                         "(check: true, or when: {field: …, equals: …}).")
        return base, notes

    # ---- flat scan ----
    if info["image_only"]:
        notes.append("This PDF has no text layer at all — it is an image. Nothing can "
                     "be detected. Create it, then click on the form in the editor to "
                     "place each value.")
        base["entries"] = []
        return base, notes

    entries = []
    for lab in info["labels"]:
        entries.append({"anchor": lab["label"], "page": lab["page"],
                        "dx": 6, "dy": 0, "from": lab["variable"]})
    base["entries"] = entries
    notes.append(f"No form fields, so this is an overlay mapping. "
                 f"{len(entries)} value(s) were placed against a label found in the text.")
    if info["unmatched_labels"]:
        notes.append(f"{len(info['unmatched_labels'])} label(s) weren't matched to a client "
                     "detail. They're listed below; place any that need filling by hand.")
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
