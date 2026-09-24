"""
conftest.py — run the whole suite against a sandbox, never the real data.

The toolkit anchors every path to `paths.ROOT`, so the tests point ROOT at a
temporary directory (via PRIVACY_TOOLKIT_HOME) BEFORE any toolkit module is
imported. That buys two things:

  * tests exercise the real save / delete / fill paths rather than mocks, and
  * a bug in one of those paths destroys a temp folder, not someone's case files.

`forms/` and `sites.yaml` are copied in, so mappings resolve and their `pdf:`
paths still find the blank PDFs when they are present. The real county PDFs are
gitignored, so tests that need one skip rather than fail on a fresh clone.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Redirect ROOT before anything imports paths.py. Import order matters here, so
# this runs at module scope rather than in a fixture.
# ---------------------------------------------------------------------------
# .resolve() to match what paths._root() does with the env var. On macOS /var is a
# symlink to /private/var, so mkdtemp() hands back /var/... while _root() resolves to
# /private/var/... — the same directory, unequal paths, and the guard below fired on
# every macOS run.
_SANDBOX = Path(tempfile.mkdtemp(prefix="privacy-toolkit-tests-")).resolve()
os.environ["PRIVACY_TOOLKIT_HOME"] = str(_SANDBOX)

for name in ("forms",):
    if (REPO / name).is_dir():
        shutil.copytree(REPO / name, _SANDBOX / name,
                        ignore=shutil.ignore_patterns("_backups", "__pycache__"))
if (REPO / "sites.yaml").is_file():
    shutil.copy2(REPO / "sites.yaml", _SANDBOX / "sites.yaml")

# The web app resolves its templates and static files against BUNDLED, which follows
# ROOT — so they have to exist in the sandbox too, or every page render 500s. The
# Python package itself is still imported from the repo via sys.path below.
for name in ("templates", "static"):
    src = REPO / "webapp" / name
    if src.is_dir():
        shutil.copytree(src, _SANDBOX / "webapp" / name,
                        ignore=shutil.ignore_patterns("__pycache__"))

sys.path.insert(0, str(REPO))

import paths  # noqa: E402  (must follow the env var above)

assert paths.ROOT == _SANDBOX, (
    "the sandbox did not take effect — tests would write to the real data dirs")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)


# ---------------------------------------------------------------------------
# Per-test isolation
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def clean_sandbox():
    """Empty every writable folder around each test, so order never matters.

    All four of these accumulate client data in normal use, which is exactly why
    they all belong here — leaving feedback/ out let the Comment tests' screenshots
    show up in a later test's housekeeping report.
    """
    def wipe():
        for d in (paths.CLIENTS_DIR, paths.OUTPUT_DIR, paths.TRASH_DIR,
                  paths.ROOT / "feedback"):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

    wipe()
    yield
    wipe()


# ---------------------------------------------------------------------------
# Shared data
# ---------------------------------------------------------------------------
@pytest.fixture
def example_client():
    """A fresh copy of the fictional client the toolkit ships for previews."""
    import copy

    from client_context import EXAMPLE_CLIENT

    return copy.deepcopy(EXAMPLE_CLIENT)


@pytest.fixture
def saved_client(example_client):
    """The fictional client written to the sandbox, loaded back with its identity."""
    from client_context import client_path, load_client, save_client

    save_client("Example Client", example_client)
    return load_client(client_path("Example Client"))


@pytest.fixture
def raw_client():
    """A test client with NO session token — for exercising the gate itself.

    base_url is loopback because the Host check is the first thing the middleware
    does, and TestClient's default "testserver" is exactly the foreign host it
    exists to refuse.
    """
    from fastapi.testclient import TestClient

    from webapp.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def app_client(raw_client):
    """A test client holding this session's token — for exercising the routes."""
    from webapp.security import SESSION_TOKEN

    raw_client.cookies.set("ptk", SESSION_TOKEN)
    raw_client.headers.update({"Sec-Fetch-Site": "same-origin"})
    return raw_client


def blank_pdf(name: str) -> Path:
    """A county blank PDF, or skip — they're gitignored, so CI won't have them."""
    path = paths.BLANK_PDF_DIR / name
    if not path.is_file():
        pytest.skip(f"{name} is not in forms/_blank_pdfs/ (gitignored)")
    return path


@pytest.fixture
def flat_form(tmp_path):
    """A synthetic one-page flat form: ruled lines at known coordinates.

    Built here rather than borrowed from forms/ so the overlay engine can be tested
    end to end on a fresh clone, with coordinates the test itself chose and can
    therefore assert against exactly.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = tmp_path / "flat_form.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 10)
    c.drawString(72, 700, "Printed Name:")
    c.drawString(72, 660, "Telephone Number:")
    c.drawString(72, 620, "Instrument Number      Book      Page      Document Title")
    for i in range(3):                     # three ruled rows under the headings
        y = 600 - (i * 18)
        c.line(72, y, 540, y)
    c.save()
    return path
