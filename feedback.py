"""
feedback.py — capture the app window so a comment can be sent about what's on screen.

The toolkit shows real client details on nearly every screen, so a screenshot of it is
PII. Two things follow, and both are deliberate:

  * The capture is written to feedback/ (gitignored) on this machine, and nothing
    leaves it until a person presses Send in their own mail client.
  * The annotation tool offers a REDACT box as well as a highlighter, so anything that
    shouldn't travel can be blacked out first — and it is blacked out in the saved
    file, not merely covered in the browser.

This module only grabs pixels and builds a mail link. It sends nothing.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path
from urllib.parse import quote

from paths import ROOT

FEEDBACK_DIR = ROOT / "feedback"
WINDOW_TITLE = "Privacy Removal Toolkit"

# Where comments go. Changing this changes who receives them.
FEEDBACK_TO = "john@qvadisai.com"
GMAIL_COMPOSE = "https://mail.google.com/mail/?view=cm&fs=1"


def capture(title: str = WINDOW_TITLE) -> bytes:
    """PNG of the app window, or of the whole screen if it can't be found.

    Grabs the real window rather than rendering the DOM, so whatever is actually on
    screen is captured — including a PDF shown in the built-in reader, which no
    HTML-to-canvas trick can reach.

    Capturing the WINDOW rather than the screen matters here beyond tidiness: a
    full-screen grab takes in whatever else the worker has open, and this picture is
    about to be attached to an email. Each platform has its own way of asking a
    single window to draw itself; the screen grab is the last resort.
    """
    image = _print_window(title)          # Windows: PrintWindow
    if image is None:
        image = _mac_window(title)        # macOS: Quartz + screencapture
    if image is None:
        from PIL import ImageGrab

        box = _window_box(title)
        image = (ImageGrab.grab(bbox=box, all_screens=True) if box
                 else ImageGrab.grab(all_screens=True))

    import io

    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


class ScreenPermissionNeeded(Exception):
    """macOS has not granted Screen Recording, so a capture would be a lie.

    Without it, screencapture returns an image of the right SIZE showing only the
    desktop — so the feature appears to work and quietly attaches the wrong picture
    to an email. Refusing and saying why is the only honest option.
    """


def _mac_screen_permission() -> bool:
    """True if this process may capture the screen. Asks for it the first time."""
    try:
        import Quartz
    except ImportError:
        return True                      # can't tell; let the capture try
    preflight = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if preflight is None:
        return True                      # older macOS: no permission model
    if preflight():
        return True
    request = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
    if request:
        request()                        # pops the system dialog, once
    return False


def _mac_window(title: str):
    """Capture this app's own window on macOS. PIL Image, or None.

    Found by OWNER PID, not by title. macOS only reveals window titles to apps that
    already hold Screen Recording permission, so a title lookup fails on exactly the
    machines where it matters — and silently, by falling through to a grab of the
    whole desktop. The webview runs in this process, so our PID always matches.
    """
    if sys.platform != "darwin":
        return None
    if not _mac_screen_permission():
        raise ScreenPermissionNeeded(
            "macOS has not given this app permission to capture the screen.")
    try:
        import os
        import subprocess
        import tempfile

        from PIL import Image
        from Quartz import (CGWindowListCopyWindowInfo, kCGNullWindowID,
                            kCGWindowListOptionOnScreenOnly)

        mine = os.getpid()
        candidates = []
        for info in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,
                                               kCGNullWindowID) or []:
            bounds = info.get("kCGWindowBounds") or {}
            width, height = int(bounds.get("Width", 0)), int(bounds.get("Height", 0))
            if width < 200 or height < 200:
                continue                 # menu bar items, tooltips, shadows
            named = str(info.get("kCGWindowName") or "")
            if info.get("kCGWindowOwnerPID") == mine or (named and named == title):
                candidates.append((width * height, info.get("kCGWindowNumber")))
        if not candidates:
            return None

        window_id = max(candidates)[1]   # the largest window we own
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "window.png"
            # -o drops the window shadow, -x silences the shutter sound.
            result = subprocess.run(
                ["screencapture", "-l", str(window_id), "-o", "-x", str(shot)],
                capture_output=True, timeout=20)
            if result.returncode != 0 or not shot.is_file():
                return None
            image = Image.open(shot)
            image.load()

        if image.width < 50 or image.height < 50:
            return None
        return image
    except ScreenPermissionNeeded:
        raise
    except Exception:
        return None


def _print_window(title: str):
    """Ask the window to render itself into a bitmap. PIL Image, or None.

    PW_RENDERFULLCONTENT is what makes this work for a WebView2 window; without it
    the browser surface comes back blank. Returns None on any failure so the caller
    can fall back to a plain screen grab.

    Every prototype is declared: a 64-bit GDI handle passed to an undeclared function
    is truncated to int and raises an overflow, which is how this silently fell back
    to a screen grab the first time round.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        H = wintypes.HANDLE

        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowDC.argtypes = [wintypes.HWND]
        user32.GetWindowDC.restype = H
        user32.ReleaseDC.argtypes = [wintypes.HWND, H]
        user32.PrintWindow.argtypes = [wintypes.HWND, H, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes = [H]
        gdi32.CreateCompatibleDC.restype = H
        gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = H
        gdi32.SelectObject.argtypes = [H, H]
        gdi32.SelectObject.restype = H
        gdi32.DeleteObject.argtypes = [H]
        gdi32.DeleteDC.argtypes = [H]

        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < 50 or height < 50:
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        gdi32.GetDIBits.argtypes = [H, H, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                                    wintypes.UINT]

        hdc = user32.GetWindowDC(hwnd)
        if not hdc:
            return None
        mem = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        previous = gdi32.SelectObject(mem, bitmap)
        try:
            if not user32.PrintWindow(hwnd, mem, 2):      # PW_RENDERFULLCONTENT
                return None
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height             # top-down rows
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0              # BI_RGB
            buf = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0):
                return None

            from PIL import Image

            image = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
            # A window that refuses to draw comes back solid black; a screen grab
            # beats handing someone a black rectangle.
            if image.convert("L").getextrema()[1] < 12:
                return None
            return image.convert("RGB")
        finally:
            gdi32.SelectObject(mem, previous)
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(hwnd, hdc)
    except Exception:
        return None


