"""
client_context.py — load/save a client file and flatten it into template variables.

fill_forms.py, build_worklist.py, validate_client.py and the web app all use this,
so a form, a worklist and the UI always draw from the same single source of truth
(clients/<name>.yaml). YAML stays the only store — there is no database.
"""
from __future__ import annotations
import datetime as _dt
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
import yaml

from paths import (CLIENTS_DIR, OUTPUT_DIR, RESERVED_CLIENT_STEMS, TRASH_DIR,
                   client_output_dir, contains, resolve, slugify)

# Previous versions of every client file. The mappings have had this since day one;
# the client files — the only thing here that cannot be regenerated — did not.
CLIENT_BACKUP_DIR = CLIENTS_DIR / "_backups"
KEEP_BACKUPS = 20

# Serializes writes within this process. Across processes (the CLI while the app is
# open) the lock file below does it.
_write_lock = threading.Lock()
LOCK_TIMEOUT = 10.0          # seconds to wait for another process to finish
LOCK_STALE_AFTER = 60.0      # a lock older than this was left by something that died


# Every variable build_context() produces, grouped for a picker. Keeping the list
# here means the form editor offers exactly what the engine can actually fill —
# add a variable to build_context and it belongs here too.
CONTEXT_VARIABLES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Person", [
        ("full_name", "Full name"), ("first_name", "First name"),
        ("middle_name", "Middle name"), ("last_name", "Last name"),
        ("suffix", "Suffix"), ("dob", "Date of birth"),
        ("phone", "Phone"), ("email", "Email"), ("role", "Role"),
        ("owner_relationship", "Name / relationship to owner"),
    ]),
    ("Residence", [
        ("home_full", "Address, one line"), ("home_line1", "Street address"),
        ("home_line2", "Address line 2"), ("home_city", "City"),
        ("home_county", "County"), ("home_state", "State"),
        ("home_zip", "ZIP"), ("home_parcel_id", "Parcel / property ID"),
    ]),
    ("Case", [
        ("case_display_name", "Client display name"), ("case_number", "Case number"),
        ("agent_name", "Agent name"), ("agent_email", "Agent email"),
    ]),
    ("Exemption", [
        ("exemption_category", "Statutory category"), ("exemption_code", "Code"),
        ("exemption_title", "Title"), ("exemption_cite", "Citation"),
        ("job_title", "Job title"), ("employing_agency", "Employing agency"),
    ]),
    ("Other", [("today", "Today's date")]),
]

# Fields a checkbox rule can test, and what each one can equal. These are the only
# two used by any mapping, and they are what a tick box on a county form turns on.
MARK_CONDITIONS: dict[str, tuple[str, list[str]]] = {
    "role": ("Person's role", ["primary", "spouse", "child", "dependent"]),
    "exemption_category": ("Statutory category", ["military", "law_enforcement", "judges"]),
}


# A fictional client used only to preview a form's layout. Every variable has a
# value, and each is about as long as a real one, so a preview shows where things
# actually land and how much room they take. Never written to clients/ and never
# used for a filing — it exists so a layout can be checked without opening a real
# client's details.
EXAMPLE_CLIENT: dict = {
    "case": {
        "case_number": "QV-2026-000",
        "display_name": "Example, Alex",
        "acting_as_agent": True,
        "agent_name": "Sample Agent",
        "agent_email": "agent@example.com",
        "exemption": {
            "code": "FL_119_071_4d",
            "title": "Protected professions personnel (and family)",
            "cite": "s. 119.071(4)(d), F.S.",
            "category": "military",
            "job_title": "Staff Sergeant",
            "employing_agency": "U.S. Army",
        },
    },
    "persons": [{
        "role": "primary",
        "first_name": "Alexandra", "middle_name": "M", "last_name": "Example",
        "suffix": "", "dob": "1986-04-17",
        "phones": ["813-555-0175"], "emails": ["alex.example@example.com"],
        "addresses": [{
            "line1": "1420 Sample Ridge Drive", "line2": "Apt 21B",
            "city": "Brandon", "county": "Hillsborough", "state": "FL",
            "zip": "33511", "parcel_id": "U-00-11-22-3AB-000000-00000.0",
            "current": True, "residence": True,
        }],
    }],
}

