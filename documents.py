"""
documents.py — the client's list of recorded documents, and the CSV that fills it.

A redaction request has to name the records it applies to: the form's own warning is
that "only the documents identified by the requestor will be redacted". Those come
from a search of the Clerk's Official Records, which is a table of

    Instrument Number | Book | Page | Document Title

This module is the one place that turns such a table — exported, copied out of a
results grid, or typed at the courthouse — into rows on the client, and hands those
rows back to the fill engine. It parses; it never fetches. Nothing here opens a
network connection: how the worker obtains the export is their business, and every
path into the toolkit ends at a file or a paste.

Rows live on the client file under `documents:`, so a filing is reproducible months
later from the same YAML:

    documents:
      - instrument_number: "2019123456"
        book: "26841"
        page: "1234"
        title: "WARRANTY DEED"
        recorded: "2019-05-14"     # optional, for the worker's own reference
        list: redact               # redact (default) | release
        persons: [primary]         # optional; omitted means "every person"

`list: release` is the second table on the Clerk form — prior redactions being
released because the address is no longer the client's residence. Two lists, one
store.
"""
from __future__ import annotations

import csv
import io
import re

# What a STORED row can hold. `list` and `persons` steer which table a row lands in;
# the first four are the only ones the form actually prints.
FIELDS = ("instrument_number", "book", "page", "title", "recorded")

# Parsed and shown while choosing rows, then dropped. A name search returns every
# SMITH in the county, and the party names are the only thing on the row that says
# which ones are this client's — so they have to be visible at the moment of choosing.
# They are nobody's business afterwards: `normalize()` keeps FIELDS only, so no
# third party's name reaches a client file.
PARTY_FIELDS = ("grantor", "grantee")
PARSED_FIELDS = FIELDS + PARTY_FIELDS

LISTS = ("redact", "release")
DEFAULT_LIST = "redact"

# Header spellings seen on county exports and copied-out results grids, normalized
# (lowercased, punctuation to spaces). Add to these rather than to the parser.
ALIASES: dict[str, tuple[str, ...]] = {
    "instrument_number": (
        "instrument number", "instrument no", "instrument num", "instrument",
        "instrument number cfn", "cfn", "clerk file number", "clerks file number",
        "clerk s file number", "document number", "document no", "doc number",
        "doc no", "doc id", "recording number", "reception number", "file number",
    ),
    "book": ("book", "bk", "or book", "o r book", "official records book",
             "book number", "book no", "book num", "or bk"),
    "page": ("page", "pg", "or page", "o r page", "official records page",
             "page number", "page no", "page num", "or pg"),
    "title": ("document title", "doc title", "title", "document type", "doc type",
              "instrument type", "document description", "description", "type",
              "kind of instrument", "doc desc"),
    "recorded": ("recorded", "record date", "recorded date", "date recorded",
                 "recording date", "file date", "filed", "date"),
    "list": ("list", "action", "table", "redact or release"),
    # Party names — shown while picking rows, never stored. "Name" / "Cross-Party
    # Name" are what the results grid calls them when it is copied and pasted.
    "grantor": ("grantor", "grantors", "party 1", "direct name", "first party", "name"),
    "grantee": ("grantee", "grantees", "party 2", "reverse name", "second party",
                "cross party name", "cross party"),
}

# One column carrying both, e.g. "26841/1234" or "26841-1234".
BOOK_PAGE_HEADERS = ("book page", "book pg", "bk pg", "or book page", "book and page")

DELIMITERS = (",", "\t", ";", "|")


class DocumentImportError(Exception):
    """The pasted text or file isn't a table this can read. Message is shown as-is."""


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _norm_header(s: str) -> str:
    """'Instrument #' -> 'instrument number', 'DocType' -> 'doc type'.

    Hillsborough's own export writes its headings camelCase (DocType, BookNum,
    PageNum, RecordDate), so a capital after a lowercase letter or a digit starts a
    new word before anything is lowercased.
    """
    s = (s or "").replace("#", " number ").replace(" ", " ")
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


def _field_for(header: str, loose: bool = False) -> str | None:
    """Which canonical field a column header means, or None if unrecognized.

    `loose` also accepts a heading that STARTS with an alias ("Book (Official
    Records)"). That pass runs only after every exact match is assigned, so a
    "BookType" column can never take the slot "BookNum" was going to fill.
    """
    key = _norm_header(header)
    if not key:
        return None
    if key in BOOK_PAGE_HEADERS:
        return "book_page"
    for field, names in ALIASES.items():
        if key in names:
            return field
    if loose:
        for field, names in ALIASES.items():
            if any(key.startswith(n + " ") for n in names):
                return field
    return None


