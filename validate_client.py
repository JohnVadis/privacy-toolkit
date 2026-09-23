#!/usr/bin/env python3
"""
validate_client.py — pre-flight check on a client file. Roadmap item #3.

    python validate_client.py --client clients/example_client.yaml
    python validate_client.py --client clients/example_client.yaml --forms hillsborough_clerk_redaction
    python validate_client.py --all

Catches a missing county / DOB / exemption.category BEFORE a form is generated with a
blank in it. Exits non-zero if there are errors.

This checks PRESENCE, never ELIGIBILITY. Whether a client actually qualifies for the
exemption they're claiming is a human judgement made outside this tool (CLAUDE.md
golden rule #1). Nothing here decides who qualifies.

One implementation, two front ends: the web app calls `validate_client()` directly and
renders the same issues inline on the edit form.
"""
from __future__ import annotations
import argparse
import sys
from dataclasses import dataclass, field as _field

from client_context import build_context, list_clients, load_client
from fill_forms import CATEGORIES, form_key_for, list_mappings, load_mapping

ROLES = ("primary", "spouse", "child", "dependent")

# Variables whose emptiness is normal on some forms and never worth a warning.
_QUIET_VARS = {"middle_name", "suffix", "home_line2"}


@dataclass
class Issue:
    """One problem with a client file.

    `field` is a dotted path into the client model (e.g. "persons.0.dob"). The web
    form uses it to anchor the message next to the input that caused it; the CLI
    just prints it.
    """
    field: str
    message: str
    severity: str = "error"          # "error" blocks; "warning" informs
    form_key: str = ""               # set when the issue is form-specific
    label: str = ""                  # human label for the offending input

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def _person_label(person: dict, index: int) -> str:
    name = " ".join(x for x in [person.get("first_name"), person.get("last_name")] if x)
    return name or (person.get("role") or f"person {index + 1}")


def _residence_index(person: dict) -> int:
    addrs = person.get("addresses") or []
    for i, a in enumerate(addrs):
        if a.get("residence"):
            return i
    return 0 if addrs else -1


def mapping_variables(mapping: dict) -> set[str]:
    """Every context variable a mapping reads via `from:` — both modes.

    Lets validation report "this form will have a blank here" generically, with no
    per-form code. Adding a mapping automatically extends the check.
    """
    names = set()
    for e in mapping.get("entries") or []:
        if isinstance(e, dict) and "from" in e:
            names.add(str(e["from"]))
    for e in (mapping.get("fields") or {}).values():
        if isinstance(e, dict) and "from" in e:
            names.add(str(e["from"]))
    return names


def _selected_mappings(form_keys=None) -> list[dict]:
    rows = [m for m in list_mappings() if not m["error"]]
    if form_keys:
        wanted = set(form_keys)
        rows = [m for m in rows if m["form_key"] in wanted]
    return rows


