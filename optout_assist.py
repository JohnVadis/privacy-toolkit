#!/usr/bin/env python3
"""
optout_assist.py — fill data-broker opt-out forms from a client file; a human submits.

    python optout_assist.py --client clients/<name>.yaml            # sites not yet worked
    python optout_assist.py --client clients/<name>.yaml --sites spokeo,radaris
    python optout_assist.py --client clients/<name>.yaml --all --person spouse
    python optout_assist.py --client clients/<name>.yaml --list     # show the plan only

Opens a visible browser at each site's opt-out page. The worker finds the listing,
types `f` and the form is filled from the client file. The worker then checks it,
solves the CAPTCHA and clicks Submit THEMSELVES, and types `s` — which records the
submission in the client's worklist (output/<client>/opt_out_worklist.xlsx).

What this never does (CLAUDE.md rule 3, docs/decisions.md D4):
  * click, press Enter in, or otherwise submit a form;
  * touch a CAPTCHA, or any frame or field that looks like one;
  * overwrite a field that already holds something — the worker's typing wins;
  * decide that a listing is the client's.

How a field is matched is deterministic: its label / placeholder / name / id /
autocomplete is normalised and checked against FIELD_RULES below, in order. A site
whose form defeats that can carry its own `fill:` entries in sites.yaml, which are
tried first. No model calls, and no scraping — nothing is read back from a site.

The browser profile is a throwaway: cookies and form history vanish when it closes.
Needs Playwright (`pip install -r requirements-optout.txt`). It drives the Edge or
Chrome already installed, so there is no separate browser download.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
from dataclasses import dataclass

from build_worklist import (WORKLIST_FILENAME, load_sites, read_tracking,
                            record_progress)
from client_context import ClientDataError, build_context, load_client, output_dir

# Re-check a removal this many days after submitting it (the worklist's own advice).
VERIFY_AFTER_DAYS = 14

# Tried in order when launching. None is Playwright's own Chromium, which needs
# `python -m playwright install chromium` — the last resort, not the default.
BROWSER_CHANNELS = ("msedge", "chrome", None)

# Fields that must never be filled. Checked against the normalised descriptor.
NEVER_FILL = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|password|passcode|verification code|"
    r"\botp\b|\bssn\b|social security|card number|\bcvv\b|"
    r"\burl\b|\blink\b|profile (url|link|address|id)|username|user name|company|business|employer|"
    # Other names and other entities: a full name in "Maiden or alias" or "Legal
    # entity name" is a wrong answer on the record (both seen live, 2026-10-12).
    r"maiden|alias|\baka\b|nickname|organi[sz]ation|\bentity\b|former|previous|"
    r"\bprior\b|lived before|relative|"
    # ID documents: the client file doesn't hold them, and "Driver's License State"
    # is not the home state just because it usually is (LexisNexis, 2026-10-12).
    r"licen[cs]e|passport|"
    # Someone else's details, or a different fact about the client that a home or
    # birth-date rule would otherwise claim (each seen or checked 2026-10-13).
    r"\b(spouse|husband|wife|parents?|guardian|mother|father|child|children)\b|"
    r"place of birth|birth ?place|(city|state|county|country) of birth|"
    r"\bcard\b|expir|\bcountry\b|\bip\b|\bweb\b|website|\bpast\b|\bbefore\b|moved|"
    r"years? at|how long|extension|\bext\b|carrier|phone type|type of phone|"
    r"last ?4|last four|\bsuffix\b|"
    # "Last name, First name" in one box: the client file holds "First Last".
    r"\blast\b.*\bfirst\b")

# autocomplete tokens that mean "never this". Not the whole NEVER_FILL list: sites put
# autocomplete="new-password" on ordinary boxes just to stop the browser's own
# autofill (Zillow's address box, 2026-10-13). Real password boxes are type=password
# and never even listed.
NEVER_AUTOCOMPLETE = re.compile(r"one-time-code|\bcc-")

# Different kinds of answer. A box whose own words ask for two of them ("Enter
# name, phone or address") is a search box, not a form field — nothing fits it.
KINDS = {"name": r"\bname\b", "phone": r"phone|\btel\b|mobile",
         "email": r"e ?mail", "address": r"(?<!e mail )(?<!email )address"}

# Frames that belong to a CAPTCHA or bot-check vendor. Never filled, never read.
CAPTCHA_FRAMES = ("recaptcha", "hcaptcha", "challenges.cloudflare", "turnstile",
                  "arkoselabs", "funcaptcha", "captcha")

# (context variable, descriptor pattern, autocomplete tokens). First match wins, so
# the specific rules sit above the general ones — "first name" must be claimed
# before the bare "name" rule at the bottom can see it.
FIELD_RULES: list[tuple[str, str, set[str]]] = [
    ("email",       r"e ?mail",                                  {"email"}),
    ("full_name",   r"\bfirst\b.*\blast\b|full name",            set()),
    ("phone",       r"phone|\btel\b|telephone|mobile|\bcell\b",  {"tel", "tel-national"}),
    ("first_name",  r"\bfirst\b|given|\bfname\b|forename",       {"given-name"}),
    # A box for one letter gets one letter (maxlength would cut it anyway, silently).
    ("middle_initial", r"middle initial|\bmi\b",                 set()),
    ("middle_name", r"middle|\bmname\b",                         {"additional-name"}),
    ("last_name",   r"\blast\b|surname|family|\blname\b",        {"family-name"}),
    ("home_zip",    r"\bzip|postal|post code|postcode",          {"postal-code"}),
    ("home_city_state", r"\blocation\b",                         set()),
    ("home_city",   r"\bcity\b|\btown\b|locality",               {"address-level2"}),
    ("home_state",  r"\bstate\b|province|region",                {"address-level1"}),
    ("home_line2",  r"address (line )?2|\bapt\b|apartment|suite|\bunit\b",
                                                                 {"address-line2"}),
    ("home_line1",  r"street|address",                           {"address-line1",
                                                                  "street-address"}),
    # The parts first: "birth year" must get 1986, not the whole date.
    ("dob_month",   r"\bmonth\b|\bmm\b",                         {"bday-month"}),
    ("dob_day",     r"\bday\b|\bdd\b",                           {"bday-day"}),
    ("dob_year",    r"\byear\b|\byyyy\b",                        {"bday-year"}),
    ("dob",         r"birth|\bdob\b",                            {"bday"}),
    ("full_name",   r"\bname\b",                                 {"name"}),
]

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Runs in the page. Lists every field a person could type into, tags each with an
# index so Python can address it again, and reads nothing but its own attributes
# and label — never page content.
_DESCRIBE_JS = r"""
() => {
  const skipTypes = new Set(["hidden","submit","button","reset","image","file",
                             "checkbox","radio","password","range","color"]);
  const out = [];
  // Tags from an earlier Fill on this page: a field hidden since then keeps its old
  // number, which the new scan reuses — two elements, one index.
  document.querySelectorAll("[data-ptk-idx]").forEach(e => e.removeAttribute("data-ptk-idx"));
  const els = document.querySelectorAll("input, select, textarea");
  let i = 0;
  for (const el of els) {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "INPUT" && skipTypes.has(type)) continue;
    if (el.disabled || el.readOnly) continue;
    const r = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (r.width <= 2 || r.height <= 2 || style.visibility === "hidden"
        || style.display === "none") continue;
    // Honeypots: fields a person can't see, there to catch bots. Filling one can get
    // the request silently thrown away. Off the page, see-through, or hidden from
    // assistive tech — a person can't fill those, so neither does this.
    if (r.right + window.scrollX <= 0 || r.bottom + window.scrollY <= 0) continue;
    let hidden = false;
    for (let a = el; a && a.nodeType === 1; a = a.parentElement) {
      if (getComputedStyle(a).opacity === "0" || a.getAttribute("aria-hidden") === "true"
          || a.hasAttribute("inert")) { hidden = true; break; }
    }
    if (hidden) continue;
    let label = "";
    if (el.labels && el.labels.length) label = Array.from(el.labels).map(l => l.innerText).join(" ");
    const lb = el.getAttribute("aria-labelledby");
    if (!label && lb) label = lb.split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || "").join(" ");
    let options = [];
    let filled = false;
    if (el.tagName === "SELECT") {
      options = Array.from(el.options).map(o => ({value: o.value, text: o.text.trim()}));
      // Filled unless it shows a placeholder ("Select…", "--", value ""/"0").
      // A list with no placeholder (Alabama first) can't tell "untouched" from
      // "the worker chose Alabama" — so it counts as chosen and is left alone.
      const o = el.options[el.selectedIndex];
      const placeholder = !o || ["", "0", "-1"].includes(o.value)
        || /^\s*(-+|select|choose|please|pick|all|any|none|month|day|year|state)\b/i.test(o.text);
      filled = !placeholder;
    } else {
      filled = (el.value || "").trim() !== "";
    }
    const idx = String(i++);
    el.setAttribute("data-ptk-idx", idx);
    out.push({
      idx, tag: el.tagName.toLowerCase(), type,
      name: el.getAttribute("name") || "", id: el.id || "",
      placeholder: el.getAttribute("placeholder") || "",
      aria: el.getAttribute("aria-label") || "",
      autocomplete: (el.getAttribute("autocomplete") || "").toLowerCase(),
      label: (label || "").trim().slice(0, 200), options, filled,
    });
  }
  return out;
}
"""


@dataclass
class Fill:
    """One planned write: which field, from which variable, with what value."""
    idx: str
    var: str
    value: str
    kind: str          # "fill" | "select"
    shown_as: str      # how the field reads to a person — for the terminal report


def describe(field: dict) -> str:
    """A field's identifying text, normalised: "homeZip_code" -> "home zip code"."""
    raw = " ".join(field.get(k) or "" for k in
                   ("label", "aria", "placeholder", "name", "id"))
    raw = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def shown_as(field: dict) -> str:
    for k in ("label", "aria", "placeholder", "name", "id"):
        if field.get(k):
            return re.sub(r"\s+", " ", field[k]).strip()[:40]
    return field.get("tag", "field")


