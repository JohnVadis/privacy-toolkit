"""
generation_log.py — a record of what was produced, for whom, from what.

A filed form is a claim made to a county under penalty of perjury. Months later the
questions are "which version of the mapping produced this?" and "is the PDF I have
the one the toolkit made?", and until now nothing could answer either: the output
folder held a PDF with no provenance at all.

So every fill appends one line here — the form, the person, a SHA-256 of the produced
file, of the mapping that produced it and of the blank PDF underneath it, plus the
library versions that did the drawing. That is enough to prove a document is
unaltered, and enough to reproduce it.

The log lives inside the client's own output folder, so it travels to trash with the
case and no second place accumulates cross-client PII. It is named with a leading dot
so the app's file list doesn't offer it as something to send to a county.

Append-only by construction: a line is added, nothing is ever rewritten. Losing the
log must never lose the filing, so every failure here is swallowed — a form that
generated is worth more than its audit line.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import platform
from pathlib import Path

LOG_NAME = ".generation-log.jsonl"

# What actually decides where ink lands on the page. A change in any of these can
# move text on a form without anything failing, which is why they are recorded
# against each document rather than assumed.
TRACKED_PACKAGES = ("pypdf", "reportlab", "pdfplumber", "pypdfium2", "openpyxl")


def _sha256(path) -> str:
    """Hex digest of a file, or "" if it can't be read."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def versions() -> dict:
    """The library versions in play, plus the Python running them."""
    from importlib.metadata import PackageNotFoundError, version

    out = {"python": platform.python_version()}
    for name in TRACKED_PACKAGES:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            continue
    return out


def entry(*, output: Path, form_key: str, person: str, mapping_path=None,
          blank_pdf=None, client: dict | None = None, extra: dict | None = None) -> dict:
    """Build the record for one generated document."""
    case = (client or {}).get("case") or {}
    record = {
        "at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "form_key": form_key,
        "person": person or "",
        "output": Path(output).name,
        "output_sha256": _sha256(output),
        "case_number": str(case.get("case_number") or ""),
        "versions": versions(),
    }
    if mapping_path:
        record["mapping"] = Path(mapping_path).name
        record["mapping_sha256"] = _sha256(mapping_path)
    if blank_pdf:
        record["blank_pdf"] = Path(blank_pdf).name
        record["blank_pdf_sha256"] = _sha256(blank_pdf)
    if extra:
        record.update(extra)
    return record


def record(output: Path, **kwargs) -> Path | None:
    """Append one line to the log beside `output`. Returns the log path, or None.

    Never raises: the form has already been written, and failing to journal it is
    not a reason to lose it.
    """
    try:
        output = Path(output)
        log = output.parent / LOG_NAME
        line = json.dumps(entry(output=output, **kwargs), ensure_ascii=False)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return log
    except Exception:
        return None


def read(output_dir) -> list[dict]:
    """Every record in a client's log, oldest first. Unreadable lines are skipped."""
    log = Path(output_dir) / LOG_NAME
    if not log.is_file():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def history(output_dir, filename: str) -> list[dict]:
    """Every time one output file was generated, newest first."""
    return [r for r in reversed(read(output_dir)) if r.get("output") == filename]


def verify(output_dir, filename: str) -> dict:
    """Is the file on disk still the one that was generated?

    Returns {status, recorded_at, ...}. "unchanged" means the bytes match the most
    recent record; "modified" means they don't — which is expected if someone signed
    or annotated the PDF, and worth knowing either way.
    """
    records = history(output_dir, filename)
    path = Path(output_dir) / filename
    if not path.is_file():
        return {"status": "missing"}
    if not records:
        return {"status": "unlogged"}

    latest = records[0]
    actual = _sha256(path)
    return {
        "status": "unchanged" if actual == latest.get("output_sha256") else "modified",
        "recorded_at": latest.get("at", ""),
        "expected_sha256": latest.get("output_sha256", ""),
        "actual_sha256": actual,
        "generations": len(records),
    }
