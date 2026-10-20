"""
sites_edit.py — add, remove and reorder the opt-out sites in sites.yaml.

The site list is shared by every client, so an edit here changes every worklist the
next time it is built. A removed site does NOT erase anyone's history: a rebuild
keeps a row the worker had touched, marked "(no longer in catalog)", because the
worklist matches rows on the site's key (build_worklist.py).

Every save:
  * keeps the file's explanatory header and its section comments,
  * leaves every setting of every other site exactly as it was (including `fill:`),
  * backs the previous version up to `_sites_backups/` first, and
  * writes through a temp file, so a crash can't leave half a list.

Engine-side, not web-side, so it can be tested and scripted like the rest.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yaml

import paths

# In the order the practice's checklist works through them — Google last, because
# it only shows what is still out there once the sources above are gone.
CATEGORIES: list[tuple[str, str]] = [
    ("data_broker", "Data brokers"),
    ("property_site", "Real estate"),
    ("maps", "Maps"),
    ("search_engine", "Search engines (last)"),
]
CATEGORY_NAMES = dict(CATEGORIES)

# The client details a site's form can ask for — what `needs` pre-fills on the
# worklist. A subset of client_context.CONTEXT_VARIABLES; the catalog test checks
# every `needs` value is a real variable.
NEEDS_CHOICES: list[tuple[str, str]] = [
    ("full_name", "Full name"), ("first_name", "First name"),
    ("middle_name", "Middle name"), ("last_name", "Last name"),
    ("dob", "Date of birth"), ("phone", "Phone"), ("email", "Email"),
    ("home_full", "Address, one line"), ("home_line1", "Street address"),
    ("home_city", "City"), ("home_state", "State"), ("home_zip", "ZIP"),
]
NEEDS_KEYS = [k for k, _ in NEEDS_CHOICES]

BACKUP_DIRNAME = "_sites_backups"
KEEP_BACKUPS = 50

# What a site's key may be: it goes into the worklist's hidden Key column and the
# page's URLs, so nothing that could be read as a path.
SAFE_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")


class SiteError(ValueError):
    """Something about a requested edit that a person needs to fix."""


def sites_file() -> Path:
    # Read at call time so tests (and a frozen app) always see the current root.
    return paths.SITES_FILE


def load() -> list[dict]:
    with open(sites_file(), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def make_key(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:40] or "site"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or "." not in parsed.netloc:
        raise SiteError("The opt-out link must be a full web address starting with "
                        "https:// — copy it from the browser's address bar.")
    return url


def add_site(name: str, url: str, category: str, *, needs=(), notes: str = "",
             client_needed: str = "", supports_agent: bool = True) -> dict:
    """Add a site at the end of its section. Returns the new site, with the name of
    the backup this change made under "_backup" (not saved in the file)."""
    name = " ".join((name or "").split())
    if not name:
        raise SiteError("Give the site a name.")
    if category not in CATEGORY_NAMES:
        raise SiteError("Choose what kind of site it is.")
    url = _clean_url(url)
    bad = [n for n in needs if n not in NEEDS_KEYS]
    if bad:
        raise SiteError(f"Unknown detail(s): {', '.join(bad)}")

    sites = load()
    if any((s.get("name") or "").lower() == name.lower() for s in sites):
        raise SiteError(f"{name} is already on the list.")
    if any((s.get("opt_out_url") or "").rstrip("/") == url.rstrip("/") for s in sites):
        raise SiteError("A site with that opt-out link is already on the list.")

    site = {"key": make_key(name, {s.get("key") for s in sites}),
            "name": name, "category": category, "opt_out_url": url,
            "needs": [n for n in NEEDS_KEYS if n in set(needs)],
            "supports_agent": bool(supports_agent)}
    if client_needed.strip():
        site["client_needed"] = client_needed.strip()
    site["notes"] = notes.strip()

    # After the last site of the same kind, so sections stay together and the
    # search engines stay last.
    order = [c for c, _ in CATEGORIES]
    rank = order.index(category)
    at = len(sites)
    for i, s in enumerate(sites):
        r = order.index(s["category"]) if s.get("category") in order else len(order)
        if r > rank:
            at = i
            break
    sites.insert(at, site)
    kept = save(sites)
    return dict(site, _backup=kept.name if kept else "")


def remove_site(key: str) -> dict:
    sites = load()
    for i, s in enumerate(sites):
        if s.get("key") == key:
            removed = sites.pop(i)
            kept = save(sites)
            return dict(removed, _backup=kept.name if kept else "")
    raise SiteError("That site isn't on the list any more.")


def move_site(key: str, direction: str) -> None:
    """Swap a site with its neighbour in the same section."""
    sites = load()
    idx = next((i for i, s in enumerate(sites) if s.get("key") == key), None)
    if idx is None:
        raise SiteError("That site isn't on the list any more.")
    other = idx - 1 if direction == "up" else idx + 1
    if not 0 <= other < len(sites) or sites[other].get("category") != sites[idx].get("category"):
        return
    sites[idx], sites[other] = sites[other], sites[idx]
    save(sites)


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------
class _Dumper(yaml.SafeDumper):
    pass


def _represent_list(dumper, data):
    # needs: [a, b] and fill: [{selector, from}] on one line each, as hand-written.
    flat = all(not isinstance(x, (list, dict)) for x in data) or all(
        isinstance(x, dict) and all(not isinstance(v, (list, dict)) for v in x.values())
        for x in data)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flat)


def _represent_dict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


def _represent_str(dumper, data):
    # Quote long text and anything with punctuation YAML could misread, as the
    # hand-written file does; leave short plain words bare.
    bare = 0 < len(data) <= 40 and re.fullmatch(r"[A-Za-z0-9_ ()\-—']+", data)
    style = None if bare else '"'
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Dumper.add_representer(list, _represent_list)
_Dumper.add_representer(dict, _represent_dict)
_Dumper.add_representer(str, _represent_str)


def _header(text: str) -> str:
    """The explanatory comment block at the top, up to the first section or site."""
    out = []
    for line in text.splitlines():
        if line.startswith("- ") or line.startswith("# --- "):
            break
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _section_comment(category: str) -> str:
    title = CATEGORY_NAMES.get(category) or (category or "Other").replace("_", " ").capitalize()
    head = f"# --- {title} "
    return head + "-" * max(3, 79 - len(head))


def render(sites: list[dict], header: str) -> str:
    parts = [header.rstrip() + "\n"] if header.strip() else []
    previous = object()
    for s in sites:
        if s.get("category") != previous:
            previous = s.get("category")
            parts.append(_section_comment(previous))
        dumped = yaml.dump([s], Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                           width=10_000, default_flow_style=False)
        parts.append(dumped.rstrip() + "\n")
    return "\n".join(parts)


def backup() -> Path | None:
    src = sites_file()
    if not src.is_file():
        return None
    dest_dir = src.parent / BACKUP_DIRNAME
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"sites.{_dt.datetime.now():%Y-%m-%d_%H%M%S_%f}.yaml"
    shutil.copy2(src, dest)
    for old in sorted(dest_dir.glob("sites.*.yaml"))[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def save(sites: list[dict]) -> Path | None:
    """Write the list, keeping the header. Returns where the old version was kept."""
    path = sites_file()
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    text = render(sites, _header(current))
    if yaml.safe_load(text) != sites:          # never write what can't be read back
        raise SiteError("The site list couldn't be written safely; nothing was changed.")
    kept = backup()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sites.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return kept


def latest_backup() -> Path | None:
    d = sites_file().parent / BACKUP_DIRNAME
    found = sorted(d.glob("sites.*.yaml")) if d.is_dir() else []
    return found[-1] if found else None


def undo(backup_name: str) -> None:
    """Undo ONE change: put back the version saved just before it.

    `backup_name` is the backup that change made (save() returns it, and the page
    carries it on its Undo button). If another change has been made since, undoing
    this one would quietly undo that one instead, so it is refused. The current
    list is backed up first, so even an undo can be undone.
    """
    last = latest_backup()
    if not last or last.name != Path(backup_name or "").name:
        raise SiteError("The list has changed since then, so that can't be undone from "
                        "here. Earlier versions are kept in the _sites_backups folder.")
    with open(last, encoding="utf-8") as fh:
        restored = yaml.safe_load(fh)
    if not isinstance(restored, list) or not all(isinstance(s, dict) for s in restored):
        raise SiteError("That earlier version is damaged; nothing was changed.")
    text = last.read_text(encoding="utf-8")
    last.unlink()                              # consumed: it is now the current list
    backup()                                   # ...and what it replaced is kept
    tmp = sites_file().with_suffix(".undo.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, sites_file())
