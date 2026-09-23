"""
clients.py — client list, and the new/edit form that writes clients/<slug>.yaml.

Saving goes through client_context.save_client(), so the file this produces is an
ordinary client YAML the CLI reads with no special casing.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from client_context import (ClientDataError, client_path, delete_client,
                            deletion_preview, list_clients, save_client)
from fill_forms import generated_files
from validate_client import errors, issues_by_field, validate_client, warnings
from webapp import model
from webapp.deps import clean_slug, get_client, templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()


def _field_errors(issues) -> dict:
    """{dotted field path: [message, ...]} for inline rendering next to inputs."""
    return {k: [i.message for i in v] for k, v in issues_by_field(issues).items()}


def _form_context(request: Request, client: dict, *, slug=None, issues=None, saved=False):
    issues = issues or []
    return {
        "request": request,
        "nav": "clients",
        "client": client,
        "slug": slug,
        "is_new": slug is None,
        "roles": model.ROLES,
        "categories": model.CATEGORIES,
        "errors": errors(issues),
        "warnings": warnings(issues),
        "field_errors": _field_errors(errors(issues)),
        "saved": saved,
    }


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse, name="client_list")
async def client_list(request: Request, deleted: str = ""):
    rows = list_clients()
    for row in rows:
        row["blocking"] = None
        row["file_count"] = 0
        if row["error"]:
            continue
        try:
            client = _safe_load(row["path"])
            row["blocking"] = len(errors(validate_client(client)))
            row["file_count"] = len(generated_files(client))
        except Exception:
            row["blocking"] = None
    return templates.TemplateResponse(
        request=request, name="clients/list.html",
        context={"nav": "clients", "rows": rows, "deleted": deleted},
    )


def _safe_load(path):
    from client_context import load_client
    return load_client(path)


# --------------------------------------------------------------------------
# HTMX fragments — one repeatable block each, rendered by the same templates
# the full page uses, so an added row is identical to a loaded one.
# --------------------------------------------------------------------------
@router.get("/clients/fragments/person", response_class=HTMLResponse, name="fragment_person")
async def fragment_person(request: Request, index: int = 0, role: str = ""):
    return templates.TemplateResponse(
        request=request, name="clients/_person.html",
        context={"p": model.blank_person(role), "pi": max(index, 0),
                 "roles": model.ROLES, "field_errors": {}},
    )


@router.get("/clients/fragments/address", response_class=HTMLResponse, name="fragment_address")
async def fragment_address(request: Request, pi: int = 0, index: int = 0):
    return templates.TemplateResponse(
        request=request, name="clients/_address.html",
        context={"a": model.blank_address(), "pi": max(pi, 0), "ai": max(index, 0),
                 "is_residence": False, "field_errors": {}},
    )


@router.get("/clients/fragments/contact", response_class=HTMLResponse, name="fragment_contact")
async def fragment_contact(request: Request, pi: int = 0, kind: str = "phones"):
    if kind not in ("phones", "emails"):
        raise HTTPException(status_code=404, detail="Unknown contact type.")
    return templates.TemplateResponse(
        request=request, name="clients/_contact.html",
        context={"pi": max(pi, 0), "kind": kind, "value": ""},
    )


# --------------------------------------------------------------------------
# new
# --------------------------------------------------------------------------
@router.get("/clients/new", response_class=HTMLResponse, name="client_new")
async def client_new(request: Request):
    return templates.TemplateResponse(
        request=request, name="clients/edit.html",
        context=_form_context(request, model.blank_client()),
    )


@router.post("/clients/new", response_class=HTMLResponse)
async def client_create(request: Request):
    form = await request.form()
    client = model.client_from_form(parse_nested(form.multi_items()))
    issues = validate_client(client)

    slug = model.suggest_slug(client)
    if not errors(issues) and client_path(slug).exists():
        issues.append(_duplicate_issue(slug))

    if errors(issues):
        return templates.TemplateResponse(
            request=request, name="clients/edit.html",
            context=_form_context(request, client, issues=issues), status_code=422,
        )

    save_client(slug, client)
    return RedirectResponse(_saved_url(request, slug), status_code=303)


def _saved_url(request: Request, slug: str) -> str:
    return url_for(request, "client_edit", slug=slug) + "?saved=1"


def _duplicate_issue(slug: str):
    from validate_client import Issue
    return Issue("case.display_name",
                 f"clients/{slug}.yaml already exists. Open that client instead, "
                 "or use a display name that differs.", label="Display name")


# --------------------------------------------------------------------------
# edit
# --------------------------------------------------------------------------
@router.get("/clients/{slug}/edit", response_class=HTMLResponse, name="client_edit")
async def client_edit(request: Request, slug: str, saved: int = 0):
    slug, client = get_client(slug)
    # Show the same warnings the CLI would, but never block on a GET.
    issues = warnings(validate_client(client)) if saved else []
    return templates.TemplateResponse(
        request=request, name="clients/edit.html",
        context=_form_context(request, client, slug=slug, issues=issues, saved=bool(saved)),
    )


@router.post("/clients/{slug}/edit", response_class=HTMLResponse)
async def client_update(request: Request, slug: str):
    slug = clean_slug(slug)
    if not client_path(slug).is_file():
        raise HTTPException(status_code=404, detail="No such client.")

    form = await request.form()
    client = model.client_from_form(parse_nested(form.multi_items()))
    # Manual form corrections aren't on this form; keep the ones already saved.
    client = model.preserve_unmanaged(client, _safe_load(client_path(slug)))
    issues = validate_client(client)

    if errors(issues):
        return templates.TemplateResponse(
            request=request, name="clients/edit.html",
            context=_form_context(request, client, slug=slug, issues=issues), status_code=422,
        )

    # The file name stays put even if the display name changed — renaming would
    # orphan bookmarks and the previously generated output folder.
    save_client(slug, client)
    return RedirectResponse(_saved_url(request, slug), status_code=303)


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------
@router.get("/clients/{slug}/delete", response_class=HTMLResponse, name="client_delete_confirm")
async def client_delete_confirm(request: Request, slug: str):
    """Ask first, and show exactly what will go. Never deletes on a GET."""
    slug, client = get_client(slug)
    return templates.TemplateResponse(
        request=request, name="clients/delete.html",
        context={
            "nav": "clients",
            "slug": slug,
            "display_name": (client.get("case") or {}).get("display_name") or slug,
            "preview": deletion_preview(slug),
        },
    )


@router.post("/clients/{slug}/delete", response_class=HTMLResponse, name="client_delete")
async def client_delete(request: Request, slug: str):
    slug, _ = get_client(slug)
    form = await request.form()
    # The confirmation page is the only thing that sends this.
    if form.get("confirm") != "yes":
        raise HTTPException(status_code=400, detail="Deletion was not confirmed.")
    try:
        dest = delete_client(slug)
    except ClientDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(
        url_for(request, "client_list") + f"?deleted={quote(dest.name)}", status_code=303)
