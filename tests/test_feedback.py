"""The Comment feature — the one path by which client data can leave the machine.

It captures the window, lets the user black things out, saves to feedback/ and OPENS
a mail client. It never sends. Every test here exists to keep that true: the human
press of Send in their own mail client is the gate, and nothing may route around it.

Nothing in this file takes a real screenshot or opens a real mail client; both are
replaced, because a test suite that shells out to the desktop is a test suite nobody
runs.
"""
from __future__ import annotations

import base64
import os
import re
from urllib.parse import parse_qs, urlparse

import pytest

import feedback as engine
import paths
from webapp.routers import feedback as router

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


@pytest.fixture(autouse=True)
def no_desktop(monkeypatch):
    """Nothing here touches the screen, the mail client or the file manager."""
    opened: list[str] = []
    revealed: list = []
    monkeypatch.setattr(engine, "capture", lambda *a, **k: PNG)
    monkeypatch.setattr(router.feedback_engine, "capture", lambda *a, **k: PNG)
    monkeypatch.setattr(router, "_open_externally",
                        lambda url: opened.append(url) or True)
    monkeypatch.setattr(router, "reveal", lambda path: revealed.append(path))
    return {"opened": opened, "revealed": revealed}


@pytest.fixture(autouse=True)
def clear_pending():
    router._pending.pop("png", None)
    yield
    router._pending.pop("png", None)


class TestItNeverSends:
    """The property the whole feature rests on."""

    def test_no_module_in_the_path_can_make_a_request(self):
        import inspect

        for module in (engine, router):
            text = inspect.getsource(module)
            for forbidden in ("requests.", "urllib.request.urlopen", "httpx.",
                              "smtplib", "socket.socket"):
                assert forbidden not in text, f"{module.__name__} reaches the network"

    def test_composing_a_link_performs_no_io(self, tmp_path):
        url = engine.compose_url("a comment", tmp_path / "shot.png")
        assert url.startswith("https://mail.google.com/mail/")

    def test_sending_only_opens_a_compose_window(self, app_client, no_desktop):
        app_client.post("/feedback/send", data={"comment": "something is wrong"})
        assert len(no_desktop["opened"]) == 1
        assert no_desktop["opened"][0].startswith("https://mail.google.com/")

    def test_the_comment_is_carried_in_the_link_not_transmitted(self, app_client,
                                                                no_desktop):
        app_client.post("/feedback/send", data={"comment": "the DOB is wrong"})
        query = parse_qs(urlparse(no_desktop["opened"][0]).query)
        assert query["body"][0].startswith("the DOB is wrong")


class TestComposeUrl:
    def test_addresses_the_configured_recipient(self):
        url = engine.compose_url("hello")
        assert parse_qs(urlparse(url).query)["to"][0] == engine.FEEDBACK_TO

    def test_names_the_screenshot_so_it_can_be_attached(self, tmp_path):
        url = engine.compose_url("hello", tmp_path / "comment_2026.png")
        assert "comment_2026.png" in parse_qs(urlparse(url).query)["body"][0]

    def test_keeps_the_folder_path_out_of_the_body(self, tmp_path):
        # The path says nothing useful to the reader and on Windows it carries the
        # sender's user name.
        url = engine.compose_url("hello", tmp_path / "comment_2026.png")
        body = parse_qs(urlparse(url).query)["body"][0]
        assert str(tmp_path) not in body

    def test_no_screenshot_means_no_attachment_note(self):
        body = parse_qs(urlparse(engine.compose_url("hello")).query)["body"][0]
        assert "attach it from" not in body


class TestSave:
    def test_writes_the_capture_into_the_feedback_folder(self):
        path = engine.save(PNG)
        assert path.is_file() and path.parent == engine.FEEDBACK_DIR
        assert path.read_bytes() == PNG

    def test_names_it_by_timestamp(self):
        assert re.match(r"comment_\d{4}-\d{2}-\d{2}_\d{6}(_\d+)?\.png$",
                        engine.save(PNG).name)

    def test_two_comments_in_the_same_second_do_not_collide(self):
        first = engine.save(b"FIRST", "first comment")
        second = engine.save(b"SECOND", "")
        assert first != second
        assert first.read_bytes() == b"FIRST"          # not overwritten
        assert not second.with_suffix(".txt").exists()  # not paired with the wrong words

    def test_keeps_the_comment_beside_the_picture(self):
        path = engine.save(PNG, "what went wrong")
        assert path.with_suffix(".txt").read_text(encoding="utf-8") == "what went wrong"

    def test_writes_no_note_file_for_an_empty_comment(self):
        path = engine.save(PNG, "   ")
        assert not path.with_suffix(".txt").exists()

    def test_the_folder_is_gitignored(self):
        # It fills up with screenshots of client data. paths.ROOT is the sandbox
        # during a test run, so this checks the real repo's ignore file.
        from conftest import REPO

        ignore = REPO / ".gitignore"
        if not ignore.is_file():
            pytest.skip("running outside a checkout")
        assert "feedback/" in ignore.read_text(encoding="utf-8")


