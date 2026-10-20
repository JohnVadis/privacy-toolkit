"""
counties.py — Florida's counties, and one spelling for each.

A county is how a client is matched to their county's forms: a form's mapping says
`county: Pinellas`, a client's residence says `county: Pinellas`, and the generate
page offers that client the Pinellas forms plus the statewide ones. That only works
if both are spelled the same way, so everything that stores a county goes through
`normalise()` and the app offers this list as a picker.

Adding a county's forms is then data, not code: upload the PDF on Form set-up, pick
the county, and every client living there gets it.
"""
from __future__ import annotations

import difflib
import re

FLORIDA_COUNTIES = [
    "Alachua", "Baker", "Bay", "Bradford", "Brevard", "Broward", "Calhoun", "Charlotte",
    "Citrus", "Clay", "Collier", "Columbia", "DeSoto", "Dixie", "Duval", "Escambia",
    "Flagler", "Franklin", "Gadsden", "Gilchrist", "Glades", "Gulf", "Hamilton", "Hardee",
    "Hendry", "Hernando", "Highlands", "Hillsborough", "Holmes", "Indian River", "Jackson",
    "Jefferson", "Lafayette", "Lake", "Lee", "Leon", "Levy", "Liberty", "Madison", "Manatee",
    "Marion", "Martin", "Miami-Dade", "Monroe", "Nassau", "Okaloosa", "Okeechobee", "Orange",
    "Osceola", "Palm Beach", "Pasco", "Pinellas", "Polk", "Putnam", "St. Johns", "St. Lucie",
    "Santa Rosa", "Sarasota", "Seminole", "Sumter", "Suwannee", "Taylor", "Union", "Volusia",
    "Wakulla", "Walton", "Washington",
]

# What a form that applies everywhere in the state says instead of a county.
STATEWIDE = "Statewide"

_SQUASH = re.compile(r"[^a-z]")


def _squash(text: str) -> str:
    return _SQUASH.sub("", (text or "").lower().replace("saint", "st"))


_BY_SQUASHED = {_squash(c): c for c in FLORIDA_COUNTIES}
_BY_SQUASHED["dade"] = "Miami-Dade"


def normalise(text: str, *, form: bool = False) -> str:
    """'hillsborough county', 'Hillsborough Co.', 'ST JOHNS' -> the county's own name.

    Anything not recognised comes back trimmed but otherwise untouched: an
    out-of-state client's county is still worth keeping as typed. `form=True` is
    for a FORM's county, where "Florida" or "statewide" means every client gets it;
    a client's own county is printed on their forms and is never "Statewide".
    """
    raw = " ".join(str(text or "").split())
    if not raw:
        return ""
    if form and _squash(raw) in ("statewide", "florida", "fl", "allcounties"):
        return STATEWIDE
    bare = re.sub(r"\s*\b(county|co\.?|cnty)\s*(,?\s*(fl|florida))?\.?\s*$", "", raw, flags=re.I)
    bare = re.sub(r",?\s*\b(fl|florida)\s*$", "", bare, flags=re.I)
    return _BY_SQUASHED.get(_squash(bare), raw)


def is_known(text: str) -> bool:
    return normalise(text) in FLORIDA_COUNTIES


def suggestion(text: str) -> str:
    """The Florida county a typo most likely meant, or ''."""
    squashed = _squash(normalise(text))
    if not squashed or squashed in _BY_SQUASHED:
        return ""
    close = difflib.get_close_matches(squashed, list(_BY_SQUASHED), n=1, cutoff=0.75)
    return _BY_SQUASHED[close[0]] if close else ""


def same(a: str, b: str) -> bool:
    na, nb = normalise(a), normalise(b)
    return bool(na) and na.lower() == nb.lower()
