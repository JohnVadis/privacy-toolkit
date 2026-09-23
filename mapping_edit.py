"""
mapping_edit.py — read, check and save a form mapping, with a backup every time.

A mapping is the highest-leverage file in the repo: it is written once and then used
for EVERY client and every filing of that form. A wrong coordinate here does not fail
loudly — it produces a government form with a mark in the wrong box, for everyone,
until a human notices. So saving goes through `check_mapping()` first and always
leaves the previous version in `forms/mappings/_backups/`.

Engine-side, not web-side, so the CLI can use the same checks.
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path

import yaml

from paths import MAPPINGS_DIR, resolve

BACKUP_DIR = MAPPINGS_DIR / "_backups"

# Keys an overlay entry may carry. Anything else is a typo that would be silently
# ignored by the engine, which is exactly the kind of thing to catch here.
ENTRY_KEYS = {"page", "x", "y", "anchor", "occurrence", "dx", "dy", "size",
              "from", "literal", "mark", "check", "when", "note"}
TOP_KEYS = {"form_key", "title", "county", "mode", "pdf", "entries", "fields", "tables"}

# A `tables:` block repeats one row of the client's `documents:` list per ruled line.
TABLE_KEYS = {"key", "list", "heading", "attachment", "continued", "page", "rows",
              "first_y", "row_pitch", "size", "note_size", "columns"}
COLUMN_KEYS = {"from", "x", "width", "size"}


def mapping_file(form_key: str) -> Path:
    return MAPPINGS_DIR / f"{form_key}.map.yaml"


def read_mapping_text(form_key: str) -> str:
    return mapping_file(form_key).read_text(encoding="utf-8")


def check_mapping(text: str, expect_key: str | None = None) -> tuple[dict | None, list[str]]:
    """Parse and sanity-check a mapping. Returns (parsed_or_None, [problems]).

    Structural only — it cannot know whether a coordinate is the RIGHT box. That is
    what the live preview in the editor is for, and what
    docs/verification-checklist.md asks a human to confirm before a first filing.
    """
    problems: list[str] = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [f"This isn't valid YAML: {exc}"]

    if not isinstance(data, dict):
        return None, ["A mapping must be a block of settings, not a list or a value."]

    for key in sorted(set(data) - TOP_KEYS):
        problems.append(f"Unknown setting '{key}' — it would be ignored.")

    key = data.get("form_key")
    if not key:
        problems.append("form_key is required.")
    elif expect_key and key != expect_key:
        problems.append(f"form_key is '{key}' but this file is {expect_key}.map.yaml — "
                        "they must match.")

    pdf = data.get("pdf")
    if not pdf:
        problems.append("pdf: is required — the blank form to fill.")
    elif not resolve(pdf).is_file():
        problems.append(f"No blank PDF at {pdf}")

    mode = data.get("mode", "fillable")
    if mode not in ("fillable", "overlay"):
        problems.append(f"mode must be 'fillable' or 'overlay', not '{mode}'.")

    if mode == "overlay":
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            problems.append("An overlay mapping needs a list of entries.")
        else:
            problems += _check_entries(entries, pdf)
    else:
        if not isinstance(data.get("fields"), dict):
            problems.append("A fillable mapping needs a fields: block.")

    if "tables" in data:
        if mode != "overlay":
            problems.append("tables: only works in overlay mode — it stamps by coordinate.")
        problems += _check_tables(data.get("tables"), pdf)

    return data, problems


def _check_tables(tables, pdf) -> list[str]:
    """Structural check on `tables:` — the same class of error as a stray coordinate.

    A table is written once and repeats for every client, so a row pitch that walks
    off the bottom of the page is worth catching here rather than in a filing.
    """
    from documents import FIELDS, LISTS

    if not isinstance(tables, list) or not tables:
        return ["tables: must be a list of table blocks."]

    try:
        import pdf_render
        pages = pdf_render.page_count(pdf) if pdf and resolve(pdf).is_file() else None
        size = pdf_render.page_size(pdf) if pages else None
    except Exception:
        pages, size = None, None

    problems = []
    for i, t in enumerate(tables, start=1):
        where = f"Table {i}"
        if not isinstance(t, dict):
            problems.append(f"{where} is not a set of settings.")
            continue
        for k in sorted(set(t) - TABLE_KEYS):
            problems.append(f"{where}: unknown setting '{k}' — it would be ignored.")

        listed = t.get("list", "redact")
        if listed not in LISTS:
            problems.append(f"{where}: list: must be one of {', '.join(LISTS)}.")

        page = t.get("page", 1)
        if pages and isinstance(page, int) and not 1 <= page <= pages:
            problems.append(f"{where}: page {page}, but the form has {pages} page(s).")

        try:
            rows = int(t.get("rows") or 0)
            first_y = float(t.get("first_y"))
            pitch = float(t.get("row_pitch", 0))
        except (TypeError, ValueError):
            problems.append(f"{where}: rows, first_y and row_pitch must be numbers.")
            continue
        if rows < 1:
            problems.append(f"{where}: rows: must be at least 1 — it is how many ruled "
                            "lines the form has.")
        # The note about a continuation sheet is written one pitch below the last row,
        # so that line has to be on the page too.
        last_y = first_y - (rows * pitch)
        if size and not (0 <= last_y <= size[1] and 0 <= first_y <= size[1]):
            problems.append(f"{where}: rows run from y={first_y:g} to y={last_y:g}, "
                            f"off a {size[1]:g}pt page.")

        columns = t.get("columns")
        if not isinstance(columns, list) or not columns:
            problems.append(f"{where}: needs a columns: list.")
            continue
        for j, c in enumerate(columns, start=1):
            at = f"{where}, column {j}"
            if not isinstance(c, dict):
                problems.append(f"{at} is not a set of settings.")
                continue
            for k in sorted(set(c) - COLUMN_KEYS):
                problems.append(f"{at}: unknown setting '{k}' — it would be ignored.")
            if c.get("from") not in FIELDS:
                problems.append(f"{at}: from: must be one of {', '.join(FIELDS)}.")
            try:
                x = float(c["x"])
            except (KeyError, TypeError, ValueError):
                problems.append(f"{at}: x: must be a number.")
                continue
            if size and not 0 <= x <= size[0]:
                problems.append(f"{at}: x={x:g} is off a {size[0]:g}pt-wide page.")
    return problems


def _check_entries(entries, pdf) -> list[str]:
    problems = []
    try:
        import pdf_render
        pages = pdf_render.page_count(pdf) if pdf and resolve(pdf).is_file() else None
        size = pdf_render.page_size(pdf) if pages else None
    except Exception:
        pages, size = None, None

    for i, e in enumerate(entries, start=1):
        if not isinstance(e, dict):
            problems.append(f"Entry {i} is not a set of settings.")
            continue
        for k in sorted(set(e) - ENTRY_KEYS):
            problems.append(f"Entry {i}: unknown setting '{k}' — it would be ignored.")

        has_where = ("anchor" in e) or ("x" in e and "y" in e)
        if not has_where:
            problems.append(f"Entry {i}: needs either an anchor, or both x and y.")
        has_what = any(k in e for k in ("from", "literal", "mark"))
        if not has_what:
            problems.append(f"Entry {i}: needs from:, literal: or mark:.")

        page = e.get("page", 1)
        if pages and isinstance(page, int) and not 1 <= page <= pages:
            problems.append(f"Entry {i}: page {page}, but the form has {pages} page(s).")
        if size and "x" in e and "y" in e:
            try:
                x, y = float(e["x"]), float(e["y"])
                w, h = size
                if not (0 <= x <= w and 0 <= y <= h):
                    problems.append(f"Entry {i}: x={x:g}, y={y:g} is off the page "
                                    f"({w:g} x {h:g} points).")
            except (TypeError, ValueError):
                problems.append(f"Entry {i}: x and y must be numbers.")
    return problems


_NUM = r"[-+]?\d*\.?\d+"


def move_entries(text: str, moves: dict[int, tuple[float, float]]) -> tuple[str, list[str]]:
    """Reposition entries in a mapping's TEXT, in place. Returns (text, notes).

    Edits the numbers where they sit rather than re-dumping the parsed YAML, because
    re-dumping would strip the comments that explain what every coordinate is for —
    and on these mappings those comments are most of the documentation.

    An absolute entry gets new x/y. An ANCHORED entry keeps its anchor and gets new
    dx/dy instead, so it stays resilient to the county reflowing the form; the caller
    passes the offset from the anchor's own position.
    """
    if not moves:
        return text, []

    lines = text.splitlines(keepends=True)
    spans = _entry_spans(lines)
    notes: list[str] = []

    for index, (x, y) in sorted(moves.items()):
        if index not in spans:
            notes.append(f"Entry {index + 1} is no longer in the file; its move was dropped.")
            continue
        start, end = spans[index]
        chunk = "".join(lines[start:end])
        anchored = re.search(r"\banchor\s*:", chunk) is not None
        kx, ky = ("dx", "dy") if anchored else ("x", "y")
        chunk, ok_x = _set_number(chunk, kx, x)
        chunk, ok_y = _set_number(chunk, ky, y)
        if not (ok_x and ok_y):
            notes.append(f"Entry {index + 1}: could not find {kx}/{ky} to update, so it "
                         "was left where it was.")
            continue
        lines[start:end] = [chunk]
        spans = _entry_spans(lines)          # line count may have changed
    return "".join(lines), notes


def _entry_spans(lines: list[str]) -> dict[int, tuple[int, int]]:
    """{entry index: (first line, last line + 1)} for the items under `entries:`."""
    spans: dict[int, tuple[int, int]] = {}
    in_entries = False
    indent = None
    index = -1
    start = 0
    # Where the entries block stops. It is NOT always the end of the file: a mapping
    # can carry top-level keys after `entries:` — `tables:` does. Ending the last
    # entry's span at len(lines) made it swallow them, so rewriting or deleting the
    # final entry deleted the document table with it, and check_mapping accepted the
    # result because a mapping without a tables block is still perfectly valid.
    block_end = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^\s*entries\s*:", line):
            in_entries = True
            continue
        if not in_entries:
            continue
        stripped = line.lstrip()
        # A non-indented, non-list line ends the entries block.
        if stripped and not line[0].isspace() and not stripped.startswith("-"):
            block_end = i
            break
        m = re.match(r"^(\s*)-\s", line)
        if m:
            if indent is None:
                indent = len(m.group(1))
            if len(m.group(1)) == indent:
                if index >= 0:
                    spans[index] = (start, i)
                index += 1
                start = i
    if index >= 0:
        spans[index] = (start, block_end)

    # An entry's span must cover its own lines ONLY. These mappings put a group
    # comment above the entries it describes, so a span that ran to the next '-'
    # swallowed the heading belonging to the following entry — and rewriting or
    # deleting one entry then destroyed the next one's documentation.
    return {i: (s, _trim_trailing(lines, s, e)) for i, (s, e) in spans.items()}


def _trim_trailing(lines: list[str], start: int, end: int) -> int:
    """Pull `end` back past any blank or comment-only lines at the tail of a span."""
    while end - 1 > start:
        stripped = lines[end - 1].strip()
        if stripped and not stripped.startswith("#"):
            break
        end -= 1
    return end


def _set_number(chunk: str, key: str, value: float) -> tuple[str, bool]:
    """Replace `key: <number>` in this entry, or insert it if absent."""
    pretty = f"{value:g}"
    pattern = re.compile(rf"(\b{key}\s*:\s*){_NUM}")
    if pattern.search(chunk):
        return pattern.sub(rf"\g<1>{pretty}", chunk, count=1), True

    # Not present — add it after the opening of a flow map, or as a new block line.
    flow = re.search(r"-\s*\{\s*", chunk)
    if flow:
        at = flow.end()
        return chunk[:at] + f"{key}: {pretty}, " + chunk[at:], True
    m = re.match(r"^(\s*)-\s", chunk)
    if m:
        pad = " " * (len(m.group(1)) + 2)
        return chunk.rstrip("\n") + f"\n{pad}{key}: {pretty}\n", True
    return chunk, False


def update_entries(text: str, changes: dict[int, dict]) -> tuple[str, list[str]]:
    """Rewrite whole entries by index, keeping everything around them.

    The entry itself is replaced with a fresh one-line flow map, because what changed
    is its meaning (which variable, whether it's a tick) rather than a number. The
    comments between entries — which are what explain the mapping — are untouched.
    """
    if not changes:
        return text, []

    lines = text.splitlines(keepends=True)
    notes: list[str] = []
    for index in sorted(changes, reverse=True):     # bottom-up keeps spans valid
        spans = _entry_spans(lines)
        if index not in spans:
            notes.append(f"Entry {index + 1} is no longer in the file; its change was dropped.")
            continue
        start, end = spans[index]
        indent = re.match(r"^(\s*)", lines[start]).group(1)
        # Carry the entry's own trailing note across — on these mappings it says
        # which line on the form the entry writes to, which stays true after a
        # change of variable or a nudge.
        note = _trailing_comment("".join(lines[start:end]))
        rewritten = f"{indent}- {format_entry(changes[index])}"
        lines[start:end] = [f"{rewritten}{note}\n"]
    return "".join(lines), notes


def _trailing_comment(chunk: str) -> str:
    """The `  # …` at the end of an entry, or '' — quotes and URLs don't count."""
    line = chunk.rstrip("\n").splitlines()[-1] if chunk.strip() else ""
    depth_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            depth_quote = not depth_quote
        elif ch == "#" and not depth_quote:
            return "  " + line[i:].strip()
    return ""


def remove_entries(text: str, indexes) -> tuple[str, list[str]]:
    """Delete entries by index, bottom-up so the remaining spans stay valid."""
    wanted = sorted({int(i) for i in indexes}, reverse=True)
    if not wanted:
        return text, []

    lines = text.splitlines(keepends=True)
    notes: list[str] = []
    for index in wanted:
        spans = _entry_spans(lines)
        if index not in spans:
            notes.append(f"Entry {index + 1} was already gone.")
            continue
        start, end = spans[index]
        del lines[start:end]
    return "".join(lines), notes


def add_entries(text: str, entries: list[dict]) -> tuple[str, list[str]]:
    """Append new entries to the end of the entries list."""
    if not entries:
        return text, []

    lines = text.splitlines(keepends=True)
    spans = _entry_spans(lines)
    if spans:
        indent = re.match(r"^(\s*)", lines[spans[max(spans)][0]]).group(1)
        at = spans[max(spans)][1]
    else:
        # An empty `entries:` list — put the first item just after it.
        at = next((i + 1 for i, l in enumerate(lines)
                   if re.match(r"^\s*entries\s*:", l)), len(lines))
        indent = "  "
        # `entries: []` has to lose its inline empty list first.
        for i, line in enumerate(lines):
            if re.match(r"^\s*entries\s*:\s*\[\s*\]\s*$", line):
                lines[i] = re.sub(r":\s*\[\s*\]\s*$", ":\n", line)
                break

    block = [f"{indent}- {format_entry(e)}\n" for e in entries]
    lines[at:at] = block
    return "".join(lines), []


def format_entry(entry: dict) -> str:
    """One entry as a compact flow map, in the order the existing mappings use."""
    order = ["page", "x", "y", "anchor", "occurrence", "dx", "dy", "size",
             "from", "literal", "mark", "check", "when", "note"]
    parts = []
    for key in order:
        if key not in entry or entry[key] is None:
            continue
        parts.append(f"{key}: {_scalar(entry[key])}")
    return "{ " + ", ".join(parts) + " }"


def _scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k}: {_scalar(v)}" for k, v in value.items()) + " }"
    text = str(value)
    # Quote anything that isn't a plain, unambiguous word.
    if not text or re.search(r"[:#,{}\[\]&*!|>'\"%@`]", text) or text != text.strip():
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def backup_mapping(form_key: str) -> Path | None:
    """Copy the current mapping aside before overwriting it."""
    src = mapping_file(form_key)
    if not src.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = BACKUP_DIR / f"{form_key}.{stamp}.map.yaml"
    shutil.copy2(src, dest)
    return dest