def _window_box(title: str):
    """(left, top, right, bottom) of the named window, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < 50 or rect.bottom - rect.top < 50:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def save(png: bytes, note: str = "") -> Path:
    """Write a capture into feedback/ and return where it landed.

    The name is unique even within the same second. It used to be the timestamp
    alone, so two comments filed in quick succession collided: the second
    overwrote the first's picture AND inherited its .txt, which paired a
    screenshot with somebody else's words.
    """
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = FEEDBACK_DIR / f"comment_{stamp}.png"
    suffix = 2
    while path.exists() or path.with_suffix(".txt").exists():
        path = FEEDBACK_DIR / f"comment_{stamp}_{suffix}.png"
        suffix += 1

    path.write_bytes(png)
    if note.strip():
        path.with_suffix(".txt").write_text(note, encoding="utf-8")
    return path


def compose_url(comment: str, image_path: Path | None = None,
                to: str = FEEDBACK_TO) -> str:
    """A Gmail compose link with the comment filled in.

    No mail URL scheme can attach a file — not Gmail's, not mailto: — so the body
    names the picture and the app opens its folder. Pretending otherwise would just
    lose the screenshot.

    The folder path stays OUT of the body. It is a path on the sender's own machine,
    so it tells the reader nothing, and on Windows it carries the sender's user name.
    They are looking at the open folder while they write; the file name is enough.
    """
    subject = "Privacy Toolkit — comment"
    body = comment.rstrip()
    if image_path:
        body += ("\n\n---\n"
                 f"Screenshot: {image_path.name}\n"
                 "(attach it from the folder that just opened)")
    return (f"{GMAIL_COMPOSE}&to={quote(to)}"
            f"&su={quote(subject)}&body={quote(body)}")
