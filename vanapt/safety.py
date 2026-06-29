"""Heuristic 'safety score' for a listing (0-100).

This is NOT a crime statistic. It's a transparent, explainable vetting score
built from signals we actually have in a listing, aimed at the two worries a
young renter (and her parent) raised: sketchy *shared-room* situations and
unsafe *locations* — plus the rental-scam red flags that target students.

Every deduction/bonus produces a short human-readable reason, returned in
`flags`, so the UI can show *why* a place scored the way it did. Silence is
never treated as a negative: a missing signal just doesn't move the score.

Buckets (each reason is tagged so the card can colour it):
  • scam      red flags in the post text (wire transfer, sight-unseen, etc.)
  • room      shared-with-strangers risk (the explicit concern)
  • location  proximity to the Downtown Eastside, Vancouver's known high-risk core
  • price     unusually-low price (classic bait)
  • info      missing photos / location (lower transparency)
  • plus      positives that add confidence (women-only household, real address)
"""
from __future__ import annotations

import json
import math
import os
import re

# Downtown Eastside core ≈ Main & Hastings. Used as the *fallback* location
# signal (Burnaby / un-geocoded listings), where real crime data isn't applied.
_DTES = (49.2815, -123.0997)

# Block-level crime grid (East Vancouver), precomputed offline from VPD GeoDASH
# open data by scripts/build_crime_grid.py. Each cell stores a 0-100 crime
# percentile (100 = worst East Van block). Loaded once; absence -> graceful
# fallback to the DTES-distance model below.
_GRID_PATH = os.path.join(os.path.dirname(__file__), "crime_grid.json")