EXAMPLE_SLUG = "__example__"


def variable_label(name: str) -> str:
    """Human label for a context variable, falling back to the name itself."""
    for _group, items in CONTEXT_VARIABLES:
        for key, label in items:
            if key == name:
                return label
    return (name or "").replace("_", " ").capitalize()


class ClientDataError(Exception):
    """A client file is missing something the caller asked for.

    Raised instead of SystemExit so a long-lived process (the web app) can catch
    it and show an error rather than exiting. The CLIs turn it back into a
    non-zero exit.
    """


# --------------------------------------------------------------------------
# load / save
# --------------------------------------------------------------------------
# Where a loaded client remembers which FILE it came from. Not part of the client
# model and never written back — `dump_client` drops it. It exists because the output
# folder has to be keyed on something unique, and a display name is not: two clients
# called "Smith, John" used to share one folder and silently overwrite each other's
# filled forms.
SLUG_KEY = "__slug__"


def client_slug(client: dict) -> str:
    """The file stem this client was loaded from, or "" for one built in memory."""
    return str((client or {}).get(SLUG_KEY) or "")


def load_client(path) -> dict:
    path = resolve(path)
    with open(path, encoding="utf-8") as fh:
        client = yaml.safe_load(fh) or {}
    if isinstance(client, dict):
        client[SLUG_KEY] = Path(path).stem
    return client


def client_path(slug: str) -> Path:
    """clients/<slug>.yaml for a client's file-stem slug (NOT the output-dir slug)."""
    return CLIENTS_DIR / f"{slug}.yaml"


def list_clients() -> list[dict]:
    """Every real client file, as summary rows for the client list.

    Skips _TEMPLATE and anything that fails to parse (reported via `error` so a
    broken file shows up in the UI instead of vanishing).
    """
    rows = []
    for p in sorted(CLIENTS_DIR.glob("*.yaml"), key=lambda q: q.stem.lower()):
        if p.stem in RESERVED_CLIENT_STEMS:
            continue
        row = {"slug": p.stem, "path": p, "error": None}
        try:
            client = load_client(p)
            case = client.get("case") or {}
            exm = case.get("exemption") or {}
            persons = client.get("persons") or []
            home = _residence(persons[0]) if persons else {}
            row.update(
                display_name=case.get("display_name") or "",
                case_number=case.get("case_number") or "",
                county=home.get("county") or "",
                category=exm.get("category") or "",
                person_count=len(persons),
                output_slug=output_dir(client).name,
            )
        except Exception as exc:  # a malformed file must not break the list
            row.update(display_name="", case_number="", county="", category="",
                       person_count=0, output_slug="", error=str(exc))
        rows.append(row)
    return rows


class _QuotingDumper(yaml.SafeDumper):
    """SafeDumper that double-quotes strings which would otherwise change type.

    "1985-06-15" must come back as a string, not a datetime.date, and "33563"
    must not come back as an int. Plain values (names, cities) stay unquoted so
    the file still reads like clients/_TEMPLATE.yaml.
    """


def _represent_str(dumper, data):
    # If PyYAML's own resolver would read this plain scalar back as a non-string,
    # force double quotes so it round-trips as text.
    tag = dumper.resolve(yaml.nodes.ScalarNode, data, (True, False))
    style = '"' if tag != "tag:yaml.org,2002:str" else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_QuotingDumper.add_representer(str, _represent_str)


def dump_client(client: dict) -> str:
    """Serialize a client dict to YAML in template order.

    Keys the toolkit attaches at runtime (SLUG_KEY) are dropped — the file records
    the client, not where it happened to be loaded from.
    """
    body = {k: v for k, v in (client or {}).items() if not str(k).startswith("__")}
    return yaml.dump(body, Dumper=_QuotingDumper, sort_keys=False,
                     allow_unicode=True, default_flow_style=False, width=100)


