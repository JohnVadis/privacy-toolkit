"""
model.py — build a client dict in clients/_TEMPLATE.yaml shape from posted form data.

The YAML file stays the single source of truth, so this is the only place the web
app decides what a saved client file looks like. Key order here is the key order
in the file, which keeps a UI-written client diff-comparable with a hand-written one.

Nothing here validates — validate_client.validate_client() does that, for both the
web app and the CLI.
"""
from __future__ import annotations

from paths import slugify

ROLES = ("primary", "spouse", "child", "dependent")
CATEGORIES = ("military", "law_enforcement", "judges")

PERSON_TEXT_FIELDS = ("role", "first_name", "middle_name", "last_name", "suffix", "dob")
ADDRESS_TEXT_FIELDS = ("line1", "line2", "city", "county", "state", "zip", "parcel_id")


def _s(value) -> str:
    return str(value).strip() if value is not None else ""


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _truthy(value) -> bool:
    return _s(value).lower() in ("1", "true", "yes", "on")


def blank_client() -> dict:
    """An empty client with one primary person — what GET /clients/new renders."""
    return {
        "case": {
            "case_number": "", "display_name": "", "acting_as_agent": True,
            "agent_name": "", "agent_email": "",
            "exemption": {"code": "", "title": "", "cite": "", "category": "",
                          "job_title": "", "employing_agency": ""},
        },
        "persons": [blank_person("primary")],
    }


def blank_person(role: str = "") -> dict:
    return {
        "role": role, "first_name": "", "middle_name": "", "last_name": "", "suffix": "",
        "dob": "", "phones": [""], "emails": [""], "addresses": [blank_address()],
    }


def blank_address() -> dict:
    return {"line1": "", "line2": "", "city": "", "county": "", "state": "", "zip": "",
            "parcel_id": "", "current": True, "residence": True}


def client_from_form(data: dict) -> dict:
    """Nested form data (see formparse) -> a client dict in template shape."""
    case_in = data.get("case") or {}
    exm_in = case_in.get("exemption") or {}

    client = {
        "case": {
            "case_number": _s(case_in.get("case_number")),
            "display_name": _s(case_in.get("display_name")),
            "acting_as_agent": _truthy(case_in.get("acting_as_agent")),
            "agent_name": _s(case_in.get("agent_name")),
            "agent_email": _s(case_in.get("agent_email")),
            "exemption": {
                "code": _s(exm_in.get("code")),
                "title": _s(exm_in.get("title")),
                "cite": _s(exm_in.get("cite")),
                "category": _s(exm_in.get("category")),
                "job_title": _s(exm_in.get("job_title")),
                "employing_agency": _s(exm_in.get("employing_agency")),
            },
        },
        "persons": [_person_from_form(p) for p in _as_list(data.get("persons"))],
    }
    return client


def _person_from_form(p) -> dict:
    p = p if isinstance(p, dict) else {}
    person = {k: _s(p.get(k)) for k in PERSON_TEXT_FIELDS}
    # Drop blank repeat rows so an untouched "add another" box doesn't land in the file.
    person["phones"] = [v for v in (_s(x) for x in _as_list(p.get("phones"))) if v]
    person["emails"] = [v for v in (_s(x) for x in _as_list(p.get("emails"))) if v]

    addresses = [_address_from_form(a) for a in _as_list(p.get("addresses"))]
    addresses = [a for a in addresses if any(_s(a.get(k)) for k in ADDRESS_TEXT_FIELDS)]

    # Exactly one residence, chosen by the radio group. If its row was dropped as
    # empty, fall back to the first address so forms still have somewhere to read.
    chosen = _s(p.get("residence_index"))
    for i, a in enumerate(addresses):
        a["residence"] = (str(i) == chosen)
    if addresses and not any(a["residence"] for a in addresses):
        addresses[0]["residence"] = True

    person["addresses"] = addresses
    return person


def _address_from_form(a) -> dict:
    a = a if isinstance(a, dict) else {}
    addr = {k: _s(a.get(k)) for k in ADDRESS_TEXT_FIELDS}
    addr["current"] = _truthy(a.get("current"))
    addr["residence"] = False  # set by the caller from the radio group
    return addr


# Keys that live on the client but are not rendered as form inputs. The edit form
# rebuilds the client dict from scratch, so without this they would be wiped the
# first time anyone pressed Save.
UNMANAGED_KEYS = ("documents", "corrections", "overrides", "marks")


def preserve_unmanaged(new: dict, existing: dict) -> dict:
    """Copy keys the edit form doesn't manage from the on-disk client onto the new one."""
    for key in UNMANAGED_KEYS:
        if existing.get(key):
            new[key] = existing[key]
    return new


def suggest_slug(client: dict, fallback: str = "client") -> str:
    """Filename stem for a new client: clients/<slug>.yaml, per the README convention."""
    display = ((client.get("case") or {}).get("display_name")) or ""
    return slugify(display) or slugify(fallback) or "client"