LOCATION_VARS = ("home_line1", "home_city", "home_state", "home_zip")


def derived(ctx: dict) -> dict:
    """Values a form asks for that the context holds in another shape."""
    extra = {"home_city_state": ", ".join(
        x for x in (ctx.get("home_city"), ctx.get("home_state")) if x),
             "middle_initial": (ctx.get("middle_name") or "")[:1].upper()}
    try:
        d = _dt.datetime.strptime(ctx.get("dob") or "", "%m/%d/%Y")
        extra.update(dob_month=f"{d.month:02d}", dob_day=f"{d.day:02d}",
                     dob_year=str(d.year))
    except ValueError:
        pass
    return extra


def match_variable(field: dict) -> str | None:
    """Which context variable this field asks for, or None. Pure; no page needed."""
    text = describe(field)
    if NEVER_FILL.search(text) or NEVER_AUTOCOMPLETE.search(field.get("autocomplete", "")):
        return None
    # Only what a person reads — a name attribute like "contact_name" says nothing.
    visible = describe({k: field.get(k) for k in ("label", "aria", "placeholder")})
    if sum(bool(re.search(p, visible)) for p in KINDS.values()) >= 2:
        return None
    # A field about the agent — the person filing for the client — takes the
    # agent's details, never the client's. Anything else about the agent is left.
    if re.search(r"\bagent\b|\brepresentative\b", text):
        if re.search(r"e ?mail", text) or field.get("type") == "email":
            return "agent_email"
        return "agent_name" if re.search(r"\bname\b", text) else None
    tokens = set(field.get("autocomplete", "").split())
    for var, _pattern, auto in FIELD_RULES:
        if tokens & auto:
            return var
    # A format hint ("MM/DD/YYYY") means one box for the whole date — not a month.
    if re.search(r"\bmm\b.*\bdd\b|\bdd\b.*\bmm\b", text):
        return "dob"
    for var, pattern, _auto in FIELD_RULES:
        if re.search(pattern, text):
            break
    else:
        # The input type only when the label says nothing — sites use type="tel"
        # for any digits (ZIP, date of birth, age) to get a number keypad.
        if field.get("type") == "email":
            return "email"
        if field.get("type") == "tel" and not text:
            return "phone"
        return None
    if var in LOCATION_VARS:
        # One box asking for several parts of an address. "City, State" gets both;
        # a search box ("City, Address, ZIP") can't be answered with one of them.
        asked = {v for v, p, _ in FIELD_RULES if v in LOCATION_VARS and re.search(p, text)}
        if {"home_city", "home_state"} <= asked and "home_line1" not in asked:
            return "home_city_state"
        if len(asked) > 1:
            return None
    return var


