"""
security.py — the gate in front of every request.

A loopback HTTP server is still reachable by anything else running on this machine,
including web pages open in a browser. Without these checks, a site the worker visits
can POST to the app and create or overwrite client files: form submissions are not
blocked by the same-origin policy, and a rebound DNS name pointing at 127.0.0.1 would
also pass. That was demonstrably possible before this module existed.

Three checks, cheapest first:

  1. Host        — the Host header's name must be 127.0.0.1 or localhost. Kills DNS
                   rebinding, where the browser sends `Host: attacker.example`.
  2. Cross-site  — `Sec-Fetch-Site` must be same-origin or none (a typed URL or a
                   click from outside the web). cross-site and same-site are refused,
                   which is what stops a malicious page's form POST. Clients that send
                   no Sec-Fetch-Site (curl, scripts) fall back to an Origin check and
                   are still gated by the token below.
  3. Token       — a secret minted per process. The desktop window (or the URL printed
                   at startup) carries it once as ?k=…; that sets an HttpOnly,
                   SameSite=Strict cookie and redirects to a clean URL. Another local
                   process cannot guess it.

None of this is a login. There are no users and no accounts — it exists so that
"listening on loopback" doesn't mean "anything on this box can drive the app".
"""
from __future__ import annotations

import hmac
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse

# Minted once per process. Restarting the app invalidates every old URL.
SESSION_TOKEN = secrets.token_urlsafe(32)

TOKEN_COOKIE = "ptk"
TOKEN_PARAM = "k"

ALLOWED_HOSTNAMES = {"127.0.0.1", "localhost", "[::1]", "::1"}

# Liveness only, returns the literal "ok" — safe to leave open so a launcher or a
# script can poll readiness without holding the token.
TOKEN_EXEMPT_PATHS = {"/healthz"}


def _hostname(host_header: str) -> str:
    """Strip the port, leaving the name. Handles bracketed IPv6."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """Enforce the three checks above. Applies to every route and to /static."""

    async def dispatch(self, request, call_next):
        # --- 1. Host -------------------------------------------------------
        if _hostname(request.headers.get("host", "")) not in ALLOWED_HOSTNAMES:
            return _refused("this request named a different host")

        # --- 2. cross-site --------------------------------------------------
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None:
            if fetch_site not in ("same-origin", "none") and not _entry_navigation(request):
                return _refused(f"this request came from {fetch_site}")
        else:
            origin = request.headers.get("origin")
            if origin and _hostname(origin.split("//", 1)[-1]) not in ALLOWED_HOSTNAMES:
                return _refused("this request came from another site")

        # --- 3. token -------------------------------------------------------
        if request.url.path not in TOKEN_EXEMPT_PATHS:
            if not _token_ok(request.cookies.get(TOKEN_COOKIE)):
                supplied = request.query_params.get(TOKEN_PARAM)
                if not _token_ok(supplied):
                    return PlainTextResponse(
                        "Forbidden — open this app from its own window or from the "
                        "link printed when it started.",
                        status_code=403,
                    )
                return _set_cookie_and_redirect(request)

        response = await call_next(request)
        # Generated pages carry PII; keep them out of any shared cache.
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


def _refused(because: str) -> PlainTextResponse:
    """A refusal that says which check fired.

    "Forbidden." on its own told a tester nothing they could report and told me
    nothing I could act on. Naming the check reveals nothing an attacker does not
    already know — they can see which request they sent — and turns a dead end into
    a bug report.
    """
    return PlainTextResponse(
        f"Forbidden — {because}.\n\n"
        "This app only answers its own window. If you are seeing this in normal use "
        "it is a bug worth reporting, along with this line.",
        status_code=403)


def _entry_navigation(request) -> bool:
    """True for a top-level GET page load — someone clicking the entry link.

    Clicking that link from anywhere outside the app (a note, a chat message, the
    browser's new-tab page) arrives as `Sec-Fetch-Site: cross-site`, and refusing
    it left the worker at a dead end with no explanation.

    Allowing it costs nothing, because the session cookie is SameSite=Strict and
    so is NOT sent on a cross-site navigation: such a request can only get past
    the token check if the token is in the URL, which is exactly the entry link.
    Form posts and fetches are unaffected — they are not navigations, so a
    malicious page still cannot reach a route that changes anything.
    """
    return (
        request.method == "GET"
        and request.headers.get("sec-fetch-mode") == "navigate"
        and request.headers.get("sec-fetch-dest") == "document"
    )


def _token_ok(value) -> bool:
    return bool(value) and hmac.compare_digest(str(value), SESSION_TOKEN)


def _set_cookie_and_redirect(request) -> RedirectResponse:
    """Trade the ?k=… in the URL for a cookie, then bounce to the clean URL.

    Keeps the token out of the address bar, out of link Referers and out of
    anything the worker might copy and paste.
    """
    params = [(k, v) for k, v in request.query_params.multi_items() if k != TOKEN_PARAM]
    url = request.url.replace_query_params(**dict(params)) if params else request.url.remove_query_params(TOKEN_PARAM)
    response = RedirectResponse(str(url), status_code=303)
    response.set_cookie(
        TOKEN_COOKIE, SESSION_TOKEN,
        httponly=True,       # unreadable from JavaScript
        samesite="strict",   # never sent on a cross-site request
        path="/",
        secure=False,        # http on loopback; Secure would stop it being stored
    )
    return response


def entry_url(port: int, host: str = "127.0.0.1") -> str:
    """The one URL that gets you in. Contains the token; treat it as a password."""
    return f"http://{host}:{port}/?{TOKEN_PARAM}={SESSION_TOKEN}"
