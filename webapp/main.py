"""
main.py — the local web UI for the privacy removal toolkit.

    uvicorn webapp.main:app --host 127.0.0.1 --port 8000 --no-access-log
    python -m webapp.main          # same thing, with the safe defaults baked in

This is a THIN WRAPPER over the CLI engine. It imports fill_forms, build_worklist,
client_context and validate_client and calls the same functions the CLI calls; it
contains no fill logic of its own. A client created here is an ordinary
clients/<slug>.yaml that `python fill_forms.py --client ...` fills identically.

The golden rules from CLAUDE.md, and how they hold here:

 1. Eligibility is human-verified. The worker picks `exemption.category` from a
    dropdown. No route infers, scores or decides who qualifies.
 2. Government forms are never auto-submitted. The only outputs are a filled PDF to
    preview and download. There is no submit route and no outbound HTTP client
    imported anywhere under webapp/.
 3. Opt-out sites are prepared, not submitted. /worklist builds the spreadsheet and
    renders sites.yaml read-only. No route opens a site, solves a CAPTCHA or posts
    a form.
 4. PII stays local. Binds to 127.0.0.1 on an OS-assigned port, serves vendored
    htmx/CSS (no CDN), and routes engine progress through a per-request collector
    so field values and client paths never reach a log. webapp/security.py rejects
    foreign Host headers, cross-site requests and anything without this session's
    token, so another local process — or a web page the worker has open — cannot
    drive the app.
 5. Deterministic. No LLM/model/API call is reachable from any request.
"""
from __future__ import annotations

import socket

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from webapp.deps import STATIC_DIR, templates
from webapp.routers import (clients, corrections, documents, feedback, forms,
                            generate)
from webapp.security import LocalOnlyMiddleware, entry_url

app = FastAPI(
    title="Privacy Removal Toolkit",
    description="Local UI over the deterministic form-fill engine. Prepares paperwork; "
                "a human reviews, notarizes and submits.",
    docs_url=None,       # no API explorer — this is a local tool, not a service
    redoc_url=None,
    openapi_url=None,
)

# Added last so it runs FIRST — every route and /static passes through the gate.
app.add_middleware(LocalOnlyMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(clients.router)
app.include_router(documents.router)
app.include_router(generate.router)
app.include_router(corrections.router)
app.include_router(forms.router)
app.include_router(feedback.router)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    """Render errors as a page rather than raw JSON. The detail never echoes PII."""
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"status": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.get("/healthz", response_class=HTMLResponse, include_in_schema=False)
async def healthz():
    return HTMLResponse("ok")


HOST = "127.0.0.1"


def bind(port: int = 0) -> tuple[socket.socket, int]:
    """Bind a loopback listening socket and report the port actually taken.

    Port 0 means "let the OS pick a free one", which is the default: a fixed,
    guessable port is one less thing for another local process to find. The socket
    is created here rather than inside uvicorn so the caller knows the port before
    the server starts, and so SO_REUSEADDR is *not* set — on Windows that option
    would let another process bind the same address underneath us.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((HOST, port))
    sock.listen(128)
    return sock, sock.getsockname()[1]


def make_server(sock: socket.socket):
    """A uvicorn Server for an already-bound socket. Caller runs it."""
    import uvicorn

    config = uvicorn.Config(app, access_log=False, log_level="warning")
    return uvicorn.Server(config), sock


def _open_browser_when_ready(server, url: str, timeout: float = 30.0) -> None:
    """Open the browser once uvicorn reports itself started.

    Waits on the server's own `started` flag rather than a fixed sleep or a port
    probe — the socket is already listening before uvicorn runs, so a port probe
    would succeed too early.
    """
    import threading
    import time
    import webbrowser

    def wait():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(server, "started", False):
                webbrowser.open(url)
                return
            time.sleep(0.1)

    threading.Thread(target=wait, daemon=True).start()


def run(port: int = 0, open_browser: bool = False):
    """Serve on loopback only. Overriding the host is a deliberate act, not a default."""
    sock, port = bind(port)
    server, sock = make_server(sock)
    url = entry_url(port)

    if open_browser:
        _open_browser_when_ready(server, url)
    # flush: stdout is block-buffered when redirected, and the URL below is the only
    # way in — it must not sit in a buffer until the process exits.
    print("\n  Privacy Removal Toolkit", flush=True)
    print(f"  Open: {url}", flush=True)
    print("  That link contains this session's key — treat it like a password.", flush=True)
    print("  Press Ctrl+C in this window to stop.\n", flush=True)
    server.run(sockets=[sock])


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    chosen_port = 0
    if "--port" in argv:
        chosen_port = int(argv[argv.index("--port") + 1])
    run(port=chosen_port, open_browser="--open-browser" in argv)
