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


def main() -> int:
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


if __name__ == "__main__":
    if "--optout" in sys.argv or "--optout-check" in sys.argv:
        raise SystemExit(optout_main(sys.argv[1:]))
    raise SystemExit(main())
