"""
case_export.py — put a whole case in one file, so it can live somewhere else.

`clients/` and `output/` exist once, on one machine. A dropped laptop loses the
engagement: the client file, every filled form, and the record of what was produced.
This packages one case — or all of them — into a zip with a manifest, so there is
something to put on a drive, hand to a colleague, or keep as the closing record of a
finished matter.

What goes in:
  clients/<slug>.yaml            the single source of truth
  output/<...>                   every filled form, the worklist, the generation log
  manifest.json                  what's inside, with a SHA-256 of each file
  README.txt                     what this is, for whoever opens it in two years

The manifest is what makes it a backup rather than a pile: `verify()` re-hashes
every member, so you can tell a good archive from a truncated one before you rely on
it, and `restore()` refuses to unpack one that doesn't check out.

**An export is not encrypted.** Python's zipfile cannot write an encrypted archive,
and adding a dependency to do it would be a bigger promise than the toolkit can keep.
So this is plaintext client data in a single file: put it on an encrypted volume, and
treat losing one exactly as you'd treat losing the laptop.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import zipfile
from pathlib import Path

from client_context import ClientDataError, client_path, load_client, output_dir
from paths import CLIENTS_DIR, ROOT, contains

MANIFEST_NAME = "manifest.json"
README_NAME = "README.txt"
EXPORTS_DIR = ROOT / "exports"
FORMAT_VERSION = 1

README = """\
Privacy Removal Toolkit — exported case
=======================================

This archive is a complete copy of one client's case as it stood when exported.

  clients/    the client file — the single source of truth for every form
  output/     every filled form, the opt-out worklist, and .generation-log.jsonl
              (what was produced, from which mapping, with checksums)
  manifest.json
              an inventory with a SHA-256 of every file above

THIS FILE CONTAINS PERSONAL INFORMATION about a real person. Keep it on an
encrypted volume. It is not encrypted itself — a zip written by Python cannot be.

To put it back into a toolkit installation:

    python case_export.py --restore <this file>

That refuses to overwrite a client who already exists unless you add --replace.
You can also just open the archive and copy the two folders in by hand; nothing
here is a special format.
"""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_files(client: dict, slug: str) -> list[tuple[str, Path]]:
    """(name inside the archive, path on disk) for everything belonging to a case."""
    members: list[tuple[str, Path]] = []

    source = client_path(slug)
    if source.is_file():
        members.append((f"clients/{source.name}", source))

    # Named with the client's own output folder, so the archive mirrors the layout
    # it came from and a restore puts things back where they were. Flattening it to
    # "output/<file>" dropped everything into the output ROOT instead.
    out = output_dir(client)
    if out.is_dir():
        for path in sorted(out.rglob("*")):
            if path.is_file():
                inner = path.relative_to(out).as_posix()
                members.append((f"output/{out.name}/{inner}", path))
    return members


def manifest(client: dict, slug: str, entries: list[dict]) -> dict:
    case = client.get("case") or {}
    return {
        "format": FORMAT_VERSION,
        "exported_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "slug": slug,
        "display_name": case.get("display_name", ""),
        "case_number": case.get("case_number", ""),
        "file_count": len(entries),
        "total_bytes": sum(e["bytes"] for e in entries),
        "files": entries,
    }


def build(client: dict, slug: str) -> tuple[bytes, dict]:
    """The archive for one case, in memory. Returns (zip bytes, manifest).

    Held in memory on purpose: writing a temp file would leave a second plaintext
    copy of the case lying around for as long as the process took to finish.
    """
    entries = []
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in _case_files(client, slug):
            data = path.read_bytes()
            archive.writestr(name, data)
            entries.append({"name": name, "bytes": len(data),
                            "sha256": _sha256_bytes(data)})
        archive.writestr(README_NAME, README)
        book = manifest(client, slug, entries)
        archive.writestr(MANIFEST_NAME, json.dumps(book, indent=2, ensure_ascii=False))
    return buffer.getvalue(), book


def filename_for(slug: str, when: _dt.datetime | None = None) -> str:
    """A name for the archive: readable, and safe everywhere it has to travel.

    ASCII only, deliberately. This string becomes a Content-Disposition header when
    the app serves the file, and HTTP headers are latin-1 — an em-dash here raised
    UnicodeEncodeError and turned every download into a 500.
    """
    stamp = (when or _dt.datetime.now()).strftime("%Y-%m-%d")
    safe = "".join(c if (c.isascii() and (c.isalnum() or c in "-_ ")) else "_"
                   for c in slug).strip() or "case"
    return f"{safe} - case export {stamp}.zip"


def export(slug: str, dest_dir=None) -> tuple[Path, dict]:
    """Write one case's archive to disk. Returns (path, manifest)."""
    path = client_path(slug)
    if not path.is_file():
        raise ClientDataError(f"no client file at {path}")
    client = load_client(path)

    data, book = build(client, slug)
    dest_dir = Path(dest_dir) if dest_dir else EXPORTS_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / filename_for(slug)
    out.write_bytes(data)
    return out, book


