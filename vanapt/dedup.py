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


def _maybe_duplicate(r1, r2) -> bool:
    # Never merge a whole-unit listing with a room-share.
    if r1["listing_type"] != r2["listing_type"]:
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

    # Title similarity as the general signal.
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