def _load_grid():
    try:
        with open(_GRID_PATH, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return {}, None, None, ""
    meta = payload.get("meta", {})
    yrs = meta.get("years") or []
    label = ""
    if yrs:
        label = yrs[0] if yrs[0] == yrs[-1] else f"{yrs[0]}–{yrs[-1][-2:]}"
    return payload.get("grid", {}), meta.get("dlat"), meta.get("dlng"), label


_GRID, _GDLAT, _GDLNG, _YEARS_LABEL = _load_grid()


def _block_pct(lat, lng):
    """Crime percentile (0-100, 100 = worst) for a listing's ~150m block,
    smoothed over the cell + its 8 neighbours (centre weighted double).
    Returns None when the point is outside the gridded area (e.g. Burnaby)."""
    if not _GRID or _GDLAT is None or lat is None or lng is None:
        return None
    i = math.floor(lat / _GDLAT)
    j = math.floor(lng / _GDLNG)
    tot = wsum = 0.0
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            v = _GRID.get(f"{i + di},{j + dj}")
            if v is None:
                continue
            wt = 2.0 if (di == 0 and dj == 0) else 1.0
            tot += v * wt
            wsum += wt
    return (tot / wsum) if wsum else None

# ---- scam / fraud red flags (strongest, most actionable signal) -----------
_SCAM_PATTERNS = [
    (re.compile(r"\b(western union|moneygram|money gram|wire transfer|wire the|"
                r"money order|cashier'?s? che(que|ck)|bitcoin|crypto|gift card)\b", re.I),
     "Asks for wire/untraceable payment"),
    (re.compile(r"\b(without (viewing|seeing)|sight unseen|no (viewing|showing|"
                r"in[\s-]?person)|can'?t show|cannot show|won'?t be able to show|"
                r"before (you )?(view|see))\b", re.I),
     "Wants money before a viewing"),
    (re.compile(r"\b(out of (the )?(country|town|state|province)|abroad|overseas|"
                r"moved away|relocat(ed|ing) (abroad|out))\b", re.I),
     "Owner claims to be away / out of country"),
    (re.compile(r"\b(ship|mail|courier|send|fedex|ups)[^.\n]{0,20}\bkeys?\b", re.I),
     "Offers to mail the keys"),
    (re.compile(r"\b(god[\s-]?fearing|god bless|missionary|the lord)\b", re.I),
     "Classic scam-script wording"),
    (re.compile(r"\b(deposit|first month|e[\s-]?transfer)[^.\n]{0,25}"
                r"(before|prior|to (hold|reserve|secure)|asap|immediately)\b", re.I),
     "Pushes a deposit to 'hold' it"),
]

# ---- shared-room signals --------------------------------------------------
_FEMALE_ONLY = re.compile(
    r"\b(female|woman|women|girl)s?\b[^.\n]{0,18}\b(only|preferred|pref|household|"
    r"roommate|tenant)\b|\b(only|prefer(red)?)\b[^.\n]{0,12}\b(female|woman|women|girl)s?\b",
    re.I)
_CO_ED = re.compile(r"\b(co[\s-]?ed|mixed (house|household|gender)|males? and females?)\b", re.I)

# ---- location text fallback (used only when lat/lng is missing) -----------
# Deliberately narrow: only terms that specifically denote the DTES core, NOT
# broad east-side hoods like Hastings-Sunrise which are perfectly ordinary.
_DTES_TEXT = re.compile(
    r"\b(downtown eastside|d\.?t\.?e\.?s\.?|oppenheimer|main (and|&|/) hastings|"
    r"hastings (and|&|/) main|east hastings|gastown)\b", re.I)


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _price_floor(bedrooms) -> int:
    """Below this monthly rent a *whole unit* is suspiciously cheap for the
    East Van / Burnaby market — a common bait tactic. Tuned low to flag only
    the implausible, not the merely good deal."""
    if bedrooms is None:
        return 800
    if bedrooms <= 0:   # studio / bachelor
        return 800
    if bedrooms == 1:
        return 950
    return 1200          # 2BR+


def safety_score(row: dict) -> dict:
    """row: a listing dict/Row (title, description, price, bedrooms,
    listing_type, lat, lng, neighborhood, address, image_url).
    Returns {'score': int 0-100, 'label': str, 'flags': [{text, kind}]}."""
    title = (row.get("title") or "")
    desc = (row.get("description") or "")
    text = f"{title}\n{desc}"
    score = 100
    flags: list[dict] = []

    def hit(delta, text_, kind):
        nonlocal score
        score += delta
        flags.append({"text": text_, "kind": kind})

    # 1) Scam red flags — strongest signal, capped so one post can't go below 0
    #    on text alone but several stack heavily.
    scam_loss = 0
    for rx, label in _SCAM_PATTERNS:
        if rx.search(text):
            scam_loss += 28
            flags.append({"text": label, "kind": "scam"})
    score -= min(scam_loss, 64)

    # 2) Shared-room-with-strangers risk (the explicit worry)
    if row.get("listing_type") == "room_share":
        hit(-18, "Shared room / roommate situation — meet them first", "room")
        if _FEMALE_ONLY.search(text):
            hit(+8, "Women-only household", "plus")
        elif _CO_ED.search(text):
            hit(-4, "Co-ed / mixed household", "room")

    # 3) Location risk. Prefer real VPD block-level crime data (East Van);
    #    fall back to Downtown-Eastside distance where we have no grid coverage
    #    (Burnaby, or un-geocoded listings).
    lat, lng = row.get("lat"), row.get("lng")
    pct = _block_pct(lat, lng)
    yr = f" (VPD {_YEARS_LABEL})" if _YEARS_LABEL else ""
    if pct is not None:
        if pct >= 95:
            hit(-34, f"Crime hotspot — worst ~5% of East Van blocks{yr}", "location")
        elif pct >= 85:
            hit(-24, f"Higher-crime block — top 15% nearby{yr}", "location")
        elif pct >= 70:
            hit(-13, f"Above-average reported crime nearby{yr}", "location")
        elif pct >= 50:
            hit(-5, "About the East Van median for reported crime", "location")
        elif pct < 25:
            hit(+4, f"Quiet block — low reported crime{yr}", "plus")
    elif lat is not None and lng is not None:
        d = _haversine_km(lat, lng, *_DTES)
        if d < 0.7:
            hit(-30, "In / right beside the Downtown Eastside", "location")
        elif d < 1.3:
            hit(-16, "On the edge of the Downtown Eastside", "location")
        elif d < 2.2:
            hit(-7, "Within ~2 km of the Downtown Eastside", "location")
    elif _DTES_TEXT.search(f"{title} {row.get('neighborhood','')} {row.get('address','')}"):
        hit(-20, "Listing mentions the Downtown Eastside area", "location")

    # 4) Unusually low price (bait) — whole units only; cheap rooms are normal
    price = row.get("price")
    if (row.get("listing_type") != "room_share" and price
            and price < _price_floor(row.get("bedrooms"))):
        hit(-15, "Unusually low rent — verify it's genuine", "price")

    # 5) Transparency: photos & a real location raise confidence
    if not (row.get("image_url") or "").strip():
        hit(-6, "No photos in the post", "info")
    if (row.get("address") or "").strip():
        hit(+5, "Specific address given", "plus")
    elif not (row.get("neighborhood") or "").strip():
        hit(-5, "No location given", "info")

    score = max(0, min(100, score))
    if score >= 80:
        label = "Looks fine"
    elif score >= 60:
        label = "Some caution"
    else:
        label = "Caution"
    return {"score": score, "label": label, "flags": flags,
            # smoothed VPD crime percentile (0-100, 100 = worst) or None when
            # outside the gridded area; persisted so the UI can filter/colour.
            "block_pct": None if pct is None else round(pct)}
