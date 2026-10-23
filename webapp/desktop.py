"""
desktop.py — run the toolkit as a desktop application window.

    python -m webapp.desktop

There is still an HTTP server, because the UI is still HTML — but it is invisible and
private: bound to 127.0.0.1 on an OS-assigned port, and gated by webapp/security.py so
only a client holding this session's token gets a response. The window below is the
only such client. Nothing is published, and nothing outside this machine can reach it.

Why a webview rather than a browser tab:
  * No port or URL for the worker to know, mistype, or bookmark into a stale session.
  * No shared browser profile, so no extension, no other tab and no cached credential
    sits next to a page showing client PII.
  * Closing the window stops the server, so it cannot be left running unnoticed.

The server runs on a background thread because pywebview must own the main thread.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from paths import BUNDLED
from webapp.main import bind, make_server
from webapp.security import entry_url

WINDOW_TITLE = "Privacy Removal Toolkit"
WINDOW_SIZE = (1180, 860)
MIN_SIZE = (900, 600)
READY_TIMEOUT = 30.0
ICON = BUNDLED / "webapp" / "static" / "toolkit.ico"

# The hint is built per-platform by _repair_hint(); the launchers differ.


def problem(message: str) -> None:
    """Report a startup failure so it is visible even with no console.

    Launched from a shortcut the app has no console at all — pythonw.exe on
    Windows, a double-clicked .app on macOS — so a plain print() vanishes and the
    worker sees nothing happen at all. A dialog is the only thing they will get.
    """
    print(message, file=sys.stderr)
    body = f"{message}\n\n{_repair_hint()}"
    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, body, WINDOW_TITLE, 0x10)  # MB_ICONERROR
        elif sys.platform == "darwin":
            import subprocess

            # osascript is part of macOS; nothing to install and no window server
            # assumptions beyond the one the app already needs.
            script = (f'display dialog {_as_applescript(body)} '
                      f'with title {_as_applescript(WINDOW_TITLE)} '
                      'buttons {"OK"} default button "OK" with icon stop')
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=60)
    except Exception:
        pass  # no window station, or a locked screen — the stderr line stands


def _as_applescript(text: str) -> str:
    """A quoted AppleScript string. Backslashes and quotes both need escaping."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _repair_hint() -> str:
    """Which launcher to point the worker at, for the platform they're on."""
    launcher = ("Start Privacy Toolkit.command" if sys.platform == "darwin"
                else "Start Privacy Toolkit.bat")
    verb = "Double-click" if sys.platform != "darwin" else "Open"
    return (f"{verb} \"{launcher}\" in the toolkit folder — "
            "it repairs the installation and reports what went wrong.")


def _wait_until_started(server, timeout: float = READY_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if getattr(server, "started", False):
            return True
        time.sleep(0.05)
    return False


def _cannot_save_here() -> str:
    """Why this copy of the app can't keep client files, or '' if it can.

    Checked before the window opens, because otherwise the list of clients loads
    fine and the first save — adding a client — fails with a server error.
    """
    from paths import ROOT

    where = f"{sys.executable} {Path(sys.executable).resolve().as_posix()}"
    if sys.platform == "darwin" and "/AppTranslocation/" in where:
        return ("macOS is running the toolkit from a hidden, read-only copy, because it "
                "was opened straight from where it was downloaded. Your client files "
                "can't be saved there.\n\nQuit, move the whole \"Privacy Toolkit\" "
                "folder into Documents (drag it there in Finder), and open the app "
                "from its new place.")
    try:
        probe_dir = ROOT / "clients"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return (f"The toolkit can't save client files in its folder:\n{ROOT}\n\n"
                f"({exc.strerror or exc}). Move the whole \"Privacy Toolkit\" folder "
                "into Documents and open it from there.")
    return ""


def main() -> int:
    blocked = _cannot_save_here()
    if blocked:
        problem(blocked)
        return 1

    try:
        import webview
    except ImportError:
        problem("The app window needs the 'pywebview' package, which isn't installed.")
        return 1

    # pywebview refuses downloads unless asked. Without this the "Save a copy"
    # button silently does nothing in the app window — it works in a browser, which
    # is what made the bug easy to miss.
    webview.settings["ALLOW_DOWNLOADS"] = True

    try:
        sock, port = bind()                   # OS picks a free loopback port
        server, sock = make_server(sock)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                                  daemon=True)
        thread.start()
    except Exception as exc:
        problem(f"The app could not start its local service.\n\n{exc}")
        return 1

    if not _wait_until_started(server):
        problem("The app did not finish starting up.")
        return 1

    width, height = WINDOW_SIZE
    webview.create_window(
        WINDOW_TITLE,
        entry_url(port),                      # the token is spent on this one request
        width=width, height=height,
        min_size=MIN_SIZE,
        text_select=True,                     # a worker needs to copy values out
        confirm_close=False,
    )

    try:
        # icon= is ignored by some backends; losing it must not stop the app.
        try:
            webview.start(icon=str(ICON) if ICON.is_file() else None)
        except TypeError:
            webview.start()                   # blocks until the window is closed
    except Exception as exc:
        problem(f"The app window could not open.\n\n{exc}")
        return 1
    finally:
        # Closing the window closes the app — never leave a server holding PII behind.
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def optout_main(argv: list[str]) -> int:
    """The Opt-out Assistant — the same executable started with --optout.

    The toolkit's worklist page starts it (webapp/deps.launch_optout). One program
    rather than two keeps the no-Python build a single thing to ship and to trust.
    """
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--optout", action="store_true")
    ap.add_argument("--optout-check", action="store_true")
    ap.add_argument("--client")
    args, _unknown = ap.parse_known_args(argv)
    if args.optout_check:
        return _optout_check()
    try:
        import optout_panel
        optout_panel.App(client_path=args.client).run()
    except Exception as exc:  # noqa: BLE001 — no console in a frozen app
        problem(f"The Opt-out Assistant could not start.\n\n{exc}")
        return 1
    return 0


