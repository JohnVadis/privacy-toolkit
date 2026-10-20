"""
sites.py — the opt-out site list: add a site, remove one, put them in order.

A thin front end over sites_edit.py, which owns the file. The list is shared by every
client; the page says so, and a removal can be undone from the page straight after.
"""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import sites_edit
from webapp.deps import templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()


def _page(request: Request, *, error: str = "", form: dict | None = None, status: int = 200):
    try:
        sites = sites_edit.load()
        load_error = ""
    except Exception as exc:                   # a hand-edit broke the file
        sites, load_error = [], f"sites.yaml can't be read: {exc}"
    groups = []
    for cat, title in sites_edit.CATEGORIES:
        rows = [dict(s, safe=bool(sites_edit.SAFE_KEY.match(str(s.get("key") or ""))))
                for s in sites if s.get("category") == cat]
        groups.append({"key": cat, "title": title, "sites": rows})
    other = [dict(s, safe=bool(sites_edit.SAFE_KEY.match(str(s.get("key") or ""))))
             for s in sites if s.get("category") not in sites_edit.CATEGORY_NAMES]
    if other:
        groups.append({"key": "", "title": "Other", "sites": other})
    q = request.query_params
    return templates.TemplateResponse(
        request=request, name="sites.html",
        context={
            "nav": "sites", "groups": groups, "count": len(sites),
            "categories": sites_edit.CATEGORIES, "needs_choices": sites_edit.NEEDS_CHOICES,
            "error": error or load_error, "form": form or {},
            "added": q.get("added", ""), "removed": q.get("removed", ""),
            "backup": q.get("backup", ""),
            "undone": q.get("undone") == "1",
        },
        status_code=status)


def _back(request: Request, **params) -> RedirectResponse:
    url = url_for(request, "sites_page")
    if params:
        url += "?" + urlencode(params)
    return RedirectResponse(url, status_code=303)


@router.get("/optout-sites", response_class=HTMLResponse, name="sites_page")
async def sites_page(request: Request):
    return _page(request)


@router.post("/optout-sites/add", name="site_add")
async def site_add(request: Request):
    data = parse_nested((await request.form()).multi_items())
    needs = data.get("needs") or []
    if isinstance(needs, str):
        needs = [needs]
    elif isinstance(needs, dict):
        needs = list(needs.values())
    try:
        site = sites_edit.add_site(
            str(data.get("name") or ""), str(data.get("opt_out_url") or ""),
            str(data.get("category") or ""), needs=[str(n) for n in needs],
            notes=str(data.get("notes") or ""),
            client_needed=str(data.get("client_needed") or ""),
            supports_agent=str(data.get("supports_agent") or "") == "1")
    except sites_edit.SiteError as exc:
        return _page(request, error=str(exc), form=data, status=400)
    return _back(request, added=site["name"], backup=site["_backup"])


@router.post("/optout-sites/{key}/remove", name="site_remove")
async def site_remove(request: Request, key: str):
    try:
        site = sites_edit.remove_site(key)
    except sites_edit.SiteError as exc:
        return _page(request, error=str(exc), status=404)
    return _back(request, removed=site.get("name") or key, backup=site["_backup"])


@router.post("/optout-sites/{key}/move", name="site_move")
async def site_move(request: Request, key: str):
    data = parse_nested((await request.form()).multi_items())
    try:
        sites_edit.move_site(key, "up" if data.get("direction") == "up" else "down")
    except sites_edit.SiteError as exc:
        return _page(request, error=str(exc), status=404)
    return _back(request)


@router.post("/optout-sites/undo", name="site_undo")
async def site_undo(request: Request):
    data = parse_nested((await request.form()).multi_items())
    try:
        sites_edit.undo(str(data.get("backup") or ""))
    except sites_edit.SiteError as exc:
        return _page(request, error=str(exc), status=400)
    return _back(request, undone="1")