def validate_client(client: dict, form_keys=None) -> list[Issue]:
    """Return every issue found. Empty list (or warnings only) means safe to generate.

    form_keys: restrict form-specific checks to those forms. None = every mapping.
    """
    issues: list[Issue] = []
    case = client.get("case") or {}
    exm = case.get("exemption") or {}
    persons = client.get("persons") or []

    # ---- case ----
    if not str(case.get("display_name") or "").strip():
        issues.append(Issue("case.display_name", "Display name is required — it names the "
                                                 "output folder and every form title.",
                            label="Display name"))

    # ---- persons ----
    if not persons:
        issues.append(Issue("persons", "Add at least one person.", label="Persons"))

    seen_roles = set()
    for i, p in enumerate(persons):
        who = _person_label(p, i)
        role = str(p.get("role") or "").strip()
        if not role:
            issues.append(Issue(f"persons.{i}.role", f"{who}: role is required.", label="Role"))
        elif role not in ROLES:
            issues.append(Issue(f"persons.{i}.role",
                                f"{who}: role '{role}' is not one of {', '.join(ROLES)}.",
                                label="Role"))
        elif role in seen_roles:
            issues.append(Issue(f"persons.{i}.role",
                                f"Two people share the role '{role}'. "
                                "--person and the form pickers select by role, so each must be unique.",
                                label="Role"))
        seen_roles.add(role)

        if not (str(p.get("first_name") or "").strip() or str(p.get("last_name") or "").strip()):
            issues.append(Issue(f"persons.{i}.first_name",
                                f"{who}: a first or last name is required.", label="Name"))

        if not str(p.get("dob") or "").strip():
            issues.append(Issue(f"persons.{i}.dob", f"{who}: date of birth is required.",
                                label="Date of birth"))

        addrs = p.get("addresses") or []
        if not addrs:
            issues.append(Issue(f"persons.{i}.addresses",
                                f"{who}: at least one address is required.", label="Addresses"))
            continue

        ri = _residence_index(p)
        if not any(a.get("residence") for a in addrs):
            issues.append(Issue(f"persons.{i}.addresses.0.residence",
                                f"{who}: mark one address as the residence — forms fill from it.",
                                severity="warning", label="Residence"))
        elif sum(1 for a in addrs if a.get("residence")) > 1:
            issues.append(Issue(f"persons.{i}.addresses.{ri}.residence",
                                f"{who}: more than one address is marked as the residence; "
                                "the first one wins.", severity="warning", label="Residence"))

        home = addrs[ri]
        if not str(home.get("county") or "").strip():
            issues.append(Issue(f"persons.{i}.addresses.{ri}.county",
                                f"{who}: residence county is required — Clerk and Property "
                                "Appraiser forms are county-level.", label="County"))
        for key, label in (("line1", "Street address"), ("city", "City"),
                           ("state", "State"), ("zip", "ZIP")):
            if not str(home.get(key) or "").strip():
                issues.append(Issue(f"persons.{i}.addresses.{ri}.{key}",
                                    f"{who}: residence {label.lower()} is empty.",
                                    severity="warning", label=label))

    # ---- exemption category, only when a chosen form actually uses it ----
    mappings = _selected_mappings(form_keys)
    category_forms = [m["form_key"] for m in mappings if m["categories"]]
    category = str(exm.get("category") or "").strip()
    if category_forms:
        if not category:
            issues.append(Issue("case.exemption.category",
                                "Statutory category is required — "
                                f"{', '.join(category_forms)} check a box based on it.",
                                label="Statutory category"))
        elif category not in CATEGORIES:
            issues.append(Issue("case.exemption.category",
                                f"Category '{category}' is not one of {', '.join(CATEGORIES)}.",
                                label="Statutory category"))
        else:
            for m in mappings:
                if m["categories"] and category not in m["categories"]:
                    issues.append(Issue("case.exemption.category",
                                        f"{m['form_key']} has no box for '{category}' "
                                        f"(it covers {', '.join(m['categories'])}); "
                                        "that form will generate with no category marked.",
                                        severity="warning", form_key=m["form_key"],
                                        label="Statutory category"))

    # ---- form-specific blanks: does a selected form read a variable we can't fill? ----
    issues.extend(_blank_variable_warnings(client, persons, mappings))
    issues.extend(_document_warnings(client, persons, mappings))
    return issues


def _blank_variable_warnings(client, persons, mappings) -> list[Issue]:
    """Warn when a selected form reads a variable that resolves empty for a person.

    Generic: driven entirely by the mappings' `from:` keys, so a new mapping is
    covered the moment it lands. This is what catches a missing parcel_id on the
    Appraiser forms or a missing job_title on the Clerk form.
    """
    out = []
    for m in mappings:
        try:
            variables = mapping_variables(load_mapping(m["path"])) - _QUIET_VARS
        except Exception:
            continue
        for i, p in enumerate(persons):
            role = p.get("role") or None
            try:
                ctx = build_context(client, role)
            except Exception:
                continue
            blanks = sorted(v for v in variables if not str(ctx.get(v, "")).strip())
            if blanks:
                out.append(Issue(f"persons.{i}",
                                 f"{_person_label(p, i)}: {m['form_key']} will be blank at "
                                 f"{', '.join(blanks)}.",
                                 severity="warning", form_key=m["form_key"],
                                 label="Blank on form"))
    return out


