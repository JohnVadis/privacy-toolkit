#!/usr/bin/env python3
"""
build_worklist.py — generate a per-client opt-out worklist spreadsheet.

    python build_worklist.py --client clients/example_client.yaml

Reads sites.yaml + the client file, pre-fills the values each site asks for, and
writes output/<client_slug>/opt_out_worklist.xlsx with status-tracking columns and
a dropdown. The worker fills in status/dates as they go — that spreadsheet is the
tracker (no database needed at this volume). Zero AI, zero tokens.

Opt-out sites are PREPARED here, never submitted: they gate on CAPTCHAs and email
verification, and a human does the clicking. Nothing in this file talks to a site.
"""
from __future__ import annotations
import argparse
import datetime as _dt
import shutil
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

from client_context import (ClientDataError, build_context, load_client,
                            output_dir)
from paths import SITES_FILE, resolve

STATUSES = ["not_started", "submitted", "verification_sent", "verified",
            "removed", "rejected", "not_removable"]

HEADERS = ["Site", "Category", "Opt-out URL", "Prefilled values (copy/paste)",
           "Supports agent?", "Status", "Submitted", "Confirmation / ref",
           "Verify by", "Notes", "Site guidance", "Key"]

# Columns the WORKER owns. Rebuilding the sheet carries these across; everything
# else is regenerated from sites.yaml and the client file.
#
# "Notes" and "Site guidance" used to be one column, which made merging ambiguous:
# there was no way to tell a worker's note from the catalog's own text. They are two
# columns now — the worker's is preserved, the catalog's is refreshed.
USER_COLUMNS = ("Status", "Submitted", "Confirmation / ref", "Verify by", "Notes")

KEY_COLUMN = "Key"
HEADER_ROW = 4
WORKLIST_FILENAME = "opt_out_worklist.xlsx"
BACKUP_DIRNAME = "_worklist_backups"