def value_for(var: str, field: dict, ctx: dict) -> tuple[str, str] | None:
    """(kind, value) to write into `field` for `var`, or None if there's nothing fit.

    A <select> only gets a value that is one of its own options — choosing the
    nearest option would be the tool guessing, and it doesn't guess.
    """
    value = str(ctx.get(var) or "").strip()
    if not value:
        return None

    if field.get("tag") == "select":
        wanted = {value.lower()}
        if var in ("dob_month", "dob_day"):
            wanted.add(str(int(value)))                    # "04" or "4"
        if var == "dob_month":
            month = _dt.date(2000, int(value), 1)
            wanted |= {month.strftime("%B").lower(), month.strftime("%b").lower()}
        if var == "home_state":
            abbr = value.upper() if value.upper() in US_STATES else next(
                (a for a, n in US_STATES.items() if n.lower() == value.lower()), "")
            if abbr:
                wanted |= {abbr.lower(), US_STATES[abbr].lower()}
        for opt in field.get("options") or []:
            if opt["value"].lower() in wanted or opt["text"].lower() in wanted:
                return "select", opt["value"]
        return None

    if var == "dob" and field.get("type") == "date":
        try:
            value = _dt.datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return "fill", value


def plan_fills(fields: list[dict], ctx: dict) -> tuple[list[Fill], list[dict]]:
    """Decide what goes where. Returns (fills, fields left for the worker).

    Pure: the browser only supplies `fields`, which is what makes the matching
    testable without one.
    """
    ctx = {**ctx, **derived(ctx)}
    fills, left = [], []
    for field in fields:
        if field.get("filled"):
            continue                      # the worker (or the site) put it there
        var = match_variable(field)
        planned = value_for(var, field, ctx) if var else None
        if planned is None:
            left.append(field)
            continue
        kind, value = planned
        fills.append(Fill(field["idx"], var, value, kind, shown_as(field)))
    return fills, left


