#!/usr/bin/env python3
"""
housekeeping.py — see what client data has piled up, and remove what's past its date.

    python housekeeping.py                      # just report; removes nothing
    python housekeeping.py --prune --dry-run    # what --prune would remove
    python housekeeping.py --prune              # remove it
    python housekeeping.py --prune --trash-days 30 --feedback-days 7

Two folders here fill up with client data as a side effect of being careful:

  * `trash/` holds deleted clients — their file and every form generated for them —
    so a misclick is recoverable. Nothing ever emptied it.
  * `feedback/` holds screenshots of the app taken for the Comment feature. A
    screenshot of this app is client data by definition.

Keeping either forever is the opposite of a privacy tool. Keeping neither makes
deletion irreversible and comments un-resendable. So: a default retention, a report
you can run any time, and a prune that always tells you what it did.

Pruning is deliberate. Nothing here runs on a schedule and nothing runs at startup —
this is a command a person types, because "the tool quietly deleted the case I was
trying to restore" is the one failure worse than a folder that grew too big.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
from pathlib import Path

from paths import ROOT, TRASH_DIR, contains

FEEDBACK_DIR = ROOT / "feedback"

# How long each is kept by default. Trash gets longer because restoring a client is
# the whole reason it exists; a screenshot is a transient thing by comparison.
TRASH_DAYS = 90
FEEDBACK_DAYS = 30


def _age_days(path: Path) -> float:
    try:
        return (_dt.datetime.now().timestamp() - path.stat().st_mtime) / 86400
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def entries(folder: Path) -> list[dict]:
    """Top-level items in a folder, with age and size. Newest first."""
    if not folder.is_dir():
        return []
    rows = [{"path": p, "name": p.name, "age_days": _age_days(p), "bytes": _size(p)}
            for p in folder.iterdir()]
    rows.sort(key=lambda r: r["age_days"])
    return rows


def status() -> dict:
    """What is sitting in each folder right now."""
    out = {}
    for name, folder, days in (("trash", TRASH_DIR, TRASH_DAYS),
                               ("feedback", FEEDBACK_DIR, FEEDBACK_DAYS)):
        rows = entries(folder)
        out[name] = {
            "folder": folder,
            "keep_days": days,
            "items": len(rows),
            "bytes": sum(r["bytes"] for r in rows),
            "oldest_days": max((r["age_days"] for r in rows), default=0.0),
            "stale": [r for r in rows if r["age_days"] > days],
        }
    return out


def prune(folder: Path, keep_days: int, dry_run: bool = False) -> list[dict]:
    """Remove items older than keep_days. Returns what was (or would be) removed.

    Refuses to touch anything outside the folder it was given, because this is the
    one function in the toolkit whose whole job is deleting client data.
    """
    removed = []
    for row in entries(folder):
        if row["age_days"] <= keep_days:
            continue
        path = row["path"]
        if not contains(folder, path) or path.resolve() == folder.resolve():
            continue
        if not dry_run:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        removed.append(row)
    return removed


def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.0f}"


def main():
    ap = argparse.ArgumentParser(
        description="Report or prune the folders that accumulate client data.")
    ap.add_argument("--prune", action="store_true",
                    help="remove items past their retention (default: report only)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --prune, list what would go without removing it")
    ap.add_argument("--trash-days", type=int, default=TRASH_DAYS)
    ap.add_argument("--feedback-days", type=int, default=FEEDBACK_DAYS)
    args = ap.parse_args()

    report = status()
    limits = {"trash": args.trash_days, "feedback": args.feedback_days}

    for name, info in report.items():
        keep = limits[name]
        stale = [r for r in entries(info["folder"]) if r["age_days"] > keep]
        print(f"\n{name}/  —  {info['items']} item(s), {_human(info['bytes'])}, "
              f"oldest {info['oldest_days']:.0f} days")
        if not info["items"]:
            continue
        print(f"   keeping {keep} days; {len(stale)} item(s) past that")
        for row in stale[-5:]:
            print(f"     {row['age_days']:5.0f}d  {_human(row['bytes']):>9}  {row['name']}")
        if len(stale) > 5:
            print(f"     ... and {len(stale) - 5} more")

    if not args.prune:
        print("\nNothing was removed. Add --prune to remove what's past its date.")
        return

    print()
    for name, info in report.items():
        removed = prune(info["folder"], limits[name], dry_run=args.dry_run)
        verb = "would remove" if args.dry_run else "removed"
        freed = sum(r["bytes"] for r in removed)
        print(f"{name}/: {verb} {len(removed)} item(s), {_human(freed)}")
    if args.dry_run:
        print("\n[dry run] Nothing was actually removed.")


if __name__ == "__main__":
    main()