def _sniff(text: str) -> str:
    """Delimiter of the header line: whichever splits it into the most columns.

    Tab wins ties, because text copied out of a results grid is tab-separated and a
    document title may legitimately contain a comma.
    """
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    counts = {d: line.count(d) for d in DELIMITERS}
    best = max(counts.values()) if counts else 0
    if best == 0:
        return ","
    for d in ("\t", ",", ";", "|"):
        if counts[d] == best:
            return d
    return ","


def _clean(v) -> str:
    """Trim, collapse whitespace, drop a stray wrapping quote."""
    s = re.sub(r"\s+", " ", str(v if v is not None else "")).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _split_book_page(v: str) -> tuple[str, str]:
    m = re.match(r"^\s*(\d+)\s*[/\-]\s*(\d+)\s*$", v or "")
    return (m.group(1), m.group(2)) if m else ((v or "").strip(), "")


def parse(text: str, default_list: str = DEFAULT_LIST) -> dict:
    """Parse a CSV/TSV table into rows. Returns {rows, columns, ignored, skipped}.

    Raises DocumentImportError when no header row is recognizable — guessing which
    unlabelled column held the instrument number is exactly the kind of judgement
    that produces a wrong filing, so this refuses instead of assuming.
    """
    if default_list not in LISTS:
        raise DocumentImportError(f"list must be one of {', '.join(LISTS)}")
    text = (text or "").lstrip("﻿")
    if not text.strip():
        raise DocumentImportError("Nothing to import — the file or paste is empty.")

    reader = csv.reader(io.StringIO(text), delimiter=_sniff(text))
    table = [r for r in reader if any(_clean(c) for c in r)]
    if not table:
        raise DocumentImportError("Nothing to import — no rows found.")

    header, body = table[0], table[1:]
    columns = {}
    # Exact heading matches first, then the looser prefix pass over what's left —
    # otherwise a "BookType" column claims Book before "BookNum" is even looked at.
    for loose in (False, True):
        for i, cell in enumerate(header):
            if i in columns:
                continue
            field = _field_for(cell, loose=loose)
            if field and field not in columns.values():
                columns[i] = field
    ignored = [_clean(c) for i, c in enumerate(header)
               if i not in columns and _clean(c)]

    recognized = set(columns.values())
    if not recognized & {"instrument_number", "book", "page", "book_page"}:
        found = ", ".join(_clean(c) for c in header if _clean(c)) or "(blank)"
        raise DocumentImportError(
            "Couldn't find an Instrument Number, Book or Page column. The first row "
            f"must be the column headings — found: {found}.")

    rows, skipped = [], 0
    for raw in body:
        row = _row_from(raw, columns, default_list)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    named = sorted(recognized - {"book_page", "list"})
    if "book_page" in recognized:
        named += ["book", "page"]
    return {"rows": rows, "columns": named, "ignored": ignored, "skipped": skipped}


def _row_from(raw: list, columns: dict, default_list: str) -> dict | None:
    """One CSV line -> a document row, or None if it identifies no record."""
    row = {f: "" for f in PARSED_FIELDS}
    row["list"] = default_list
    for i, field in columns.items():
        value = _clean(raw[i]) if i < len(raw) else ""
        if field == "book_page":
            row["book"], row["page"] = _split_book_page(value)
        elif field == "list":
            row["list"] = value.lower() if value.lower() in LISTS else default_list
        elif field == "recorded":
            # The export stamps a clock time ("2026-09-22 11:28 AM"); the date is the
            # part anyone refers to, and this is never printed on a form anyway.
            row[field] = re.sub(r"\s+\d{1,2}:\d{2}(:\d{2})?\s*([AaPp]\.?[Mm]\.?)?$",
                                "", value).strip()
        else:
            row[field] = value
    # A row has to point at a record. Everything else about it is optional.
    if not (row["instrument_number"] or (row["book"] and row["page"])):
        return None
    return row


