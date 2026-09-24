#!/usr/bin/env python3
"""
build_exe.py — build an app the client can run without installing Python.

    python build_exe.py            # build it
    python build_exe.py --zip      # build it and zip the folder

On Windows this produces dist/Privacy Toolkit/ containing "Privacy Toolkit.exe";
on macOS, dist/Privacy Toolkit/ containing "Privacy Toolkit.app". Either way the
client unzips it and double-clicks. Nothing to install, no internet needed.

PyInstaller does not cross-compile: a Mac build has to be made ON a Mac and a
Windows build on Windows. Run this on each.

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

MACOS = sys.platform == "darwin"
BUNDLE_ID = "com.qvadis.privacytoolkit"

# What PyInstaller actually produces, per platform. On macOS --windowed yields a
# .app bundle, and the .app is the thing to ship.
APP_FILE = f"{APP_NAME}.app" if MACOS else f"{APP_NAME}.exe"


def make_test_copy_body() -> str:
    """The platform-independent half of the READ ME, shared with the source copy.

    Everything from "WHAT IT DOES" down is the same wherever it runs, so it is kept
    in one place rather than drifting between three copies.
    """
    import make_test_copy

    text = make_test_copy.READ_ME
    return "\n" + text[text.index("WHAT IT DOES"):]

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
    "PIL.ImageGrab",
]

# The webview backend differs, and PyInstaller only finds the one it can see.
HIDDEN += (["webview.platforms.cocoa"] if MACOS
           else ["webview.platforms.edgechromium", "webview.platforms.winforms"])

EXCLUDE = ["tkinter", "matplotlib", "numpy", "pytest", "PyInstaller", "pymupdf", "fitz"]


def run_pyinstaller() -> None:
    sep = ";" if sys.platform == "win32" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--windowed",                      # no console window behind the app
        str(ROOT / "webapp" / "desktop.py"),
    ]
    icon = _icon_file()
    if icon:
        args += ["--icon", str(icon)]
    if MACOS:
        # Without an identifier macOS treats each rebuild as a different app and
        # re-asks for Screen Recording permission every time.
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    else:
        args += ["--contents-directory", "_internal"]
    for src, dest in BUNDLED_DATA:
        args += ["--add-data", f"{ROOT / src}{sep}{dest}"]
    for name in HIDDEN:
        args += ["--hidden-import", name]
    for name in EXCLUDE:
        args += ["--exclude-module", name]
    args += ["--paths", str(ROOT)]

    print("Running PyInstaller — this takes a couple of minutes...\n")
    subprocess.run(args, check=True, cwd=ROOT)


def _icon_file():
    """The icon for this platform, or None — a missing icon must not fail a build."""
    name = "toolkit.icns" if MACOS else "toolkit.ico"
    path = ROOT / "webapp" / "static" / name
    if path.is_file():
        return path
    if MACOS:
        print(f"  note: no {name} — building without a custom icon.")
        print("        (make one with: iconutil -c icns toolkit.iconset)")
    return None


def add_user_files(app_dir: Path) -> None:
    """Put the editable files beside the app."""
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

    (app_dir / "READ ME FIRST.txt").write_text(
        READ_ME_MACOS if MACOS else READ_ME_WINDOWS, encoding="utf-8")


def tidy_macos_bundle(app_dir: Path) -> None:
    """Leave the .app and the files it uses — nothing else.

    PyInstaller writes BOTH a .app and a plain onedir build, and the onedir copy is
    a complete second copy of the runtime: it doubles the download, and it leaves a
    `Privacy Toolkit` unix executable sitting next to `Privacy Toolkit.app` that
    looks just as launchable and opens a Terminal window if clicked. A beta tester
    should see one thing to double-click.
    """
    for junk in (app_dir / "_internal", app_dir / APP_NAME):
        if junk.is_dir():
            shutil.rmtree(junk)
        elif junk.exists():
            junk.unlink()


def sign_macos_bundle(app_dir: Path) -> None:
    """Ad-hoc sign the bundle so macOS will run it at all.

    Apple Silicon refuses to launch an unsigned arm64 binary outright — the user
    gets "the application is damaged", which sounds like a corrupt download and
    isn't. An ad-hoc signature costs nothing and no certificate. It does NOT
    silence the unidentified-developer warning; that needs a paid Developer ID.
    """
    app = app_dir / APP_FILE
    try:
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                       check=True, capture_output=True)
        print("  ad-hoc signed the bundle")
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        print(f"  note: could not ad-hoc sign ({detail.decode(errors='replace').strip()})")


READ_ME_WINDOWS = """PRIVACY REMOVAL TOOLKIT
=======================

TO START
--------
Double-click "Privacy Toolkit.exe".

Nothing to install. No internet needed. It opens in a few seconds.

Windows may say "Windows protected your PC" the first time, because this app
hasn't been bought a code-signing certificate. Click "More info", then
"Run anyway". It only has to be done once.

If you want it handy, right-click the .exe and choose "Pin to Start" or send a
shortcut to your Desktop.
""" + make_test_copy_body()

READ_ME_MACOS = """PRIVACY REMOVAL TOOLKIT
=======================

TO START
--------
Double-click "Privacy Toolkit.app".

Nothing to install — no Python, no Terminal, no setup. It opens in a few seconds.

THE FIRST TIME, macOS WILL REFUSE TO OPEN IT.
You will see something like "Apple could not verify Privacy Toolkit is free of
malware". That is macOS objecting to any app that hasn't been bought an Apple
developer certificate — it is not a warning about this app specifically.

  Right-click (or Control-click) "Privacy Toolkit.app"  ->  Open  ->  Open

You only do that once. After that it opens normally by double-clicking.

If macOS instead says the app is "damaged and can't be opened", the download
picked up a quarantine flag. Open Terminal, type the following with a trailing
space, drag this folder onto the window, and press Return:

  xattr -cr 

KEEP THIS FOLDER TOGETHER
-------------------------
The app writes your client files into the folders beside it — "clients" and
"output". Move the whole folder, not just the app, or you will leave the cases
behind. Put it anywhere you like: Documents, Desktop, an external drive.
""" + make_test_copy_body()


def main():
    ap = argparse.ArgumentParser(
        description="Build the no-Python-needed app for whichever OS you run this on.")
    ap.add_argument("--zip", action="store_true")
    args = ap.parse_args()

    run_pyinstaller()
    app_dir = DIST / APP_NAME
    produced = DIST / APP_FILE if MACOS else app_dir / APP_FILE
    if not produced.exists():
        raise SystemExit(f"PyInstaller did not produce {APP_FILE}")

    if MACOS:
        # Ship a FOLDER holding the .app plus the editable files, so user data sits
        # beside the bundle (paths._root walks out of it) rather than inside, where
        # a reinstall would erase it and a signed app could not write at all.
        app_dir.mkdir(parents=True, exist_ok=True)
        target = app_dir / APP_FILE
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(produced), str(target))
        # Only after the .app is in place: the onedir copy beside it is redundant,
        # and an unsigned arm64 bundle will not launch at all.
        tidy_macos_bundle(app_dir)
        sign_macos_bundle(app_dir)
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
