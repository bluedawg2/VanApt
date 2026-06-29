"""Group likely-duplicate listings (same unit posted to multiple sites, or
re-posted) so the UI shows each place once with all its source links."""
from __future__ import annotations

import difflib

from . import db
from .geo import haversine_m
from .models import _title_tokens


def _similar_titles(a: str, b: str) -> float:
    ta, tb = set(_title_tokens(a)), set(_title_tokens(b))
    if ta and tb:
        jac = len(ta & tb) / len(ta | tb)
        if jac >= 0.6:
            return jac
    # Fall back to sequence ratio for short/odd titles.
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _weak_geo(r) -> bool:
    """A Facebook capture with no coordinates. Its card titles are generic
    ('1 Bed 1 Bath - House') and it has no location to disambiguate with, so
    fuzzy title matching alone is unsafe — see _maybe_duplicate."""
    return r["source"] == "facebook" and (r["lat"] is None or r["lng"] is None)


# Words that say nothing about *which* unit this is. A title built only from
# these (typical of Facebook Marketplace cards) is too generic to merge on.
_GENERIC_TOKENS = {
    "bed", "beds", "bedroom", "bedrooms", "bath", "baths", "bathroom",
    "house", "apartment", "apt", "condo", "townhouse", "suite", "basement",
    "studio", "room", "rooms", "private", "shared", "rent", "rental", "for",
    "unit", "floor", "den", "and", "with", "near", "new", "renovated", "large",
    "small", "cozy", "bright", "spacious", "clean", "available", "now",
}


def _is_generic_title(t: str) -> bool:
    """True when nothing distinctive (a street, neighbourhood, building name…)
    survives after dropping boilerplate words — so the title is no evidence of
    a duplicate on its own."""
    distinctive = [w for w in _title_tokens(t)
                   if w not in _GENERIC_TOKENS and not w.isdigit()]
    return not distinctive


def _maybe_duplicate(r1, r2) -> bool:
    # Never merge a whole-unit listing with a room-share.
    if r1["listing_type"] != r2["listing_type"]:
        return False

    # Two coordinate-less Facebook posts can't be told apart from genuinely
    # different units that happen to share a boilerplate title + price, so never
    # auto-merge them. Exact re-imports are already collapsed by uid upstream,
    # so this only protects distinct listings from being wrongly hidden.
    if _weak_geo(r1) and _weak_geo(r2):
        return False

    # Price must match closely (allow small rounding/typo differences).
    p1, p2 = r1["price"], r2["price"]
    if p1 and p2 and abs(p1 - p2) > 50:
        return False

    # Bedrooms must match when both known.
    b1, b2 = r1["bedrooms"], r2["bedrooms"]
    if b1 is not None and b2 is not None and abs(b1 - b2) > 0.01:
        return False

    # Strong geo signal: same price + very close coords => duplicate.
    if all(r1[k] is not None for k in ("lat", "lng")) and \
       all(r2[k] is not None for k in ("lat", "lng")):
        d = haversine_m(r1["lat"], r1["lng"], r2["lat"], r2["lng"])
        if d <= 120 and (not (p1 and p2) or abs(p1 - p2) <= 50):
            return True
        if d > 400:  # clearly different buildings
            return False

    # Title similarity as the general signal. When one side is a coord-less
    # Facebook post, demand a higher bar AND corroborating price+bedrooms (both
    # known and matching), since FB's generic titles inflate similarity.
    if _weak_geo(r1) or _weak_geo(r2):
        if not (p1 and p2) or b1 is None or b2 is None:
            return False
        if _is_generic_title(r1["title"]) or _is_generic_title(r2["title"]):
            return False  # generic boilerplate title is no evidence on its own
        return _similar_titles(r1["title"], r2["title"]) >= 0.75
    return _similar_titles(r1["title"], r2["title"]) >= 0.72


class _UF:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def rebuild() -> int:
    """Recompute dedup groups over all rows. Returns number of duplicate
    listings collapsed."""
    rows = db.all_rows()
    uf = _UF([r["uid"] for r in rows])

    # Bucket by (listing_type, price-rounded, bedrooms) to keep comparisons
    # cheap, then compare within buckets and adjacent price buckets.
    buckets: dict[tuple, list] = {}
    for r in rows:
        pr = round(r["price"] / 50) if r["price"] else -1
        key = (r["listing_type"], r["bedrooms"], pr)
        buckets.setdefault(key, []).append(r)

    keys = list(buckets)
    for (lt, bd, pr) in keys:
        candidates = list(buckets[(lt, bd, pr)])
        for adj in (pr - 1, pr + 1):
            candidates += buckets.get((lt, bd, adj), [])
        n = len(candidates)
        for i in range(n):
            for j in range(i + 1, n):
                if candidates[i]["uid"] == candidates[j]["uid"]:
                    continue
                if _maybe_duplicate(candidates[i], candidates[j]):
                    uf.union(candidates[i]["uid"], candidates[j]["uid"])

    # Assign group ids and choose a primary per group.
    members: dict[str, list] = {}
    by_uid = {r["uid"]: r for r in rows}
    for r in rows:
        g = uf.find(r["uid"])
        members.setdefault(g, []).append(r["uid"])

    groups: dict[str, str] = {}
    primaries: set[str] = set()
    collapsed = 0

    def score(uid):
        r = by_uid[uid]
        s = 0
        if r["image_url"]:
            s += 3
        if r["sqft"]:
            s += 2
        if r["lat"] is not None:
            s += 2
        s += len(r["description"] or "") / 500.0
        # Prefer richer sources slightly.
        s += {"zumper": 1.5, "rentals_ca": 1.5, "craigslist": 1.0}.get(r["source"], 0)
        if r["status"] == "favorite":
            s += 100  # never hide something she favorited
        return s

    for gid, uids in members.items():
        primary = max(uids, key=score)
        primaries.add(primary)
        if len(uids) > 1:
            collapsed += len(uids) - 1
        for u in uids:
            groups[u] = gid

    db.set_dedup(groups, primaries)
    return collapsed