def _document_warnings(client, persons, mappings) -> list[Issue]:
    """What the imported document list will do to the forms that print it.

    Never an error: a blank table is the documented fallback — the worker writes the
    documents in at the Clerk's counter. These say what WILL happen, so nobody
    discovers at the courthouse that three of seven records made it onto the form.
    """
    import documents as docs
    import pdf_overlay

    out = []
    stored = docs.documents(client)
    roles = {str(p.get("role") or "") for p in persons}

    for i, row in enumerate(stored):
        named = f"instrument {row['instrument_number']}" if row.get("instrument_number") \
            else f"book {row.get('book', '?')} page {row.get('page', '?')}"
        unknown = [w for w in (row.get("persons") or []) if w not in roles]
        if unknown:
            out.append(Issue(f"documents.{i}.persons",
                             f"Document {named} is limited to '{', '.join(unknown)}', which "
                             "is nobody on this client — it will print on no one's form.",
                             severity="warning", label="Documents"))
        if not row.get("title"):
            out.append(Issue(f"documents.{i}.title",
                             f"Document {named} has no document title.",
                             severity="warning", label="Documents"))

    for m in mappings:
        for table in m.get("tables") or []:
            listed = table.get("list", docs.DEFAULT_LIST)
            rows = docs.for_form(client, listed)
            capacity = int(table.get("rows") or 0)
            if not rows:
                if listed == docs.DEFAULT_LIST:
                    out.append(Issue("documents",
                                     f"{m['form_key']}: no documents imported, so the "
                                     "document table prints blank — to be filled in at the "
                                     "Clerk's office.", severity="warning",
                                     form_key=m["form_key"], label="Documents"))
                continue
            if capacity and len(rows) > capacity:
                out.append(Issue("documents",
                                 f"{m['form_key']}: {len(rows)} {listed} documents but the "
                                 f"form has {capacity} line(s) — {max(capacity - 1, 0)} print "
                                 f"on it and all {len(rows)} on Attachment "
                                 f"{table.get('attachment', 'A')}, which the requestor must "
                                 "initial.", severity="warning", form_key=m["form_key"],
                                 label="Documents"))
                continue
            out.extend(_truncation_warnings(m, table, rows, pdf_overlay))
    return out


def _truncation_warnings(mapping_row, table, rows, pdf_overlay) -> list[Issue]:
    """Values too long for their ruled cell, where no attachment carries the full one."""
    out = []
    size = float(table.get("size", 9))
    for row in rows[:int(table.get("rows") or len(rows))]:
        for col in table.get("columns") or []:
            value = str(row.get(col.get("from", ""), "") or "")
            if not value:
                continue
            shown, _ = pdf_overlay.fit_text(value, float(col.get("width", 0) or 0),
                                            float(col.get("size", size)))
            if shown.endswith("...") and shown[:-3] != value:
                out.append(Issue("documents",
                                 f"{mapping_row['form_key']}: \"{value}\" is too long for the "
                                 f"{str(col.get('from', '')).replace('_', ' ')} column and "
                                 "prints cut short.", severity="warning",
                                 form_key=mapping_row["form_key"], label="Documents"))
    return out


def errors(issues) -> list[Issue]:
    return [i for i in issues if i.is_error]


def warnings(issues) -> list[Issue]:
    return [i for i in issues if not i.is_error]


def issues_by_field(issues) -> dict:
    """Group issues by their dotted field path, for inline rendering on the edit form."""
    out: dict[str, list[Issue]] = {}
    for i in issues:
        out.setdefault(i.field, []).append(i)
    return out


def _report(name: str, issues: list[Issue]) -> int:
    errs, warns = errors(issues), warnings(issues)
    if not issues:
        print(f"[ok]   {name}: no issues.")
        return 0
    print(f"{'[FAIL]' if errs else '[warn]'} {name}: {len(errs)} error(s), {len(warns)} warning(s)")
    for i in errs:
        print(f"   ERROR  {i.field}: {i.message}")
    for i in warns:
        print(f"   warn   {i.field}: {i.message}")
    return 1 if errs else 0


def main():
    ap = argparse.ArgumentParser(description="Check a client file before generating forms.")
    ap.add_argument("--client", help="path to a client YAML")
    ap.add_argument("--all", action="store_true", help="check every file in clients/")
    ap.add_argument("--forms", nargs="*",
                    help="only consider these form_keys (default: all mappings)")
    ap.add_argument("--strict", action="store_true", help="treat warnings as failures too")
    args = ap.parse_args()

    if not args.client and not args.all:
        ap.error("pass --client <path> or --all")

    targets = []
    if args.client:
        targets.append((args.client, args.client))
    if args.all:
        targets += [(r["slug"], r["path"]) for r in list_clients()]

    bad = 0
    for name, path in targets:
        try:
            client = load_client(path)
        except Exception as exc:
            print(f"[FAIL] {name}: could not read — {exc}")
            bad += 1
            continue
        found = validate_client(client, args.forms)
        bad += _report(str(name), found)
        if args.strict and warnings(found):
            bad += 1

    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
