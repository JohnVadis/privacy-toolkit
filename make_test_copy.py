#!/usr/bin/env python3
"""
make_test_copy.py — build a clean copy of the toolkit to hand to someone for testing.

    python make_test_copy.py                 # build the folder
    python make_test_copy.py --zip           # build it and zip it

Works from an ALLOWLIST, never a list of exclusions. This repo holds real client
details, and "copy everything except the private bits" is one forgotten pattern away
from mailing someone a stranger's date of birth. Anything not named below does not
travel, and the build refuses to finish if a known-private value appears in the output.

What is deliberately left out:
  * clients/*.yaml except the template and a made-up sample — real client files
  * output/, trash/, feedback/ — generated work, all of it client data
  * .venv/ — rebuilt by the launcher on first run
  * docs/, CLAUDE.md, this script, build_exe.py — for whoever maintains the toolkit
  * the demo and scratch form mappings — not real county forms
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from paths import ROOT

DEST_NAME = "Privacy Toolkit (test copy)"

# Everything the app needs to run, and nothing else.
FILES = [
    "Start Privacy Toolkit.bat",
    "Start Privacy Toolkit.command",     # macOS launcher (keep the exec bit)
    "requirements.txt",
    "sites.yaml",
    # engine
    "paths.py", "client_context.py", "fill_forms.py", "validate_client.py",
    "build_worklist.py", "pdf_fill.py", "pdf_overlay.py", "pdf_render.py",
    "mapping_edit.py", "form_import.py", "feedback.py", "scaffold_mapping.py",
    "documents.py", "pdf_attachment.py", "import_documents.py",
    # the client's own starting point
    "clients/_TEMPLATE.yaml",
]

FOLDERS = ["webapp"]

# Real county forms only. The demo form and anything scratch stays behind.
FORM_KEYS = [
    "hillsborough_clerk_redaction",
    "hillsborough_appraiser_confidentiality",
    "hillsborough_appraiser_military",
    "fl_dos_public_records_exemption",
]

SKIP_DIRS = {"__pycache__", ".venv", "_backups"}

# If any of these turn up in the copy, something private leaked in.
def private_markers() -> list[str]:
    """Values from the real client files that must never appear in a test copy."""
    markers: set[str] = set()
    for path in (ROOT / "clients").glob("*.yaml"):
        if path.stem in ("_TEMPLATE",):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.findall(r'^\s*(?:first_name|last_name|line1):\s*"?([^"\n#]+)"?',
                                text, re.M):
            value = match.strip()
            if len(value) > 2:
                markers.add(value)
        for match in re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', text):
            markers.add(match)
    return sorted(markers)


def build(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    for rel in FILES:
        src = ROOT / rel
        if not src.is_file():
            raise SystemExit(f"missing: {rel}")
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)

    for rel in FOLDERS:
        shutil.copytree(ROOT / rel, dest / rel,
                        ignore=shutil.ignore_patterns(*SKIP_DIRS))

    (dest / "forms" / "mappings").mkdir(parents=True)
    (dest / "forms" / "_blank_pdfs").mkdir(parents=True)
    for key in FORM_KEYS:
        mapping = ROOT / "forms" / "mappings" / f"{key}.map.yaml"
        if not mapping.is_file():
            raise SystemExit(f"missing mapping: {key}")
        shutil.copy2(mapping, dest / "forms" / "mappings" / mapping.name)
        pdf = _blank_pdf_for(mapping)
        shutil.copy2(pdf, dest / "forms" / "_blank_pdfs" / pdf.name)

    _write_sample_client(dest)
    _write_readme(dest)
    for folder in ("output", "clients"):
        (dest / folder).mkdir(exist_ok=True)
    return dest


def _blank_pdf_for(mapping: Path) -> Path:
    import yaml

    data = yaml.safe_load(mapping.read_text(encoding="utf-8"))
    pdf = ROOT / str(data["pdf"])
    if not pdf.is_file():
        raise SystemExit(f"missing blank PDF for {mapping.name}: {data['pdf']}")
    return pdf


def _write_sample_client(dest: Path) -> None:
    """One obviously made-up client, so the app isn't empty on first open."""
    from client_context import EXAMPLE_CLIENT, dump_client

    sample = dict(EXAMPLE_CLIENT)
    (dest / "clients").mkdir(exist_ok=True)
    text = ("# A made-up client so there is something to look at. Not a real person.\n"
            "# Delete this file once you're entering real clients.\n\n"
            + dump_client(sample))
    (dest / "clients" / "sample_client.yaml").write_text(text, encoding="utf-8")