def load_sites(sites_path=None) -> list[dict]:
    """The opt-out catalog. Also backs the read-only sites table in the web UI."""
    with open(resolve(sites_path or SITES_FILE), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def read_tracking(path) -> dict:
    """What the worker has already recorded in an existing worklist.

    Returns {site key or site name: {column: value}} for the USER_COLUMNS only.
    Matching prefers the hidden Key column; a sheet built before that column existed
    is matched on the site's name instead.

    Anything unreadable returns {} — the caller backs the old file up before writing,
    so a sheet this cannot parse is preserved on disk rather than silently dropped.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        from openpyxl import load_workbook

        wb = load_workbook(path, data_only=True)
        ws = wb.active
        headers = [c.value for c in ws[HEADER_ROW]]
        index = {h: i for i, h in enumerate(headers) if h}
        if "Site" not in index:
            return {}

        tracked = {}
        for row in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
            if not any(v not in (None, "") for v in row):
                continue

            def cell(name):
                i = index.get(name)
                return row[i] if i is not None and i < len(row) else None

            values = {c: cell(c) for c in USER_COLUMNS}
            # Kept so a row whose site later leaves the catalog can still be labelled
            # with the name the worker saw, rather than an internal key.
            values["_site"] = cell("Site")
            # "not_started" with nothing else filled in is an untouched row.
            touched = any(str(v).strip() for c, v in values.items()
                          if v is not None and not (c == "Status" and
                                                    str(v).strip() in ("", "not_started")))
            if not touched:
                continue
            key = cell(KEY_COLUMN) or cell("Site")
            if key:
                tracked[str(key).strip()] = values
        return tracked
    except Exception:
        return {}


def _backup_existing(path: Path) -> Path | None:
    """Copy the current worklist aside before it is rebuilt. Returns where, or None.

    Merging is careful, but the file being replaced is weeks of someone's manual
    work — worth one copy in a folder the UI doesn't list.
    """
    if not path.is_file():
        return None
    dest_dir = path.parent / BACKUP_DIRNAME
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{path.stem}.{_dt.datetime.now():%Y-%m-%d_%H%M%S}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def prefilled(site: dict, ctx: dict) -> str:
    parts = []
    for var in site.get("needs", []):
        val = ctx.get(var, "")
        if val:
            parts.append(f"{var}: {val}")
    return "  |  ".join(parts)


def build_worklist(client: dict, person: str | None = None, sites_path=None,
                   stats: dict | None = None) -> Path:
    """Write the client's opt-out worklist and return its path.

    The one entry point; the CLI below and webapp/ both call it.

    Rebuilding MERGES: Status, Submitted, Confirmation, Verify-by and Notes are read
    back out of the existing sheet and carried across. It used to overwrite, so
    rebuilding after adding one site — or building for a second person — silently
    threw away every status the worker had recorded. A site that has since left the
    catalog keeps its row if there is work recorded against it.

    `stats`, if given, is filled in with what happened, for the UI to report.
    """
    ctx = build_context(client, person)
    sites = load_sites(sites_path)
    out_path = output_dir(client) / WORKLIST_FILENAME
    tracked = read_tracking(out_path)
    used_keys = set()

    wb = Workbook()
    ws = wb.active
    ws.title = "Opt-out worklist"

    title = f"Opt-out worklist — {ctx['case_display_name'] or ctx['full_name']}"
    ws["A1"] = title
    ws["A1"].font = Font(name="Arial", size=13, bold=True)
    ws["A2"] = ("Prefilled values come from the client file. Fill in Status/Submitted/"
                "Confirmation as you go. Re-check removals after ~2 weeks.")
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="555555")

    header_row = HEADER_ROW
    head_fill = PatternFill("solid", fgColor="1F3864")
    head_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for c, h in enumerate(HEADERS, start=1):
        cell = ws.cell(row=header_row, column=c, value=h)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = border

    def write_row(row_index: int, values: list) -> None:
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row=row_index, column=c, value=v)
            cell.font = Font(name="Arial", size=10)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border

    counts = {"sites": len(sites), "preserved": 0, "carried_over": 0}
    r = header_row + 1

    for site in sites:
        key = str(site.get("key") or site.get("name") or "")
        name = site.get("name", key)
        # Match on the stable key first, then on the displayed name, which is how a
        # sheet built before the Key column existed has to be matched.
        prior = tracked.get(key) or tracked.get(name) or {}
        if prior:
            counts["preserved"] += 1
            used_keys.update({key, name} & set(tracked))

        write_row(r, [
            name,
            site.get("category", ""),
            site.get("opt_out_url", ""),
            prefilled(site, ctx),
            "yes" if site.get("supports_agent") else "no",
            prior.get("Status") or "not_started",
            prior.get("Submitted") or "",
            prior.get("Confirmation / ref") or "",
            prior.get("Verify by") or "",
            prior.get("Notes") or "",
            site.get("notes", ""),
            key,
        ])
        r += 1

    # Work recorded against a site that has since left sites.yaml. Dropping the row
    # would delete the only record that the submission ever happened.
    for key, prior in tracked.items():
        if key in used_keys:
            continue
        counts["carried_over"] += 1
        write_row(r, [
            prior.get("_site") or key, "(no longer in catalog)", "", "", "",
            prior.get("Status") or "", prior.get("Submitted") or "",
            prior.get("Confirmation / ref") or "", prior.get("Verify by") or "",
            prior.get("Notes") or "", "", key,
        ])
        r += 1

    # Status dropdown on the Status column (F), for the data rows.
    if r > header_row + 1:
        dv = DataValidation(type="list", formula1='"%s"' % ",".join(STATUSES),
                            allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"F{header_row+1}:F{r-1}")

    widths = [22, 14, 40, 46, 14, 16, 12, 20, 12, 34, 40, 16]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.column_dimensions[get_column_letter(len(HEADERS))].hidden = True  # Key
    ws.freeze_panes = f"A{header_row+1}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_existing(out_path)
    try:
        wb.save(out_path)
    except PermissionError:
        raise ClientDataError(
            f"{out_path.name} is open in another program. Close it and build again — "
            "nothing was changed.")

    if stats is not None:
        stats.update(counts, backup=backup)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Build an opt-out worklist for a client.")
    ap.add_argument("--client", required=True)
    ap.add_argument("--person", help="person role to use (default: first/primary)")
    ap.add_argument("--sites", default=None, help="site catalog (default: sites.yaml)")
    args = ap.parse_args()

    try:
        client = load_client(args.client)
        stats = {}
        out_path = build_worklist(client, args.person, args.sites, stats=stats)
    except ClientDataError as exc:
        raise SystemExit(str(exc))

    print(f"[ok] wrote {out_path}  ({stats['sites']} sites)")
    if stats.get("preserved"):
        print(f"     kept the tracked status of {stats['preserved']} site(s) already worked")
    if stats.get("carried_over"):
        print(f"     carried over {stats['carried_over']} row(s) for sites no longer in the catalog")
    if stats.get("backup"):
        print(f"     previous version backed up to {stats['backup'].parent.name}/{stats['backup'].name}")


if __name__ == "__main__":
    main()
