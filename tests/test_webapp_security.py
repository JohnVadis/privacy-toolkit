"""The gate in front of every request.

CLAUDE.md golden rule #7: don't weaken this. Each check below stops something that
was demonstrably possible without it — a page the worker has open POSTing to the
app, a rebound DNS name pointing at loopback, another local process driving it.
These tests are here so removing one fails loudly instead of quietly.
"""
from __future__ import annotations

import pytest

from webapp.security import (ALLOWED_HOSTNAMES, TOKEN_COOKIE, TOKEN_PARAM,
                             SESSION_TOKEN, entry_url)

SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


class TestHostCheck:
    """Kills DNS rebinding: the browser sends the attacker's name as Host."""

    @pytest.mark.parametrize("host", ["evil.example", "attacker.test:8000",
                                      "privacy-toolkit.example.com"])
    def test_rejects_a_foreign_host(self, app_client, host):
        response = app_client.get("/", headers={"Host": host, **SAME_ORIGIN})
        assert response.status_code == 403

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost:1234"])
    def test_accepts_loopback(self, app_client, host):
        response = app_client.get("/", headers={"Host": host, **SAME_ORIGIN})
        assert response.status_code == 200

    def test_the_allowlist_is_loopback_only(self):
        assert ALLOWED_HOSTNAMES == {"127.0.0.1", "localhost", "[::1]", "::1"}


class TestCrossSiteCheck:
    """Stops a malicious page's form POST, which the same-origin policy does not."""

    @pytest.mark.parametrize("site", ["cross-site", "same-site"])
    def test_rejects_a_cross_site_request(self, app_client, site):
        response = app_client.get("/", headers={"Sec-Fetch-Site": site})
        assert response.status_code == 403

    def test_rejects_a_cross_site_post_even_with_the_token(self, app_client):
        response = app_client.post("/clients/new", data={},
                                   headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403

    def test_allows_a_top_level_navigation_from_outside(self, app_client):
        # Clicking the entry link from a note or a chat arrives as cross-site.
        # Safe because the session cookie is SameSite=Strict and so is NOT sent —
        # such a request can only pass the token check via ?k= in the URL.
        response = app_client.get("/", headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        })
        assert response.status_code == 200

    def test_a_navigation_allowance_does_not_extend_to_posts(self, app_client):
        response = app_client.post("/clients/new", data={}, headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        })
        assert response.status_code == 403

    def test_falls_back_to_origin_when_the_header_is_absent(self, raw_client):
        # curl and scripts send no Sec-Fetch-Site at all; a foreign Origin is still
        # refused. raw_client is used because app_client sets Sec-Fetch-Site as a
        # default header, which would take this branch out of play.
        raw_client.cookies.set(TOKEN_COOKIE, SESSION_TOKEN)
        response = raw_client.get("/", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403

    def test_a_loopback_origin_with_no_fetch_metadata_is_allowed(self, raw_client):
        raw_client.cookies.set(TOKEN_COOKIE, SESSION_TOKEN)
        response = raw_client.get("/", headers={"Origin": "http://127.0.0.1:8765"})
        assert response.status_code == 200


class TestTokenCheck:
    """Stops any other local process, which loopback alone does not."""

    def test_refuses_a_request_with_no_token(self, raw_client):
        assert raw_client.get("/", headers=SAME_ORIGIN).status_code == 403

    def test_refuses_a_wrong_token(self, raw_client):
        raw_client.cookies.set(TOKEN_COOKIE, "not-the-token")
        assert raw_client.get("/", headers=SAME_ORIGIN).status_code == 403

    def test_says_how_to_get_in_rather_than_just_refusing(self, raw_client):
        body = raw_client.get("/", headers=SAME_ORIGIN).text
        assert "link printed when it started" in body

    def test_accepts_the_token_in_the_url_and_trades_it_for_a_cookie(self, raw_client):
        response = raw_client.get(f"/?{TOKEN_PARAM}={SESSION_TOKEN}",
                                  headers=SAME_ORIGIN, follow_redirects=False)
        assert response.status_code == 303
        assert TOKEN_COOKIE in response.cookies

    def test_the_cookie_is_httponly_and_samesite_strict(self, raw_client):
        response = raw_client.get(f"/?{TOKEN_PARAM}={SESSION_TOKEN}",
                                  headers=SAME_ORIGIN, follow_redirects=False)
        header = response.headers["set-cookie"].lower()
        assert "httponly" in header and "samesite=strict" in header

    def test_the_redirect_strips_the_token_from_the_url(self, raw_client):
        response = raw_client.get(f"/?{TOKEN_PARAM}={SESSION_TOKEN}",
                                  headers=SAME_ORIGIN, follow_redirects=False)
        assert TOKEN_PARAM + "=" not in response.headers["location"]

    def test_the_redirect_keeps_the_other_query_params(self, raw_client):
        response = raw_client.get(f"/?deleted=abc&{TOKEN_PARAM}={SESSION_TOKEN}",
                                  headers=SAME_ORIGIN, follow_redirects=False)
        assert "deleted=abc" in response.headers["location"]

    def test_healthz_is_reachable_without_one(self, raw_client):
        response = raw_client.get("/healthz")
        assert response.status_code == 200 and response.text == "ok"

    def test_entry_url_carries_the_token(self):
        assert f"{TOKEN_PARAM}={SESSION_TOKEN}" in entry_url(8765)


class TestResponseHeaders:
    def test_pages_are_never_cached(self, app_client):
        # Every page here can show client PII.
        assert "no-store" in app_client.get("/").headers["cache-control"]

    def test_no_referrer_is_leaked(self, app_client):
        assert app_client.get("/").headers["referrer-policy"] == "no-referrer"

    def test_content_type_is_not_sniffed(self, app_client):
        assert app_client.get("/").headers["x-content-type-options"] == "nosniff"


class TestNoApiSurface:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_the_api_explorer_is_disabled(self, app_client, path):
        # This is a local tool, not a service.
        assert app_client.get(path).status_code == 404
