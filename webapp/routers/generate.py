"""
generate.py — run the engine for a client, then preview / download what it produced.

Every route here is a thin call into fill_forms.fill_client() or
build_worklist.build_worklist() — the same functions the CLI calls. There is no
fill logic in this file, and nothing here submits anything anywhere: the only exits
are a PNG preview, a file download, and a link the worker clicks themselves.
"""
from __future__ import annotations

import io
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, RedirectResponse,
                               Response)

import generation_log
from build_worklist import WORKLIST_FILENAME, build_worklist, load_sites
from client_context import ClientDataError, person_roles
from fill_forms import fill_client, generated_files, list_mappings, output_dir
from validate_client import errors, validate_client, warnings
from webapp.deps import collector, get_client, output_file, templates, url_for
from webapp.formparse import parse_nested

router = APIRouter()

# Generated files hold PII. Never let a browser or proxy keep a copy.
NO_STORE = {"Cache-Control": "no-store, max-age=0", "Referrer-Policy": "no-referrer"}


def _people(client: dict) -> list[dict]:
    """The persons a form can be generated for, with a label for the picker."""
    out = []
    for p in client.get("persons") or []:
        name = " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x)
        out.append({"role": p.get("role") or "", "name": name or (p.get("role") or "unnamed")})
    return [p for p in out if p["role"]]


def _available_forms() -> list[dict]:
    return [m for m in list_mappings() if not m["error"] and m["pdf_present"]]


def _picker_context(request: Request, slug: str, client: dict, **extra) -> dict:
    ctx = {
        "request": request,
        "nav": "clients",
        "slug": slug,
        "client": client,
        "display_name": (client.get("case") or {}).get("display_name") or slug,
        "people": _people(client),
        "forms": _available_forms(),
        "output_rel": f"output/{output_dir(client).name}",
        "failure": "",
    }
    ctx.update(extra)
    return ctx


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------
@router.get("/clients/{slug}/generate", response_class=HTMLResponse, name="generate_page")
async def generate_page(request: Request, slug: str):
    slug, client = get_client(slug)
    found = validate_client(client)
    return templates.TemplateResponse(
        request=request, name="generate.html",
        context=_picker_context(request, slug, client,
                                errors=errors(found), warnings=warnings(found), results=None),
    )


@router.post("/clients/{slug}/generate", response_class=HTMLResponse, name="generate_run")
async def generate_run(request: Request, slug: str):
    slug, client = get_client(slug)
    data = parse_nested((await request.form()).multi_items())

    chosen_people = [str(r) for r in _as_list(data.get("persons"))] or person_roles(client)
    chosen_forms = [str(f) for f in _as_list(data.get("forms"))] or None

    # Same pre-flight the CLI runs, scoped to the forms actually selected.
    found = validate_client(client, chosen_forms)
    if errors(found):
        return templates.TemplateResponse(
            request=request, name="generate.html",
            context=_picker_context(request, slug, client, errors=errors(found),
                                    warnings=warnings(found), results=None),
            status_code=422,
        )

    # The engine's progress lines name the client and output paths, so they go to a
    # per-request collector shown once on the page — never to a server log.
    log, report = collector()
    failure = ""
    try:
        made = fill_client(client, persons=chosen_people, form_keys=chosen_forms,
                           report=report)
    except ClientDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # A locked PDF, a full disk, a mapping that resolves to nothing — the engine
        # may already have written some of the forms before failing. A bare 500 left
        # the worker with no idea which, so report what DID get made and name the
        # problem. The message is the exception's, which carries no field values.
        made = generated_before_failure(client, chosen_people, chosen_forms)
        failure = f"{type(exc).__name__}: {exc}"
        report(f"[FAIL] generation stopped — {failure}")

    results = [{"filename": p.name, "form_key": p.stem.split("__")[0],
                "person": p.stem.split("__")[-1]} for p in made]
    return templates.TemplateResponse(
        request=request, name="generate.html",
        context=_picker_context(request, slug, client, errors=[], warnings=warnings(found),
                                results=results, log=log, failure=failure,
                                selected_people=chosen_people, selected_forms=chosen_forms),
        status_code=500 if failure else 200,
    )


def generated_before_failure(client, people, forms) -> list:
    """The outputs that exist for this run, after the engine stopped part-way."""
    wanted = set(forms or [])
    roles = set(people or [])
    out = []
    for row in generated_files(client):
        if wanted and row["form_key"] not in wanted:
            continue
        if roles and row["person"] not in roles:
            continue
        out.append(row["path"])
    return out


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --------------------------------------------------------------------------
# preview / download
# --------------------------------------------------------------------------
@router.get("/clients/{slug}/preview/{filename}", name="preview_file")
async def preview_file(slug: str, filename: str):
    """Page 1 of a generated PDF as a PNG, rendered in memory.

    Nothing is written to disk, so previewing adds no second copy of the PII.
    """
    slug, client = get_client(slug)
    path = output_file(client, filename)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="No preview for that file type.")
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            image = pdf[0].render(scale=1.4).to_pil()
            buf = io.BytesIO()
            image.save(buf, format="PNG")
        finally:
            pdf.close()
    except Exception as exc:
        # Previews are a convenience; a rendering failure must not hide the download.
        raise HTTPException(status_code=503, detail=f"Could not render a preview: {exc}")
    return Response(content=buf.getvalue(), media_type="image/png", headers=NO_STORE)


MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/clients/{slug}/file/{filename}", name="inline_file")
async def inline_file(slug: str, filename: str):
    """Serve the file for display rather than saving — what the viewer embeds."""
    slug, client = get_client(slug)
    path = output_file(client, filename)
    media = MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    headers = dict(NO_STORE)
    headers["Content-Disposition"] = f'inline; filename="{path.name}"'
    return FileResponse(path, media_type=media, headers=headers)


@router.get("/clients/{slug}/download/{filename}", name="download_file")
async def download_file(slug: str, filename: str):
    slug, client = get_client(slug)
    path = output_file(client, filename)
    return FileResponse(path, filename=path.name, headers=NO_STORE,
                        media_type="application/octet-stream")


@router.get("/clients/{slug}/files", response_class=HTMLResponse, name="client_files")
async def client_files(request: Request, slug: str):
    """Everything ever generated for this client — not just this session's run."""
    slug, client = get_client(slug)
    return templates.TemplateResponse(
        request=request, name="files.html",
        context={
            "nav": "clients",
            "slug": slug,
            "display_name": (client.get("case") or {}).get("display_name") or slug,
            "files": _decorate(generated_files(client), client),
            "output_rel": f"output/{output_dir(client).name}",
        },
    )


@router.get("/clients/{slug}/view/{filename}", response_class=HTMLResponse, name="view_file")
async def view_file(request: Request, slug: str, filename: str):
    """Read a filled form in the app: every page, zoomable, printable."""
    slug, client = get_client(slug)
    path = output_file(client, filename)
    files = _decorate(generated_files(client), client)
    current = next((f for f in files if f["filename"] == path.name), None)
    return templates.TemplateResponse(
        request=request, name="view.html",
        context={
            "nav": "clients",
            "slug": slug,
            "display_name": (client.get("case") or {}).get("display_name") or slug,
            "filename": path.name,
            "current": current,
            "files": [f for f in files if f["is_pdf"]],
        },
    )


@router.post("/clients/{slug}/reveal/{filename}", name="reveal_file")
async def reveal_file(request: Request, slug: str, filename: str):
    """Open the folder holding this file, with it selected.

    The files are already on this machine — for a worker who needs to attach one
    to an email or print it, opening the folder beats re-saving a copy. Windows
    only; elsewhere this is a no-op and the page just reloads.
    """
    slug, client = get_client(slug)
    path = output_file(client, filename)
    try:
        if sys.platform == "win32":
            # Explorer's /select, needs the path QUOTED, and a list argument gets
            # quoted in a way it won't parse — with a space anywhere in the path it
            # silently opens Documents instead. A single command-line string is the
            # form that works. No shell is involved; `path` has already passed the
            # output-folder guard, and '"' can't appear in it.
            if '"' in str(path):
                raise ValueError("unexpected quote in path")
            subprocess.Popen(f'explorer /select,"{path}"')
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
    except Exception:
        pass  # a file manager that won't open must not break the page
    return RedirectResponse(url_for(request, "client_files", slug=slug), status_code=303)


def _decorate(rows: list[dict], client: dict | None = None) -> list[dict]:
    """Add the form title, the size, and what the generation log says about it."""
    titles = {m["form_key"]: (m.get("title") or "") for m in list_mappings()}
    out_dir = output_dir(client) if client else None
    for r in rows:
        r["title"] = titles.get(r["form_key"], "")
        r["size_h"] = _human_size(r["size"])
        # "Is the file I'm about to file the one the toolkit made?" — answerable
        # now, and worth answering on the screen where someone picks a file to send.
        r["provenance"] = (generation_log.verify(out_dir, r["filename"])
                           if out_dir else {"status": "unlogged"})
    return rows


def _human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.0f}"


# --------------------------------------------------------------------------
# opt-out worklist
# --------------------------------------------------------------------------
@router.get("/clients/{slug}/worklist", response_class=HTMLResponse, name="worklist_page")
async def worklist_page(request: Request, slug: str):
    slug, client = get_client(slug)
    existing = output_dir(client) / WORKLIST_FILENAME
    return templates.TemplateResponse(
        request=request, name="worklist.html",
        context=_worklist_context(request, slug, client,
                                  built=existing.name if existing.is_file() else None),
    )


@router.post("/clients/{slug}/worklist", response_class=HTMLResponse, name="worklist_run")
async def worklist_run(request: Request, slug: str):
    slug, client = get_client(slug)
    data = parse_nested((await request.form()).multi_items())
    person = str(data.get("person") or "") or None
    stats: dict = {}
    try:
        path = build_worklist(client, person, stats=stats)
    except ClientDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return templates.TemplateResponse(
        request=request, name="worklist.html",
        context=_worklist_context(request, slug, client, built=path.name, just_built=True,
                                  person=person, stats=stats),
    )


def _worklist_context(request: Request, slug: str, client: dict, **extra) -> dict:
    ctx = {
        "request": request,
        "nav": "clients",
        "slug": slug,
        "display_name": (client.get("case") or {}).get("display_name") or slug,
        "people": _people(client),
        "sites": load_sites(),
        "output_rel": f"output/{output_dir(client).name}",
        "built": None,
        "just_built": False,
        "person": None,
        "stats": {},
    }
    ctx.update(extra)
    return ctx
