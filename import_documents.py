#!/usr/bin/env python3
"""
import_documents.py — put a Clerk of Circuit Court record list onto a client.

    python import_documents.py --client clients/<name>.yaml official_records.csv
    python import_documents.py --client clients/<name>.yaml -            # paste, then Ctrl-Z
    python import_documents.py --client clients/<name>.yaml releases.csv --list release
    python import_documents.py --client clients/<name>.yaml export.csv --match "SMITH LORENZO"
    python import_documents.py --client clients/<name>.yaml export.csv --persons primary spouse
    python import_documents.py --client clients/<name>.yaml export.csv --dry-run

Input is whatever the county search gives you — an export, or the results grid copied
and pasted (that arrives tab-separated, which is read the same way). The first row
must be the column headings; the ones that matter are

    Instrument Number | Book | Page | Document Title

and common spellings of each (CFN, OR Book, Doc Type, a combined "Book/Page") are
recognized. Anything else in the file is ignored and reported.

A name search matches a NAME, not a person: an export for "SMITH" holds every Smith in
the county. `--match` keeps only the rows containing the text you give it — party name,
document title, instrument number — and the preview prints the parties so you can see
what you are about to claim. Pair it with `--dry-run` before writing. (The app's
Documents screen does the same job with tick boxes.) Party names are shown, never
stored: what lands on the client is the record's own identifiers.

Rows are MERGED into the client's `documents:` list by default, because a worker
imports one search at a time — the married name, then the maiden name — and the
second import must not discard the first. Re-importing the same export changes
nothing.

Nothing here contacts the Clerk. It reads a file you already have.
"""
from __future__ import annotations

import argparse
import sys

import documents
from client_context import ClientDataError, dump_client, load_client
from paths import resolve

COLUMNS = (("instrument_number", "Instrument Number", 20),
           ("book", "Book", 8), ("page", "Page", 8), ("title", "Document Title", 30),
           ("_parties", "Parties", 34))


def _parties(row: dict) -> str:
    both = [row.get("grantor") or "", row.get("grantee") or ""]
    return " -> ".join(p for p in both if p)


def preview(rows: list[dict], limit: int = 12) -> str:
    """The imported rows as a fixed-width table, so a mistake is visible before saving."""
    head = "  ".join(title.ljust(width) for _f, title, width in COLUMNS)
    out = [head, "  ".join("-" * width for _f, _t, width in COLUMNS)]
    for row in rows[:limit]:
        cells = {**row, "_parties": _parties(row)}
        out.append("  ".join(str(cells.get(f, ""))[:width].ljust(width)
                             for f, _t, width in COLUMNS))
    if len(rows) > limit:
        out.append(f"... and {len(rows) - limit} more")
    return "\n".join(out)


def matching(rows: list[dict], needles: list[str]) -> list[dict]:
    """Rows containing every --match term, over the whole row including party names.

    The CLI's answer to the same problem the app's row picker solves: a name search
    returns every SMITH in the county, and only some of them are this client.
    """
    if not needles:
        return rows
    wanted = [n.lower() for n in needles if n.strip()]
    kept = []
    for row in rows:
        hay = " ".join(str(v) for v in row.values() if isinstance(v, str)).lower()
        if all(n in hay for n in wanted):
            kept.append(row)
    return kept


def main():
    ap = argparse.ArgumentParser(
        description="Import a Clerk of Circuit Court document list onto a client.")
    ap.add_argument("csv", help="CSV/TSV file from the Official Records search, or - for stdin")
    ap.add_argument("--client", required=True, help="path to the client YAML")
    ap.add_argument("--list", dest="listed", default=documents.DEFAULT_LIST,
                    choices=documents.LISTS,
                    help="which table these belong to (default: redact)")
    ap.add_argument("--persons", nargs="*", metavar="ROLE",
                    help="limit these documents to certain people (default: everyone)")
    ap.add_argument("--match", nargs="*", default=[], metavar="TEXT",
                    help="only import rows containing every TEXT (party name, title, "
                         "instrument number) — a name search returns more than one person")
    ap.add_argument("--replace", action="store_true",
                    help="replace this client's existing rows for that list instead of merging")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; write nothing")
    args = ap.parse_args()

    try:
        text = sys.stdin.read() if args.csv == "-" else documents.read_file(resolve(args.csv))
        parsed = documents.parse(text, default_list=args.listed)
        client = load_client(args.client)
    except (documents.DocumentImportError, ClientDataError) as exc:
        raise SystemExit(f"[FAIL] {exc}")
    except OSError as exc:
        raise SystemExit(f"[FAIL] Could not read that file: {exc}")

    read = parsed["rows"]
    if not read:
        raise SystemExit("[FAIL] No rows had an instrument number or a book and page.")
    new = matching(read, args.match)
    if not new:
        raise SystemExit(f"[FAIL] None of the {len(read)} rows match "
                         f"{', '.join(args.match)}. Nothing was written.")
    if args.persons:
        for row in new:
            row["persons"] = list(args.persons)

    existing = documents.documents(client)
    # --replace clears only the list being imported; a release import must not wipe
    # the redaction list, which is the one thing you cannot undo from the CSV.
    kept_other = [r for r in existing if r.get("list") != args.listed]
    kept_same = [] if args.replace else [r for r in existing if r.get("list") == args.listed]
    result = documents.merge(kept_same, new)

    print(f"\nRead columns: {', '.join(parsed['columns']) or '(none)'}")
    if parsed["ignored"]:
        print(f"Ignored columns: {', '.join(parsed['ignored'])}")
    if parsed["skipped"]:
        print(f"Skipped {parsed['skipped']} row(s) with no instrument number and no book/page.")
    if args.match:
        print(f"Matched {len(new)} of {len(read)} row(s) on: {', '.join(args.match)}")
    print(f"\n{preview(new)}\n")
    print(f"{args.listed}: {result['added']} added, {result['updated']} updated, "
          f"{result['duplicate']} already present — {len(result['rows'])} total.")

    if args.dry_run:
        print("\n[dry run] Nothing was written.")
        return

    # Keep the saved file in a stable order — the redaction list, then the releases —
    # so two imports in a different order produce the same YAML.
    combined = sorted(kept_other + result["rows"],
                      key=lambda r: documents.LISTS.index(r.get("list", documents.DEFAULT_LIST)))
    client = documents.set_documents(client, combined)
    path = resolve(args.client)
    path.write_text(dump_client(client), encoding="utf-8")
    print(f"\nSaved to {path}. Generate the Clerk form to see them on it:")
    print(f"  python fill_forms.py --client {args.client} --forms hillsborough_clerk_redaction")


if __name__ == "__main__":
    main()
