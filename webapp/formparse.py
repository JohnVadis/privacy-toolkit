"""
formparse.py — turn flat HTML form keys into the nested client model.

Inputs look like the usual bracket convention, which is what lets the persons /
addresses / phones sections repeat without any per-row server state:

    case[display_name]                      -> {"case": {"display_name": ...}}
    persons[0][dob]                         -> {"persons": [{"dob": ...}]}
    persons[0][addresses][1][city]          -> nested list inside a list
    persons[0][phones][]                    -> appends, in document order

Indices may be sparse (removing the middle row of 0,1,2 leaves 0,2). Lists are
rebuilt in numeric order, so gaps close without the browser having to renumber.

Scalars are last-wins, which makes the hidden-input + checkbox pairing work: an
unchecked box submits only the hidden "0", a checked one submits "0" then "1".
"""
from __future__ import annotations
import re

_BRACKET = re.compile(r"\[([^\[\]]*)\]")


def split_key(key: str) -> list[str]:
    """'persons[0][phones][]' -> ['persons', '0', 'phones', '']"""
    head = key.split("[", 1)[0]
    return [head] + _BRACKET.findall(key)


def _assign(root: dict, path: list[str], value):
    node = root
    for i, token in enumerate(path):
        last = i == len(path) - 1
        # "" means append: key it by the container's current size so document
        # order is preserved and _listify turns it back into a list.
        key = str(len(node)) if token == "" else token
        if last:
            node[key] = value
        else:
            nxt = node.get(key)
            if not isinstance(nxt, dict):
                nxt = {}
                node[key] = nxt
            node = nxt


def _listify(node):
    if not isinstance(node, dict):
        return node
    converted = {k: _listify(v) for k, v in node.items()}
    if converted and all(k.isdigit() for k in converted):
        return [converted[k] for k in sorted(converted, key=int)]
    return converted


def parse_nested(items) -> dict:
    """items: an iterable of (key, value) pairs, e.g. (await request.form()).multi_items()."""
    root: dict = {}
    for key, value in items:
        if not key:
            continue
        _assign(root, split_key(key), value)
    result = _listify(root)
    return result if isinstance(result, dict) else {}
