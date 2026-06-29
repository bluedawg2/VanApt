"""Heuristics for pulling listing data out of modern JS sites.

Many rental sites (Kijiji, rentals.ca, PadMapper) are Next.js/React apps that
embed their data as JSON in the HTML (``__NEXT_DATA__`` or inline state). Rather
than brittle CSS selectors, we extract that JSON and recursively hunt for
dict objects that *look like* a rental listing. This survives most layout
changes; only a total data-shape change breaks it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterator

_NEXT = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_LDJSON = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)

PRICE_KEYS = {"price", "rent", "amount", "minprice", "monthlyrent", "askingprice",
              "pricevalue", "displayprice"}
TITLE_KEYS = {"title", "name", "heading", "headline", "summary"}
URL_KEYS = {"url", "seourl", "href", "link", "weburl", "permalink", "vipurl"}
LAT_KEYS = {"lat", "latitude"}
LNG_KEYS = {"lng", "lon", "long", "longitude"}
BED_KEYS = {"bedrooms", "beds", "numbedrooms", "bedroomcount", "br"}
SQFT_KEYS = {"sqft", "squarefeet", "size", "area", "floorsize"}


def embedded_json_blobs(html: str) -> Iterator[Any]:
    for m in _NEXT.finditer(html):
        try:
            yield json.loads(m.group(1))
        except Exception:
            pass
    for m in _LDJSON.finditer(html):
        try:
            yield json.loads(m.group(1))
        except Exception:
            pass


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v.replace(",", ""))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    if isinstance(v, dict):  # e.g. {"amount": 1800} or {"value": 1800}
        for k in ("amount", "value", "min", "raw"):
            if k in v:
                return _num(v[k])
    return None


def extract_assigned_json(html: str, var_pattern: str):
    """Extract a JSON object assigned to a JS variable, e.g.
    ``window.__PRELOADED_STATE__ = {...};``. Brace-balances to find the end."""
    m = re.search(var_pattern + r"\s*=\s*(\{)", html)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except Exception:
                    return None
    return None


def normalize_rent(value):
    """Several sites store price in cents (e.g. 179500 == $1795/mo). Monthly
    rents we care about are < ~3k, so any value >= 8000 is treated as cents."""
    n = _num(value)
    if n is None:
        return None
    if n >= 8000:
        n = n / 100.0
    return int(round(n))


def _lower_keys(d: dict) -> dict:
    return {str(k).lower(): k for k in d.keys()}


def looks_like_listing(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    lk = _lower_keys(d)
    has_price = any(k in lk for k in PRICE_KEYS)
    has_locish = any(k in lk for k in (TITLE_KEYS | URL_KEYS | {"address", "location"}))
    return has_price and has_locish


def extract_field(d: dict, keys: set[str]):
    lk = _lower_keys(d)
    for k in keys:
        if k in lk:
            return d[lk[k]]
    return None


def find_listings(obj: Any, _depth: int = 0, _seen: int = 0) -> Iterator[dict]:
    """Yield dicts that look like listings, anywhere in a nested structure."""
    if _depth > 25 or _seen > 200000:
        return
    if isinstance(obj, dict):
        if looks_like_listing(obj):
            yield obj
        for v in obj.values():
            yield from find_listings(v, _depth + 1, _seen + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_listings(v, _depth + 1, _seen + 1)
