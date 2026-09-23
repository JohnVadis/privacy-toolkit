"""Behaviour that differs by operating system.

These run on whatever machine the suite runs on, with sys.platform patched, because
the point is the DISPATCH — that each platform gets the branch written for it, and
that nothing Windows-only runs on a Mac or vice versa. What the OS then does with
the command is the OS's business and is verified by running the app there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import feedback as engine
import paths
from webapp import deps, desktop
from webapp.routers import feedback as feedback_router
from webapp.routers import generate as generate_router


class TestFrozenRoot:
    """Where client data lives when the app is a packaged bundle."""

    def _root_for(self, monkeypatch, platform, executable):
        monkeypatch.delenv(paths.HOME_ENV, raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", executable)
        monkeypatch.setattr(sys, "platform", platform)
        # Compared as a posix string: resolve() on this machine prepends a drive
        # letter to a unix-looking path, which says nothing about the logic.
        return paths._root().as_posix()

    def test_windows_data_sits_beside_the_exe(self, monkeypatch):
        root = self._root_for(monkeypatch, "win32",
                              r"C:\Apps\Privacy Toolkit\Privacy Toolkit.exe")
        assert root.endswith("Privacy Toolkit")

    def test_macos_data_sits_beside_the_app_not_inside_it(self, monkeypatch):
        # Inside the bundle it would be erased by a reinstall, and unwritable once
        # the app is signed or quarantined.
        root = self._root_for(
            monkeypatch, "darwin",
            "/Applications/Privacy Toolkit/Privacy Toolkit.app/Contents/MacOS/toolkit")
        assert root.endswith("/Applications/Privacy Toolkit")
        assert ".app" not in root

    def test_a_nested_app_path_still_walks_all_the_way_out(self, monkeypatch):
        root = self._root_for(
            monkeypatch, "darwin",
            "/Users/someone/Desktop/Kit/Privacy Toolkit.app/Contents/MacOS/toolkit")
        assert root.endswith("/Users/someone/Desktop/Kit")

    def test_a_plain_unix_binary_keeps_its_own_folder(self, monkeypatch):
        root = self._root_for(monkeypatch, "linux", "/opt/toolkit/bin/toolkit")
        assert root.endswith("/opt/toolkit/bin")

    def test_the_home_override_still_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "platform", "darwin")
        assert paths._root() == tmp_path.resolve()   # a real path on this machine


class TestStartupDialog:
    def test_windows_uses_a_message_box(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "platform", "win32")

        class FakeUser32:
            def MessageBoxW(self, *args):
                calls.append(args)

        class FakeCtypes:
            windll = type("W", (), {"user32": FakeUser32()})()

        monkeypatch.setitem(sys.modules, "ctypes", FakeCtypes)
        desktop.problem("something broke")
        assert calls and "something broke" in calls[0][1]

    def test_macos_uses_osascript(self, monkeypatch):
        calls = []
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("subprocess.run",
                            lambda *a, **k: calls.append(a[0]) or None)
        desktop.problem("something broke")
        assert calls and calls[0][0] == "osascript"
        assert "display dialog" in calls[0][2]

    def test_a_dialog_that_cannot_open_is_not_fatal(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")

        def boom(*a, **k):
            raise OSError("no window server")

        monkeypatch.setattr("subprocess.run", boom)
        desktop.problem("something broke")      # must not raise

    def test_the_repair_hint_names_the_right_launcher(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert "Start Privacy Toolkit.command" in desktop._repair_hint()
        monkeypatch.setattr(sys, "platform", "win32")
        assert "Start Privacy Toolkit.bat" in desktop._repair_hint()

    @pytest.mark.parametrize("text,expected", [
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("plain", '"plain"'),
    ])
    def test_applescript_strings_are_escaped(self, text, expected):
        # An unescaped quote here turns a dialog into a syntax error, which on a
        # failed start is the only message the worker would have got.
        assert desktop._as_applescript(text) == expected


class TestWindowCapture:
    def test_the_windows_path_is_skipped_elsewhere(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert engine._print_window("anything") is None
        assert engine._window_box("anything") is None

    def test_the_macos_path_is_skipped_elsewhere(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert engine._mac_window("anything") is None

    def test_macos_capture_asks_screencapture_for_one_window(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        seen = {}

        def fake_run(args, **kwargs):
            seen["args"] = args
            Path(args[-1]).write_bytes(b"")     # produce nothing usable
            return type("R", (), {"returncode": 1})()

        monkeypatch.setattr("subprocess.run", fake_run)
        fake_quartz = type(sys)("Quartz")
        fake_quartz.CGWindowListCopyWindowInfo = lambda *a: [
            {"kCGWindowName": "Privacy Removal Toolkit", "kCGWindowNumber": 42}]
        fake_quartz.kCGNullWindowID = 0
        fake_quartz.kCGWindowListOptionOnScreenOnly = 1
        monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)

        assert engine._mac_window("Privacy Removal Toolkit") is None   # capture failed
        assert seen["args"][0] == "screencapture"
        assert "-l" in seen["args"] and "42" in seen["args"]

    def test_macos_capture_gives_up_when_the_window_is_not_found(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        fake_quartz = type(sys)("Quartz")
        fake_quartz.CGWindowListCopyWindowInfo = lambda *a: [
            {"kCGWindowName": "Some Other App", "kCGWindowNumber": 7}]
        fake_quartz.kCGNullWindowID = 0
        fake_quartz.kCGWindowListOptionOnScreenOnly = 1
        monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)
        assert engine._mac_window("Privacy Removal Toolkit") is None

    def test_a_missing_quartz_is_not_fatal(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setitem(sys.modules, "Quartz", None)
        assert engine._mac_window("anything") is None

    def test_capture_still_produces_a_png_by_falling_back(self, monkeypatch):
        # Whatever the platform, the Comment feature must hand back an image.
        from PIL import Image

        monkeypatch.setattr(engine, "_print_window", lambda t: None)
        monkeypatch.setattr(engine, "_mac_window", lambda t: None)
        monkeypatch.setattr(engine, "_window_box", lambda t: None)
        monkeypatch.setattr("PIL.ImageGrab.grab",
                            lambda **k: Image.new("RGB", (80, 60), "white"))
        assert engine.capture().startswith(b"\x89PNG")


class TestRevealingAFile:
    def test_macos_uses_open_dash_r(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr("subprocess.Popen", lambda a, **k: calls.append(a))
        target = tmp_path / "form.pdf"
        target.write_bytes(b"%PDF-1.4\n")

        deps.reveal(target)
        assert calls and calls[0][:2] == ["open", "-R"]

    def test_windows_selects_the_file_in_explorer(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("subprocess.Popen", lambda a, **k: calls.append(a))
        deps.reveal(tmp_path / "form.pdf")
        assert calls and calls[0].startswith("explorer /select,")

    def test_linux_opens_the_containing_folder(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("subprocess.Popen", lambda a, **k: calls.append(a))
        target = tmp_path / "form.pdf"
        target.write_bytes(b"%PDF-1.4\n")
        deps.reveal(target)
        assert calls and calls[0][0] == "xdg-open"

    def test_a_file_manager_that_will_not_open_is_not_fatal(self, monkeypatch,
                                                            tmp_path):
        monkeypatch.setattr(sys, "platform", "darwin")

        def boom(*a, **k):
            raise OSError("no such binary")

        monkeypatch.setattr("subprocess.Popen", boom)
        deps.reveal(tmp_path / "x.pdf")                 # must not raise

    def test_both_routers_share_one_implementation(self):
        # A platform quirk fixed once should be fixed everywhere.
        assert feedback_router.reveal is deps.reveal
        assert generate_router.reveal is deps.reveal


class TestPackaging:
    def test_the_mac_launcher_ships_with_the_source_copy(self):
        import make_test_copy

        assert "Start Privacy Toolkit.command" in make_test_copy.FILES

    def test_the_mac_launcher_is_executable_and_lf(self):
        from conftest import REPO

        launcher = REPO / "Start Privacy Toolkit.command"
        if not launcher.is_file():
            pytest.skip("running outside a checkout")
        raw = launcher.read_bytes()
        # A CRLF shebang fails on macOS with "bad interpreter".
        assert raw.startswith(b"#!/bin/bash\n")
        assert b"\r\n" not in raw

    def test_the_build_targets_this_platform(self):
        import build_exe

        assert build_exe.APP_FILE.endswith(".app" if build_exe.MACOS else ".exe")

    def test_the_build_picks_the_right_webview_backend(self):
        import build_exe

        backends = [h for h in build_exe.HIDDEN if "webview.platforms" in h]
        assert backends
        assert all(("cocoa" in b) == build_exe.MACOS for b in backends)