def _is_captcha_frame(frame) -> bool:
    url = (frame.url or "").lower()
    return any(marker in url for marker in CAPTCHA_FRAMES)


def fill_page(page, ctx: dict, site: dict | None = None) -> dict:
    """Fill every recognisable empty field on `page` (and its frames). Never submits.

    Returns {"filled": [(shown_as, var)], "left": [shown_as], "errors": [str]}.
    Values are deliberately absent from the report — it goes to a terminal.
    """
    report = {"filled": [], "left": [], "errors": [], "moved_on": False}
    ctx = {**ctx, **derived(ctx)}
    start_url = page.url

    # A site's own `fill:` entries first: {selector: <css>, from: <variable>}.
    for entry in (site or {}).get("fill") or []:
        value = str(ctx.get(entry.get("from"), "") or "")
        selector = entry.get("selector")
        if not (value and selector):
            continue
        try:
            loc = page.locator(selector).first
            if loc.input_value(timeout=2000).strip():
                continue
            if (loc.evaluate("e => e.tagName") or "").lower() == "select":
                loc.select_option(value, timeout=2000)
            else:
                loc.fill(value, timeout=2000)
            report["filled"].append((selector, entry["from"]))
        except Exception as exc:  # noqa: BLE001 — a missing selector is a report line
            report["errors"].append(f"{selector}: {type(exc).__name__}")

    for frame in page.frames:
        if _is_captcha_frame(frame):
            continue
        try:
            fields = frame.evaluate(_DESCRIBE_JS)
        except Exception:  # noqa: BLE001 — a frame mid-navigation or cross-process
            continue
        fills, left = plan_fills(fields, ctx)
        # Dropdowns last: a site script on a select's change event is the likeliest
        # to reload or submit the form, and by then the typing is done.
        fills.sort(key=lambda f: f.kind == "select")
        frame_url = frame.url
        for f in fills:
            # Filling fires the site's own input/change handlers, and a site can
            # submit or navigate from one. That's the site, not this tool — but stop
            # at once and tell the worker, rather than typing into whatever loads.
            if page.is_closed() or frame.is_detached() or frame.url != frame_url \
                    or page.url != start_url:
                report["moved_on"] = True
                return report
            loc = frame.locator(f'[data-ptk-idx="{f.idx}"]')
            try:
                if f.kind == "select":
                    loc.select_option(f.value, timeout=2000)
                else:
                    loc.fill(f.value, timeout=2000)
                report["filled"].append((f.shown_as, f.var))
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{f.shown_as}: {type(exc).__name__}")
        report["left"].extend(shown_as(x) for x in left)
    if not page.is_closed() and page.url != start_url:
        report["moved_on"] = True
    return report