class _FileLock:
    """A crude cross-process lock, so the CLI and the app can't interleave a write.

    O_EXCL on a sibling file is the one primitive that behaves the same on Windows
    and POSIX. A lock left behind by a process that died is taken over once it is
    clearly stale, because the alternative — a tool that refuses to save until
    someone deletes a file they don't know about — is worse than the race it avoids.
    """

    def __init__(self, target: Path):
        self.path = target.with_suffix(target.suffix + ".lock")

    def __enter__(self):
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue                      # it vanished; try again
                if age > LOCK_STALE_AFTER:
                    self.path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    raise ClientDataError(
                        "Another copy of the toolkit is saving this client. Close the "
                        "other window (or wait a moment) and try again.")
                time.sleep(0.1)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
        return False


def backup_client(slug: str) -> Path | None:
    """Copy the current clients/<slug>.yaml aside. Returns where, or None if new."""
    src = client_path(slug)
    if not src.is_file():
        return None
    dest_dir = CLIENT_BACKUP_DIR / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_dt.datetime.now():%Y-%m-%d_%H%M%S}.yaml"
    shutil.copy2(src, dest)

    keep = sorted(dest_dir.glob("*.yaml"))[-KEEP_BACKUPS:]
    for old in sorted(dest_dir.glob("*.yaml"))[:-KEEP_BACKUPS]:
        if old not in keep:
            old.unlink(missing_ok=True)
    return dest


def save_client(slug: str, client: dict) -> Path:
    """Write clients/<slug>.yaml. Caller is responsible for validating first.

    Atomic, backed up, and locked. This file is the single source of truth for a
    client and there is no other copy of it: a half-written one loses the case.
    The content is rendered BEFORE anything is touched, so a serialization error
    leaves the existing file exactly as it was.
    """
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = client_path(slug)
    body = dump_client(client)

    with _write_lock, _FileLock(path):
        backup_client(slug)
        fd, tmp_name = tempfile.mkstemp(dir=str(CLIENTS_DIR), prefix=f".{slug}.",
                                        suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())        # the rename is only atomic if the data landed
            os.replace(tmp, path)            # atomic on Windows and POSIX alike
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    client[SLUG_KEY] = slug                  # it now has a file; remember which
    return path


def output_dir(client: dict) -> Path:
    """output/<folder>/ for this client. The one definition; everything routes here.

    Keyed on the client's FILE STEM, which the filesystem already guarantees to be
    unique. It used to be keyed on the display name, which guarantees nothing: two
    clients called "Smith, John" shared a folder, so generating for one overwrote the
    other's filled forms, and deleting either sent both to trash.

    A client saved before this change still has its folder under the old
    display-name slug, so that folder is adopted when it exists — nothing has to be
    moved, and no history is lost. New clients never collide.
    """
    slug = client_slug(client)
    legacy = _legacy_output_dir(client)

    if not slug:
        # An in-memory client with no file behind it (a form preview, a test).
        return legacy or client_output_dir("client")

    current = client_output_dir(slug)
    if current.is_dir():
        return current
    if legacy is not None and legacy.is_dir():
        return legacy
    return current


def _legacy_output_dir(client: dict) -> Path | None:
    """Where this client's output lived when the folder came from the display name.

    None when that name yields nothing usable — the case that used to resolve to
    output/ ITSELF and take every client's paperwork with it on delete.
    """
    name = slugify((client.get("case") or {}).get("display_name"))
    if not name:
        return None
    try:
        return client_output_dir(name)
    except ValueError:
        return None


def deletion_preview(slug: str) -> dict:
    """What deleting this client would move. Lets the UI show it before asking."""
    path = client_path(slug)
    try:
        out_dir = output_dir(load_client(path)) if path.is_file() else client_output_dir(slug)
    except Exception:
        out_dir = client_output_dir(slug)
    # Belt and braces: this path is about to be MOVED. If anything above ever
    # returns the output root again, refuse rather than take every client with it.
    if not contains(OUTPUT_DIR, out_dir) or out_dir.resolve() == OUTPUT_DIR.resolve():
        out_dir = None

    generated = sorted(p.name for p in out_dir.glob("*")) if out_dir and out_dir.is_dir() else []
    return {
        "slug": slug,
        "client_file": path,
        "client_file_exists": path.is_file(),
        "output_dir": out_dir if out_dir and out_dir.is_dir() else None,
        "generated": generated,
    }