class TestCaptureRoute:
    def test_grabs_the_window_and_holds_it_in_memory(self, app_client):
        response = app_client.post("/feedback/capture")
        assert response.status_code == 204
        assert router._pending["png"] == PNG

    def test_nothing_is_written_to_disk_by_capturing(self, app_client):
        before = set(engine.FEEDBACK_DIR.glob("*")) if engine.FEEDBACK_DIR.is_dir() else set()
        app_client.post("/feedback/capture")
        after = set(engine.FEEDBACK_DIR.glob("*")) if engine.FEEDBACK_DIR.is_dir() else set()
        assert before == after

    def test_a_failure_to_capture_is_reported_not_swallowed(self, app_client,
                                                            monkeypatch):
        def boom():
            raise RuntimeError("no window station")

        monkeypatch.setattr(router.feedback_engine, "capture", boom)
        assert app_client.post("/feedback/capture").status_code == 503

    def test_the_image_is_404_until_something_is_captured(self, app_client):
        assert app_client.get("/feedback/image").status_code == 404

    def test_the_image_is_served_after_capturing(self, app_client):
        app_client.post("/feedback/capture")
        response = app_client.get("/feedback/image")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_the_image_is_never_cached(self, app_client):
        app_client.post("/feedback/capture")
        assert "no-store" in app_client.get("/feedback/image").headers["cache-control"]


class TestSendRoute:
    def test_a_comment_is_required(self, app_client):
        assert app_client.post("/feedback/send", data={"comment": "   "}).status_code == 400

    def test_saves_the_marked_up_image_the_browser_posted(self, app_client):
        # The redact tool blacks pixels out in the image DATA, so what the browser
        # sends is what must be saved — not the original capture.
        app_client.post("/feedback/capture")
        redacted = b"\x89PNG\r\n\x1a\n" + b"\xff" * 64
        data_url = "data:image/png;base64," + base64.b64encode(redacted).decode()

        app_client.post("/feedback/send",
                        data={"comment": "redacted this", "image": data_url})
        saved = sorted(engine.FEEDBACK_DIR.glob("*.png"))[-1]
        assert saved.read_bytes() == redacted

    def test_falls_back_to_the_capture_when_the_browser_sends_nothing(self,
                                                                      app_client):
        app_client.post("/feedback/capture")
        app_client.post("/feedback/send", data={"comment": "no markup"})
        assert sorted(engine.FEEDBACK_DIR.glob("*.png"))[-1].read_bytes() == PNG

    def test_works_with_no_screenshot_at_all(self, app_client, no_desktop):
        response = app_client.post("/feedback/send", data={"comment": "just words"})
        assert response.status_code == 200
        assert no_desktop["opened"]

    def test_opens_the_folder_so_the_picture_can_be_attached(self, app_client,
                                                             no_desktop):
        app_client.post("/feedback/capture")
        app_client.post("/feedback/send", data={"comment": "look at this"})
        assert no_desktop["revealed"]

    def test_forgets_the_capture_afterwards(self, app_client):
        app_client.post("/feedback/capture")
        app_client.post("/feedback/send", data={"comment": "done"})
        assert "png" not in router._pending

    def test_tells_the_user_to_send_it_themselves(self, app_client):
        body = app_client.post("/feedback/send", data={"comment": "x"}).text
        assert "send" in body.lower()

    def test_says_so_when_the_mail_client_would_not_open(self, app_client,
                                                         monkeypatch):
        monkeypatch.setattr(router, "_open_externally", lambda url: False)
        body = app_client.post("/feedback/send", data={"comment": "x"}).text
        assert "mail.google.com" in body        # the link is offered to copy

    def test_an_overlong_comment_is_truncated_not_rejected(self, app_client,
                                                            no_desktop):
        app_client.post("/feedback/send", data={"comment": "x" * 50_000})
        body = parse_qs(urlparse(no_desktop["opened"][0]).query)["body"][0]
        assert len(body) <= router.MAX_COMMENT + 200

    def test_a_real_screenshot_is_not_too_big_to_send(self, app_client, no_desktop):
        """The bug a beta tester hit: press Send, get "Forbidden".

        Starlette caps a single form field at 1 MB by default, and a screenshot of
        a window on a Retina display is several. The upload was rejected before any
        handler ran, so nothing here was reached and nothing was saved — the user
        wrote their note and lost it. Anything that lowers MAX_IMAGE_BYTES back
        under a realistic capture should fail this.
        """
        # Incompressible, so the encoded size is honest rather than a PNG of flat
        # colour that shrinks to nothing and proves nothing.
        blob = base64.b64encode(os.urandom(3 * 1024 * 1024)).decode()
        assert len(blob) > 1024 * 1024          # bigger than the old cap

        response = app_client.post("/feedback/send", data={
            "comment": "the document table printed on the wrong page",
            "image": f"data:image/png;base64,{blob}",
        })
        assert response.status_code == 200
        assert no_desktop["opened"], "the mail client was never opened"


class TestPage:
    def test_says_how_to_take_a_screenshot_when_there_is_none(self, app_client):
        assert "No screenshot yet" in app_client.get("/feedback").text

    def test_warns_that_the_screenshot_is_client_data(self, app_client):
        app_client.post("/feedback/capture")
        body = app_client.get("/feedback").text
        assert "may show client details" in body
        assert "black anything out" in body

    def test_says_nothing_is_sent_until_the_user_sends_it(self, app_client):
        app_client.post("/feedback/capture")
        assert "Nothing is sent until you" in app_client.get("/feedback").text

    def test_names_who_it_goes_to(self, app_client):
        assert engine.FEEDBACK_TO in app_client.get("/feedback").text


class TestDecodeImage:
    def test_reads_a_data_url(self):
        data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
        assert router._decode_image(data_url) == PNG

    @pytest.mark.parametrize("raw", ["", "not a data url", "data:image/png;base64,!!!"])
    def test_anything_else_is_none_rather_than_an_exception(self, raw):
        assert router._decode_image(raw) is None