# --------------------------------------------------------------------------
# reading one back
# --------------------------------------------------------------------------
def read_manifest(archive_path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        try:
            return json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except KeyError:
            raise ClientDataError("That zip has no manifest — it wasn't made by "
                                  "case_export.py.")
        except (ValueError, UnicodeDecodeError):
            raise ClientDataError("That archive's manifest is unreadable.")


def verify(archive_path) -> dict:
    """Re-hash every member against the manifest.

    Returns {ok, checked, missing, corrupt, manifest}. Worth running before you
    rely on a backup, and run for you before any restore.
    """
    book = read_manifest(archive_path)
    missing, corrupt = [], []
    with zipfile.ZipFile(archive_path) as archive:
        present = set(archive.namelist())
        for entry in book.get("files", []):
            name = entry.get("name", "")
            if name not in present:
                missing.append(name)
                continue
            if _sha256_bytes(archive.read(name)) != entry.get("sha256"):
                corrupt.append(name)
    return {
        "ok": not missing and not corrupt,
        "checked": len(book.get("files", [])),
        "missing": missing,
        "corrupt": corrupt,
        "manifest": book,
    }


def _safe_target(name: str, root: Path) -> Path | None:
    """Where an archive member may be written, or None if it may not be.

    A zip can name its members anything at all, including "../../../something".
    Unpacking one without checking is how an archive from outside writes wherever
    it likes; every member here is resolved and confirmed to land under `root`.
    """
    if name.startswith("/") or ".." in Path(name).parts:
        return None
    target = root / name
    return target if contains(root, target) else None


def restore(archive_path, replace: bool = False, root: Path | None = None) -> dict:
    """Unpack a case back into a toolkit installation.

    Refuses if the client already exists, unless `replace`. Verifies first: an
    archive that doesn't check out is not something to write over a live case with.
    """
    root = Path(root) if root else ROOT
    checked = verify(archive_path)
    if not checked["ok"]:
        raise ClientDataError(
            "That archive is damaged — "
            f"{len(checked['missing'])} file(s) missing, "
            f"{len(checked['corrupt'])} with the wrong contents. Nothing was written.")

    book = checked["manifest"]
    slug = str(book.get("slug") or "")
    if not slug:
        raise ClientDataError("That archive doesn't say which client it holds.")

    existing = (root / "clients" / f"{slug}.yaml")
    if existing.is_file() and not replace:
        raise ClientDataError(
            f"'{slug}' already exists here. Rename or remove it first, or pass "
            "--replace to overwrite it with the archive's version.")

    written, skipped = [], []
    with zipfile.ZipFile(archive_path) as archive:
        for entry in book.get("files", []):
            name = entry["name"]
            target = _safe_target(name, root)
            if target is None:
                skipped.append(name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            written.append(target)
    return {"slug": slug, "written": written, "skipped": skipped,
            "display_name": book.get("display_name", ""), "manifest": book}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _human(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.0f}"


def main():
    import argparse

    from client_context import list_clients

    ap = argparse.ArgumentParser(
        description="Package a case into one file, or put one back.")
    ap.add_argument("--client", help="path to a client YAML, or its slug")
    ap.add_argument("--all", action="store_true", help="export every client")
    ap.add_argument("--to", help="where to write (default: exports/)")
    ap.add_argument("--verify", metavar="ARCHIVE", help="check an archive and stop")
    ap.add_argument("--restore", metavar="ARCHIVE", help="unpack an archive")
    ap.add_argument("--replace", action="store_true",
                    help="with --restore, overwrite a client that already exists")
    args = ap.parse_args()

    try:
        if args.verify:
            result = verify(args.verify)
            book = result["manifest"]
            print(f"{book.get('display_name') or book.get('slug')} — exported "
                  f"{book.get('exported_at', '?')}")
            print(f"  {result['checked']} file(s), {_human(book.get('total_bytes', 0))}")
            if result["ok"]:
                print("  every file matches its checksum.")
            else:
                for name in result["missing"]:
                    print(f"  MISSING  {name}")
                for name in result["corrupt"]:
                    print(f"  CHANGED  {name}")
            raise SystemExit(0 if result["ok"] else 1)

        if args.restore:
            result = restore(args.restore, replace=args.replace)
            print(f"Restored {result['display_name'] or result['slug']} — "
                  f"{len(result['written'])} file(s).")
            for name in result["skipped"]:
                print(f"  refused an unsafe path in the archive: {name}")
            return

        if args.all:
            slugs = [row["slug"] for row in list_clients() if not row["error"]]
        elif args.client:
            slugs = [Path(args.client).stem]
        else:
            ap.error("pass --client, --all, --verify or --restore")

        if not slugs:
            raise SystemExit("No clients to export.")

        for slug in slugs:
            path, book = export(slug, args.to)
            print(f"[ok] {book.get('display_name') or slug}: {book['file_count']} "
                  f"file(s), {_human(path.stat().st_size)}  ->  {path}")
        print("\nThese archives hold personal information and are NOT encrypted.\n"
              "Put them somewhere encrypted.")
    except ClientDataError as exc:
        raise SystemExit(f"[FAIL] {exc}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"[FAIL] {exc}")


if __name__ == "__main__":
    main()