def decode(data: bytes) -> str:
    """Bytes from a file or an upload as text, tolerating a BOM and a cp1252 export.

    The NUL check is what actually catches a mis-picked file: latin-1 decodes any
    byte sequence at all, so without it a .xlsx or a PDF came back as mojibake and
    failed later with a confusing complaint about column headings.
    """
    if b"\x00" in data[:4096]:
        raise DocumentImportError(
            "That looks like a spreadsheet or a PDF rather than a CSV. Use the "
            "search's Export to Spreadsheet (it writes a .csv), or paste the rows.")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentImportError("That file isn't text — export the search as CSV and "
                              "try again.")


def read_file(path) -> str:
    """Read a CSV/TSV file as text."""
    with open(path, "rb") as fh:
        return decode(fh.read())


# --------------------------------------------------------------------------
# the store on the client
# --------------------------------------------------------------------------
def key(row: dict) -> tuple:
    """Identity of a record: the instrument number, or book/page when there is none."""
    inst = (row.get("instrument_number") or "").strip().lower()
    if inst:
        return ("i", inst)
    return ("bp", (row.get("book") or "").strip(), (row.get("page") or "").strip())


def normalize(row: dict) -> dict:
    """A stored row: known fields, cleaned, with empties dropped."""
    out = {f: _clean(row.get(f)) for f in FIELDS}
    out = {k: v for k, v in out.items() if v}
    listed = str(row.get("list") or DEFAULT_LIST).lower()
    out["list"] = listed if listed in LISTS else DEFAULT_LIST
    persons = row.get("persons")
    if isinstance(persons, (list, tuple)) and persons:
        out["persons"] = [str(p) for p in persons]
    return out


def documents(client: dict) -> list[dict]:
    """Every stored row, normalized. A missing or malformed `documents:` reads empty."""
    raw = client.get("documents")
    if not isinstance(raw, list):
        return []
    return [normalize(r) for r in raw if isinstance(r, dict)]


def merge(existing: list[dict], new: list[dict], replace: bool = False) -> dict:
    """Combine stored rows with imported ones. Returns {rows, added, updated, duplicate}.

    Merging is the default because a worker imports one search at a time — the
    client's married name, then the maiden name — and a second import must not throw
    the first away. A repeated record updates the row it matches instead of appearing
    twice, so re-importing a wider export is safe.
    """
    kept: list[dict] = []
    index: dict[tuple, int] = {}
    stats = {"added": 0, "updated": 0, "duplicate": 0}

    def absorb(row: dict, count: bool) -> None:
        row = normalize(row)
        k = key(row)
        if k in index:
            before = kept[index[k]]
            after = {**before, **{f: v for f, v in row.items() if v}}
            if after != before:
                kept[index[k]] = after
                if count:
                    stats["updated"] += 1
            elif count:
                stats["duplicate"] += 1
            return
        index[k] = len(kept)
        kept.append(row)
        if count:
            stats["added"] += 1

    # The stored rows go through the same door, so a file that already held the same
    # record twice comes back holding it once.
    if not replace:
        for r in existing:
            absorb(r, count=False)
    for r in new:
        absorb(r, count=True)
    return {"rows": kept, **stats}


def set_documents(client: dict, rows: list[dict]) -> dict:
    """Return the client with `documents:` set, positioned right after `persons:`.

    Rebuilds the dict so the key lands in a predictable place in the saved YAML
    rather than wherever it happened to be written first.
    """
    rows = [normalize(r) for r in rows]
    out = {}
    for k, v in client.items():
        if k == "documents":
            continue
        out[k] = v
        if k == "persons" and rows:
            out["documents"] = rows
    if rows and "documents" not in out:
        out["documents"] = rows
    return out


def for_form(client: dict, listed: str = DEFAULT_LIST,
             person: str | None = None) -> list[dict]:
    """The rows one table on one person's form should print, in stored order.

    A row with `persons:` is limited to those roles — a child's form shouldn't name
    records that say nothing about the child. A row without it applies to everyone.
    """
    rows = []
    for r in documents(client):
        if r.get("list", DEFAULT_LIST) != listed:
            continue
        who = r.get("persons")
        if who and person and person not in who:
            continue
        rows.append(r)
    return rows


def counts(client: dict) -> dict:
    """{redact: n, release: n} — what the client currently has stored."""
    rows = documents(client)
    return {name: sum(1 for r in rows if r.get("list", DEFAULT_LIST) == name)
            for name in LISTS}