def save_mapping(form_key: str, text: str) -> tuple[Path, Path | None, list[str]]:
    """Check, back up, then write. Returns (path, backup_path, problems).

    Refuses to write when there are problems — the file on disk is left untouched.
    """
    _, problems = check_mapping(text, expect_key=form_key)
    if problems:
        return mapping_file(form_key), None, problems
    backup = backup_mapping(form_key)
    path = mapping_file(form_key)
    path.write_text(text, encoding="utf-8")
    return path, backup, []


def list_backups(form_key: str) -> list[Path]:
    if not BACKUP_DIR.is_dir():
        return []
    return sorted(BACKUP_DIR.glob(f"{form_key}.*.map.yaml"), reverse=True)


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Check a form mapping for problems.")
    ap.add_argument("form_key", nargs="?", help="a form key (default: check them all)")
    args = ap.parse_args()

    keys = ([args.form_key] if args.form_key
            else [p.name.split(".")[0] for p in sorted(MAPPINGS_DIR.glob("*.map.yaml"))])
    bad = 0
    for key in keys:
        try:
            _, problems = check_mapping(read_mapping_text(key), expect_key=key)
        except FileNotFoundError:
            print(f"[FAIL] {key}: no such mapping")
            bad += 1
            continue
        if problems:
            bad += 1
            print(f"[FAIL] {key}: {len(problems)} problem(s)")
            for p in problems:
                print(f"    {p}")
        else:
            print(f"[ok]   {key}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