def site_domain(url: str) -> str:
    """"app.instantcheckmate.com" -> "instantcheckmate.com". Good enough to notice a
    tab on another company's site; not a public-suffix parser."""
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    return ".".join(host.split(".")[-2:])


def off_site(page_url: str, site: dict | None) -> bool:
    """True when the tab isn't on the site being worked — an ad, a pop-up, another
    company's form. The client's details shouldn't go there without a yes."""
    if not site or not site.get("opt_out_url"):
        return False
    return site_domain(page_url) != site_domain(site["opt_out_url"])


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
def pending_sites(client: dict, sites: list[dict]) -> list[dict]:
    """Sites whose worklist row hasn't been worked — what a default run visits."""
    tracked = read_tracking(output_dir(client) / WORKLIST_FILENAME)

    def worked(site):
        row = tracked.get(str(site.get("key"))) or tracked.get(site.get("name")) or {}
        return str(row.get("Status") or "not_started").strip() not in ("", "not_started")

    return [s for s in sites if not worked(s)]


def select_sites(client: dict, keys: str | None, include_all: bool,
                 person: str | None) -> list[dict]:
    sites = load_sites()
    if keys:
        wanted = [k.strip() for k in keys.split(",") if k.strip()]
        known = {s.get("key"): s for s in sites}
        missing = [k for k in wanted if k not in known]
        if missing:
            raise ClientDataError(f"not in sites.yaml: {', '.join(missing)}")
        return [known[k] for k in wanted]
    # The worklist tracks one status per site, for the primary person. Anyone else's
    # progress is kept in Notes, so it can't say what they still need — visit all.
    if include_all or not is_worklist_person(client, person):
        return sites
    return pending_sites(client, sites)


def site_status(client: dict) -> dict:
    """{site key: Status} from the client's worklist, for showing what's been done."""
    tracked = read_tracking(output_dir(client) / WORKLIST_FILENAME)
    return {s.get("key"): str((tracked.get(str(s.get("key")))
                               or tracked.get(s.get("name")) or {}).get("Status")
                              or "not_started")
            for s in load_sites()}


def is_worklist_person(client: dict, role: str | None) -> bool:
    """Whether the worklist's Status column is this person's.

    The worklist is built for the FIRST person in the file (build_context's
    default), whatever their role is called — nothing makes that one "primary".
    """
    if not role:
        return True
    persons = client.get("persons") or []
    return bool(persons) and persons[0].get("role") == role


