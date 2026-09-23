"""
client_context.py — load/save a client file and flatten it into template variables.

fill_forms.py, build_worklist.py, validate_client.py and the web app all use this,
so a form, a worklist and the UI always draw from the same single source of truth
(clients/<name>.yaml). YAML stays the only store — there is no database.
"""
from __future__ import annotations
import datetime as _dt
import shutil
from pathlib import Path
import yaml

from paths import (CLIENTS_DIR, OUTPUT_DIR, RESERVED_CLIENT_STEMS, TRASH_DIR,
                   resolve, slugify)


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
def load_client(path) -> dict:
    with open(resolve(path), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
                output_slug=slugify(case.get("display_name") or p.stem),
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
    """Serialize a client dict to YAML in template order."""
    return yaml.dump(client, Dumper=_QuotingDumper, sort_keys=False,
                     allow_unicode=True, default_flow_style=False, width=100)


def save_client(slug: str, client: dict) -> Path:
    """Write clients/<slug>.yaml. Caller is responsible for validating first."""
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = client_path(slug)
    path.write_text(dump_client(client), encoding="utf-8")
    return path


def deletion_preview(slug: str) -> dict:
    """What deleting this client would move. Lets the UI show it before asking."""
    path = client_path(slug)
    out_dir = OUTPUT_DIR / _output_slug(slug)
    generated = sorted(p.name for p in out_dir.glob("*")) if out_dir.is_dir() else []
    return {
        "slug": slug,
        "client_file": path,
        "client_file_exists": path.is_file(),
        "output_dir": out_dir if out_dir.is_dir() else None,
        "generated": generated,
    }


def _output_slug(slug: str) -> str:
    """The output/ folder name for a client — from display_name, not the file stem."""
    try:
        display = (load_client(client_path(slug)).get("case") or {}).get("display_name")
    except Exception:
        display = None
    return slugify(display or slug)


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
