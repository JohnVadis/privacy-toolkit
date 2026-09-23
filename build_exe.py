#!/usr/bin/env python3
"""
build_exe.py — build a Windows app the client can run without installing Python.

    python build_exe.py            # build it
    python build_exe.py --zip      # build it and zip the folder

Produces dist/Privacy Toolkit/ containing "Privacy Toolkit.exe" plus the files the
toolkit reads and writes. The client unzips it and double-clicks the .exe. Nothing to
install, no internet needed.

Two things shape the layout:

  * Templates, stylesheets and scripts are read-only, so they ship INSIDE the exe and
    are found through paths.BUNDLED.
  * clients/, output/, forms/ and sites.yaml are things the user edits — the mapping
    editor writes to forms/mappings/, form import writes to forms/_blank_pdfs/ — so
    they sit BESIDE the exe as ordinary files, found through paths.ROOT. Bundling them
    would put them in a temp folder that is deleted when the app closes, taking every
    client file with it.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from paths import ROOT

APP_NAME = "Privacy Toolkit"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

# Shipped inside the executable: read-only, never edited by the user.
BUNDLED_DATA = [
    ("webapp/templates", "webapp/templates"),
    ("webapp/static", "webapp/static"),
]

# Shipped beside the executable: the user edits these.
BESIDE = ["sites.yaml", "clients/_TEMPLATE.yaml"]
FORM_KEYS = [
    "hillsborough_clerk_redaction",
    "hillsborough_appraiser_confidentiality",
    "hillsborough_appraiser_military",
    "fl_dos_public_records_exemption",
]

# Imported dynamically or late, so PyInstaller can't see them by itself.
HIDDEN = [
    "webapp.main", "webapp.desktop", "webapp.deps", "webapp.security",
    "webapp.model", "webapp.formparse",
    "webapp.routers.clients", "webapp.routers.generate",
    "webapp.routers.corrections", "webapp.routers.forms", "webapp.routers.feedback",
    "webapp.routers.documents",
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "pypdfium2", "pdfplumber", "reportlab.pdfgen.canvas", "openpyxl",
    "PIL.ImageGrab", "webview.platforms.edgechromium", "webview.platforms.winforms",
]

EXCLUDE = ["tkinter", "matplotlib", "numpy", "pytest", "PyInstaller", "pymupdf", "fitz"]


def run_pyinstaller() -> None:
    sep = ";" if sys.platform == "win32" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--windowed",                      # no console window behind the app
        "--icon", str(ROOT / "webapp" / "static" / "toolkit.ico"),
        "--contents-directory", "_internal",
        str(ROOT / "webapp" / "desktop.py"),
    ]
    for src, dest in BUNDLED_DATA:
        args += ["--add-data", f"{ROOT / src}{sep}{dest}"]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    for name in EXCLUDE:
        args += ["--exclude-module", name]
    args += ["--paths", str(ROOT)]

    print("Running PyInstaller — this takes a couple of minutes...\n")
    subprocess.run(args, check=True, cwd=ROOT)


def add_user_files(app_dir: Path) -> None:
    """Put the editable files beside the .exe."""
    for rel in BESIDE:
        dest = app_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)

    (app_dir / "forms" / "mappings").mkdir(parents=True, exist_ok=True)
    (app_dir / "forms" / "_blank_pdfs").mkdir(parents=True, exist_ok=True)
    import yaml

    for key in FORM_KEYS:
        mapping = ROOT / "forms" / "mappings" / f"{key}.map.yaml"
        shutil.copy2(mapping, app_dir / "forms" / "mappings" / mapping.name)
        pdf = ROOT / str(yaml.safe_load(mapping.read_text(encoding="utf-8"))["pdf"])
        shutil.copy2(pdf, app_dir / "forms" / "_blank_pdfs" / pdf.name)

    from client_context import EXAMPLE_CLIENT, dump_client

    (app_dir / "clients").mkdir(exist_ok=True)
    (app_dir / "clients" / "sample_client.yaml").write_text(
        "# A made-up client so there is something to look at. Not a real person.\n"
        "# Delete this file once you're entering real clients.\n\n"
        + dump_client(dict(EXAMPLE_CLIENT)), encoding="utf-8")
    (app_dir / "output").mkdir(exist_ok=True)

    import make_test_copy

    (app_dir / "READ ME FIRST.txt").write_text(
        make_test_copy.READ_ME.replace(
            'Double-click "Start Privacy Toolkit.bat".',
            'Double-click "Privacy Toolkit.exe".')
        .replace(
            """The first time, it sets itself up: it builds what it needs and downloads a few
parts, which takes about a minute and needs an internet connection. It then puts
a "Privacy Toolkit" icon on your Desktop and opens the app.

After that, just use the Desktop icon. It opens in about two seconds and there is
no black window. Come back to the .bat file only if something breaks — it repairs
the installation and shows you what went wrong.

If it tells you Python isn't installed: get it from python.org/downloads, and on
the first screen of the installer tick "Add python.exe to PATH".""",
            """Nothing to install. No internet needed. It opens in a few seconds.

Windows may say "Windows protected your PC" the first time, because this app
hasn't been bought a code-signing certificate. Click "More info", then
"Run anyway". It only has to be done once.

If you want it handy, right-click the .exe and choose "Pin to Start" or send a
shortcut to your Desktop."""), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Build the no-Python-needed Windows app.")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    run_pyinstaller()
    app_dir = DIST / APP_NAME
    if not (app_dir / f"{APP_NAME}.exe").is_file():
        raise SystemExit("PyInstaller did not produce an .exe")
    add_user_files(app_dir)

    total = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\nBuilt {app_dir}")
    print(f"  {sum(1 for f in app_dir.rglob('*') if f.is_file())} files, "
          f"{total / 1048576:.0f} MB")

    if args.zip:
        archive = shutil.make_archive(str(DIST / f"{APP_NAME} (no Python needed)"),
                                      "zip", root_dir=DIST, base_dir=APP_NAME)
        print(f"  zipped: {archive} ({Path(archive).stat().st_size / 1048576:.0f} MB)")


if __name__ == "__main__":
    main()