def record_site(client: dict, site: dict, ctx: dict, status: str,
                ref: str = "") -> str:
    """Record one site's outcome in the worklist. Returns the re-check date.

    A non-primary person's outcome goes in Notes: the worklist holds one status per
    site and it is the primary's, so writing the spouse's there would overwrite it.
    Raises ClientDataError (e.g. the sheet is open in Excel) — callers offer a retry.
    """
    today = _dt.date.today()
    stamp = today.strftime("%m/%d/%Y")
    verify = (today + _dt.timedelta(days=VERIFY_AFTER_DAYS)).strftime("%m/%d/%Y")
    role = ctx.get("role") or ""
    key = str(site.get("key"))
    if is_worklist_person(client, role):
        record_progress(client, key, status=status, submitted=stamp,
                        verify_by=verify, confirmation=ref or None)
    else:
        who = ctx.get("full_name") or role
        note = f"{who} ({role}): {status} {stamp}" + (f", ref {ref}" if ref else "")
        record_progress(client, key, note=note)
    return verify


class Browser:
    """The visible browser the worker uses. Started on first use, restarted if closed.

    Headed, always: the worker has to see the page, judge the listing and click
    Submit. The context is a throwaway profile — cookies and form history are gone
    when it closes.
    """

    def __init__(self, channel: str | None = "auto"):
        self.channel = channel
        self._pw = self._browser = self._context = None

    def _start(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ClientDataError(
                "Playwright isn't installed — run: pip install -r requirements-optout.txt")
        if self._pw is None:
            self._pw = sync_playwright().start()
        if self._browser is not None:          # restarting: don't leave the old one
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001
                pass
        channels = BROWSER_CHANNELS if self.channel == "auto" else (self.channel or None,)
        last = None
        for ch in channels:
            try:
                self._browser = self._pw.chromium.launch(channel=ch, headless=False)
                break
            except Exception as exc:  # noqa: BLE001 — try the next installed browser
                last = exc
        else:
            raise ClientDataError(
                "couldn't start a browser — install Microsoft Edge or Google Chrome, or "
                f"run `python -m playwright install chromium` ({type(last).__name__})")
        self._context = self._browser.new_context()

    def _alive(self) -> bool:
        try:
            return bool(self._browser and self._browser.is_connected())
        except Exception:  # noqa: BLE001
            return False

    def open(self, url: str) -> bool:
        """Open `url` in a new tab. False if the page didn't load (the tab stays)."""
        if not self._alive():
            self._start()
        try:
            page = self._context.new_page()
        except Exception:  # noqa: BLE001 — the window was closed under us
            self._start()
            page = self._context.new_page()
        try:
            page.bring_to_front()
            # "commit": back as soon as the site starts answering, so the window
            # isn't frozen for a slow page — the worker watches it load.
            page.goto(url, wait_until="commit", timeout=30000)
            return True
        except Exception:  # noqa: BLE001
            return False

    def current_page(self):
        """The tab the worker is looking at. Not simply the newest: a pop-under or
        an ad opens tabs too, and the client's details mustn't land in one."""
        if not self._alive():
            return None
        pages = [p for p in self._context.pages if not p.is_closed()]
        front = []
        for p in pages:
            try:
                if p.evaluate("document.visibilityState") == "visible":
                    front.append(p)
            except Exception:  # noqa: BLE001 — mid-navigation
                continue
        for p in reversed(front):
            try:
                if p.evaluate("document.hasFocus()"):
                    return p
            except Exception:  # noqa: BLE001
                continue
        if front:
            return front[-1]
        return pages[-1] if pages else None

    def close(self):
        for thing in (self._context, self._browser):
            try:
                if thing:
                    thing.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            self._pw.stop()
        self._pw = self._browser = self._context = None


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


def _record(client, site, ctx, status, ref=""):
    while True:
        try:
            verify = record_site(client, site, ctx, status, ref)
            print(f"  recorded in {WORKLIST_FILENAME}: {status}, re-check by {verify}")
            return
        except ClientDataError as exc:
            print(f"  ! {exc}")
            if _ask("  Press Enter to retry, or type n to skip recording: ").lower() == "n":
                print("  not recorded — note it in the worklist by hand.")
                return


HELP = ("  f = fill this page    s = I submitted it    v = verification email sent\n"
        "  k = skip to next site    q = quit")


def run(client: dict, sites: list[dict], person: str | None, channel: str = "auto"):
    """The terminal version of the session. optout_panel.py is the windowed one."""
    ctx = build_context(client, person)
    who = ctx.get("full_name") or "the client"
    print(f"Opt-out assist for {who} ({ctx.get('role') or 'primary'}) — "
          f"{len(sites)} site(s).")
    print("Nothing is submitted for you: you check each form and click Submit.\n")

    browser = Browser(channel)
    try:
        for n, site in enumerate(sites, start=1):
            print(f"[{n}/{len(sites)}] {site.get('name')}  {site.get('opt_out_url')}")
            if site.get("notes"):
                print(f"  Guidance: {site['notes']}")
            if site.get("client_needed"):
                print(f"  NEEDS THE CLIENT: {site['client_needed']}")
            elif not site.get("supports_agent"):
                print("  Note: this site may not accept a request made by an agent.")
            for var in site.get("needs", []):
                if ctx.get(var):
                    print(f"    {var}: {ctx[var]}")
            if not browser.open(site["opt_out_url"]):
                print("  ! the page didn't load; navigate there yourself.")
            print(HELP)

            while True:
                cmd = _ask("> ").lower()
                if cmd in ("f", ""):
                    target = browser.current_page()
                    if target is None:
                        print("  no open tab to fill.")
                        continue
                    if off_site(target.url, site) and _ask(
                            f"  This tab is on {site_domain(target.url)}, not "
                            f"{site_domain(site['opt_out_url'])}. Fill it anyway? [y/N] "
                    ).lower() != "y":
                        continue
                    rep = fill_page(target, ctx, site)
                    print(f"  filled {len(rep['filled'])} field(s) on {target.url}")
                    if rep["moved_on"]:
                        print("  ! the page changed by itself while filling (the site's "
                              "own script) — check what it did before going on.")
                    for label, var in rep["filled"]:
                        print(f"    {label}  <- {var}")
                    if rep["left"]:
                        print(f"  left for you: {', '.join(rep['left'])}")
                    for err in rep["errors"]:
                        print(f"  ! {err}")
                    print("  Check every value, solve any CAPTCHA, then click Submit.")
                elif cmd == "s":
                    _record(client, site, ctx, "submitted",
                            _ask("  Confirmation / ref (Enter if none): "))
                    break
                elif cmd == "v":
                    _record(client, site, ctx, "verification_sent",
                            _ask("  Confirmation / ref (Enter if none): "))
                    break
                elif cmd == "k":
                    break
                elif cmd == "q":
                    return
                else:
                    print(HELP)
            print()
    finally:
        browser.close()
    print("Done. Check your email for verification links, and re-check removals "
          f"after ~{VERIFY_AFTER_DAYS} days.")


def main():
    ap = argparse.ArgumentParser(
        description="Fill opt-out forms from a client file. You review and submit.")
    ap.add_argument("--client", help="client file (leave out to pick in the window)")
    ap.add_argument("--person", help="person role to use (default: first/primary)")
    ap.add_argument("--sites", help="comma-separated site keys from sites.yaml")
    ap.add_argument("--all", action="store_true",
                    help="every site, including ones already worked")
    ap.add_argument("--browser", default="auto",
                    help="msedge | chrome | chromium (default: whichever is installed)")
    ap.add_argument("--list", action="store_true", help="show the sites and stop")
    ap.add_argument("--terminal", action="store_true",
                    help="work in this terminal instead of the window")
    args = ap.parse_args()
    channel = None if args.browser == "chromium" else args.browser

    if not args.terminal and not args.list:
        import optout_panel
        optout_panel.main(client_path=args.client, person=args.person, channel=channel)
        return
    if not args.client:
        ap.error("--client is needed with --terminal or --list")

    try:
        client = load_client(args.client)
        sites = select_sites(client, args.sites, args.all, args.person)
        if args.list or not sites:
            print("\n".join(f"{s.get('key'):28} {s.get('opt_out_url')}" for s in sites)
                  or "Nothing to do — every site has been worked. Use --all to revisit.")
            return
        run(client, sites, args.person, channel)
    except ClientDataError as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