def _optout_check() -> int:
    """Can this build drive a browser? Exit 0 if so. For the build checks: starts
    the bundled Playwright driver and an installed Edge/Chrome, invisibly, and stops."""
    try:
        from playwright.sync_api import sync_playwright

        import optout_assist
        import optout_panel  # noqa: F401 — the window's imports must resolve too

        with sync_playwright() as pw:
            for channel in optout_assist.BROWSER_CHANNELS:
                try:
                    browser = pw.chromium.launch(channel=channel, headless=True)
                except Exception:  # noqa: BLE001
                    continue
                page = browser.new_page()
                page.set_content("<input id=x aria-label='First name'>")
                filled = optout_assist.fill_page(
                    page, {"first_name": "Check"})["filled"]
                browser.close()
                print(f"ok: {channel or 'chromium'}, filled {len(filled)}")
                return 0 if filled else 3
        print("no browser could be started")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {type(exc).__name__}: {exc}")
        return 1


def page_check() -> int:
    """`--page-check`: drive the BUILT app through its main screens, headless.

    Starts the real server on loopback and, as the window would, adds a client,
    opens each of its tabs and makes its forms. Any server error prints its
    traceback (uvicorn writes it to stderr) and fails the check. Run by CI against
    the frozen app on every platform it ships for: a module PyInstaller left out
    only shows up in the frozen app, never in the tests.
    Set PRIVACY_TOOLKIT_HOME to a scratch folder first — it writes a client.
    """
    import http.cookiejar
    import urllib.error
    import urllib.parse
    import urllib.request

    from webapp.security import SESSION_TOKEN, TOKEN_COOKIE

    blocked = _cannot_save_here()
    if blocked:
        print("failed:", blocked)
        return 1
    sock, port = bind()
    server, sock = make_server(sock)
    threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
    if not _wait_until_started(server):
        print("failed: the server did not start")
        return 1

    base = f"http://127.0.0.1:{port}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("Sec-Fetch-Site", "same-origin"), ("Origin", base)]
    failures = []

    def hit(label, path, data=None, ok=(200,)):
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        try:
            with opener.open(base + path, data=body, timeout=120) as r:
                code, final = r.status, r.geturl()
        except urllib.error.HTTPError as exc:
            code, final = exc.code, path
        print(f"{'ok  ' if code in ok else 'FAIL'} {code} {label}", flush=True)
        if code not in ok:
            failures.append(label)
        return final

    hit("start (spends the token)", f"/?k={SESSION_TOKEN}")
    if not any(c.name == TOKEN_COOKIE for c in jar):
        print("failed: no session cookie")
        return 1
    hit("client list", "/")
    hit("new client page", "/clients/new")
    for frag in ("person?index=1", "address?pi=0&index=1", "contact?pi=0&kind=phones"):
        hit(f"add-row fragment {frag.split('?')[0]}", f"/clients/fragments/{frag}")
    client = {
        "case[display_name]": "Page Check", "case[exemption][category]": "military",
        "persons[0][role]": "primary", "persons[0][first_name]": "Pat",
        "persons[0][last_name]": "Check", "persons[0][dob]": "1980-01-02",
        "persons[0][phones][]": "813-555-0100", "persons[0][emails][]": "pat@example.com",
        "persons[0][residence_index]": "0",
        "persons[0][addresses][0][line1]": "1 Example St",
        "persons[0][addresses][0][city]": "Tampa",
        "persons[0][addresses][0][county]": "hillsborough county",
        "persons[0][addresses][0][state]": "FL", "persons[0][addresses][0][zip]": "33602",
    }
    landed = hit("add a client", "/clients/new", client)
    slug = urllib.parse.unquote(landed.split("/clients/", 1)[-1].split("/", 1)[0]) \
        if "/clients/" in landed else ""
    if not slug:
        print("FAIL adding the client did not open it")
        failures.append("add a client")
    else:
        q = urllib.parse.quote(slug)
        for label, path in (("details", "edit"), ("documents", "documents"),
                            ("make forms", "generate"), ("filled forms", "files"),
                            ("opt-outs", "worklist")):
            hit(f"tab: {label}", f"/clients/{q}/{path}")
        hit("make the forms", f"/clients/{q}/generate", {})
        hit("build the worklist", f"/clients/{q}/worklist", {})
    hit("opt-out sites", "/optout-sites")
    hit("form set-up", "/forms")
    hit("add a form page", "/forms/new")
    server.should_exit = True
    if failures:
        print("failed:", ", ".join(failures))
        return 1
    print("ok: every page worked")
    return 0


if __name__ == "__main__":
    if "--page-check" in sys.argv:
        raise SystemExit(page_check())
    if "--optout" in sys.argv or "--optout-check" in sys.argv:
        raise SystemExit(optout_main(sys.argv[1:]))
    raise SystemExit(main())
