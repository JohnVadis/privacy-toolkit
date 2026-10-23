"""A server error is a page that says what to do, and an unwritable folder is caught early."""
from __future__ import annotations

import errno
import sys

import pytest

from webapp import desktop, main


@pytest.fixture
def failing_route():
    """A route that raises, added for the test and removed after."""
    raised = {}

    async def boom():
        raise raised["exc"]

    main.app.add_api_route("/_test_boom", boom)
    yield raised
    main.app.router.routes[:] = [r for r in main.app.router.routes
                                 if getattr(r, "path", "") != "/_test_boom"]


@pytest.fixture
def lenient_client(raw_client):
    from fastapi.testclient import TestClient
    from webapp.security import SESSION_TOKEN

    with TestClient(main.app, base_url="http://127.0.0.1",
                    raise_server_exceptions=False) as client:
        client.cookies.set("ptk", SESSION_TOKEN)
        client.headers.update({"Sec-Fetch-Site": "same-origin"})
        yield client


def test_a_read_only_folder_explains_how_to_fix_it(failing_route, lenient_client):
    failing_route["exc"] = OSError(errno.EROFS, "Read-only file system")
    page = lenient_client.get("/_test_boom")
    assert page.status_code == 500
    assert "couldn" in page.text and "Documents" in page.text
    assert "Internal Server Error" not in page.text


def test_any_other_error_is_a_page_too(failing_route, lenient_client):
    failing_route["exc"] = KeyError("anything")
    page = lenient_client.get("/_test_boom")
    assert page.status_code == 500 and "KeyError" in page.text and "Comment" in page.text


def test_a_translocated_mac_app_is_stopped_before_it_opens(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable",
                        "/private/var/folders/x/AppTranslocation/ABC/d/Privacy Toolkit.app"
                        "/Contents/MacOS/Privacy Toolkit")
    assert "read-only copy" in desktop._cannot_save_here()


def test_a_writable_folder_passes():
    assert desktop._cannot_save_here() == ""