def delete_client(slug: str) -> Path:
    """Move a client's file and generated output into trash/, and return where.

    Not an unlink. This holds an engagement's paperwork, and the person clicking
    is not necessarily the person who can recreate it — so deletion is reversible
    by moving a folder back. Emptying trash/ is a separate, deliberate act.
    """
    preview = deletion_preview(slug)
    if not preview["client_file_exists"]:
        raise ClientDataError(f"no client file at {preview['client_file']}")

    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = TRASH_DIR / f"{stamp}__{slugify(slug)}"
    dest.mkdir(parents=True, exist_ok=True)

    shutil.move(str(preview["client_file"]), str(dest / preview["client_file"].name))
    if preview["output_dir"]:
        shutil.move(str(preview["output_dir"]), str(dest / "output"))
    return dest


# --------------------------------------------------------------------------
# context building
# --------------------------------------------------------------------------
def _fmt_date(v) -> str:
    if not v:
        return ""
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.strftime("%m/%d/%Y")
    s = str(v).strip()
    # Accept "YYYY-MM-DD" typed as a quoted string and normalize to MM/DD/YYYY.
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return s


def _residence(person: dict) -> dict:
    addrs = person.get("addresses") or []
    return next((a for a in addrs if a.get("residence")), addrs[0] if addrs else {})


def _pick_person(client: dict, role: str | None):
    persons = client.get("persons") or []
    if not persons:
        raise ClientDataError("client file has no persons")
    if role:
        for p in persons:
            if p.get("role") == role:
                return p
        raise ClientDataError(f"no person with role '{role}' in client file")
    return persons[0]  # default: first (primary)


def person_roles(client: dict) -> list[str]:
    """Roles present in the file, in order — what --all-persons iterates over."""
    return [p.get("role", "") for p in (client.get("persons") or [])]


def build_context(client: dict, role: str | None = None) -> dict:
    """Flat dict of variables a mapping or worklist can reference."""
    case = client.get("case", {}) or {}
    exm = case.get("exemption", {}) or {}
    p = _pick_person(client, role)

    names = [p.get("first_name"), p.get("middle_name"), p.get("last_name"), p.get("suffix")]
    full_name = " ".join(n for n in names if n)

    home = _residence(p)
    home_parts = [home.get("line1"), home.get("line2"),
                  ", ".join(x for x in [home.get("city"), home.get("state")] if x)]
    home_full = ", ".join(x for x in home_parts if x)
    if home.get("zip"):
        home_full = f"{home_full} {home['zip']}".strip()

    phones = p.get("phones") or []
    emails = p.get("emails") or []

    role = p.get("role", "")
    rel = {"primary": "Self", "spouse": "Spouse", "child": "Dependent",
           "dependent": "Dependent"}.get(role, "")
    owner_relationship = f"{full_name} / {rel}".strip(" /") if rel else full_name

    return {
        # person
        "role": p.get("role", ""),
        "first_name": p.get("first_name", ""),
        "middle_name": p.get("middle_name", ""),
        "last_name": p.get("last_name", ""),
        "suffix": p.get("suffix", ""),
        "full_name": full_name,
        "dob": _fmt_date(p.get("dob")),
        "phone": phones[0] if phones else "",
        "email": emails[0] if emails else "",
        # residence
        "home_line1": home.get("line1", ""),
        "home_line2": home.get("line2", ""),
        "home_city": home.get("city", ""),
        "home_county": home.get("county", ""),
        "home_state": home.get("state", ""),
        "home_zip": home.get("zip", ""),
        "home_full": home_full,
        "home_parcel_id": home.get("parcel_id", ""),
        "owner_relationship": owner_relationship,
        # case / agent / exemption
        "case_number": case.get("case_number", ""),
        "case_display_name": case.get("display_name", ""),
        "agent_name": case.get("agent_name", ""),
        "agent_email": case.get("agent_email", ""),
        "exemption_code": exm.get("code", ""),
        "exemption_title": exm.get("title", ""),
        "exemption_cite": exm.get("cite", ""),
        "exemption_category": exm.get("category", ""),
        "job_title": exm.get("job_title", ""),
        "employing_agency": exm.get("employing_agency", ""),
        # misc
        "today": _dt.date.today().strftime("%m/%d/%Y"),
    }