def _write_readme(dest: Path) -> None:
    (dest / "READ ME FIRST.txt").write_text(READ_ME, encoding="utf-8")


READ_ME = """PRIVACY REMOVAL TOOLKIT — test copy
===================================

TO START
--------
Double-click "Start Privacy Toolkit.bat".

The first time, it sets itself up: it builds what it needs and downloads a few
parts, which takes about a minute and needs an internet connection. It then puts
a "Privacy Toolkit" icon on your Desktop and opens the app.

After that, just use the Desktop icon. It opens in about two seconds and there is
no black window. Come back to the .bat file only if something breaks — it repairs
the installation and shows you what went wrong.

If it tells you Python isn't installed: get it from python.org/downloads, and on
the first screen of the installer tick "Add python.exe to PATH".


WHAT IT DOES
------------
1. You enter a client's details ONCE.
2. It fills the Florida forms with those details, ready to print.
3. It builds a spreadsheet of the opt-out sites with the values each one asks for.

There is a made-up sample client in the list so you can try it straight away.
Delete it when you start entering real people.


WHAT IT DOES NOT DO — on purpose
--------------------------------
* It never submits anything. Every form is a draft for you to check, get
  notarised where the county requires it, and file yourself.
* It never decides whether someone qualifies for an exemption. You set that.
* It never sends the opt-out requests. Those sites need a person.


YOUR CLIENTS' DETAILS STAY ON THIS COMPUTER
-------------------------------------------
The app runs entirely on this machine. Nothing is uploaded, and nothing on your
network can reach it. Client details live in the "clients" folder and the filled
forms in "output" — both on this computer only, so keep it locked and backed up.

The one exception is the "Comment" button, which is there for you to send feedback
about the app. It takes a picture of your screen, so use the "Hide" tool to black
out anything that shouldn't be shared before you send it.


IF SOMETHING IS WRONG
---------------------
Press "Comment" at the top of any screen. Mark up the picture, write what you
expected, and it opens an email. Nothing is sent until you press send yourself.
"""


def check_clean(dest: Path) -> list[str]:
    """Look for anything private that shouldn't have travelled."""
    problems = []
    markers = private_markers()

    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(dest)
        if any(part in SKIP_DIRS for part in rel.parts):
            problems.append(f"{rel}: should not have been copied")
        if path.suffix.lower() in (".yaml", ".txt", ".py", ".html", ".css", ".js", ".bat"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in markers:
                if marker in text:
                    problems.append(f"{rel}: contains {marker!r} from a real client file")
    for folder in ("output", "trash", "feedback"):
        stray = list((dest / folder).glob("*")) if (dest / folder).is_dir() else []
        if stray:
            problems.append(f"{folder}/ is not empty: {[p.name for p in stray]}")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Build a clean copy for someone to test.")
    ap.add_argument("--dest", default=str(ROOT.parent / DEST_NAME))
    ap.add_argument("--zip", action="store_true", help="also produce a .zip alongside it")
    args = ap.parse_args()

    dest = Path(args.dest)
    build(dest)

    problems = check_clean(dest)
    files = sorted(p.relative_to(dest) for p in dest.rglob("*") if p.is_file())
    print(f"Built {dest}  ({len(files)} files)")
    for f in files:
        print(f"   {f}")

    if problems:
        print("\nPROBLEMS — not safe to send:")
        for p in problems:
            print(f"   {p}")
        sys.exit(1)
    print(f"\nChecked against {len(private_markers())} value(s) from the real client "
          "files: none of them appear in the copy.")

    if args.zip:
        archive = shutil.make_archive(str(dest), "zip", root_dir=dest.parent,
                                      base_dir=dest.name)
        size = Path(archive).stat().st_size / (1024 * 1024)
        print(f"Zipped: {archive}  ({size:.1f} MB)")


if __name__ == "__main__":
    main()
