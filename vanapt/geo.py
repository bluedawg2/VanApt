"""Classify a listing into East Van / Burnaby / other, by geo then keywords."""
from __future__ import annotations

import math
import re
from . import config

# Forward Sortation Areas (first 3 chars of a postal code) — a precise signal
# when a full street address is present but the neighborhood name isn't.
BURNABY_FSA = {"V5A", "V5B", "V5C", "V5E", "V5G", "V5H", "V5J"}
EAST_VAN_FSA = {"V5K", "V5L", "V5M", "V5N", "V5P", "V5R", "V5S", "V5T",
                "V5V", "V5W", "V5X", "V6A"}
# Any Canadian postal code (first 3 chars). BC codes start with "V".
_POSTAL_RE = re.compile(r"\b([A-Za-z]\d[A-Za-z])\s*\d[A-Za-z]\d\b")
# Out-of-region markers: other provinces (uppercase, as in addresses) and
# major non-Metro-Vancouver cities. Used to reject leaked national listings.
_OTHER_PROV_RE = re.compile(r"\b(QC|ON|AB|MB|SK|NS|NB|PE|NL|NT|YT|NU)\b")
_OTHER_CITIES = ("montreal", "montréal", "toronto", "calgary", "edmonton",
                 "ottawa", "winnipeg", "halifax", "laval", "gatineau",
                 "mississauga", "brampton", "saint-laurent", "quebec city",
                 "québec city")
# Metro Vancouver municipalities that are NOT our target and whose coordinates
# can fall inside the (necessarily rectangular) bounding boxes. An explicit
# name in the location text overrides the box.
_OTHER_METRO = ("new westminster", "new west", "surrey", "coquitlam",
                "port moody", "richmond", "delta", "ladner", "tsawwassen",
                "langley", "maple ridge", "pitt meadows", "white rock",
                "north vancouver", "west vancouver", "north van", "west van",
                "squamish", "abbotsford", "mission", "chilliwack", "anmore",
                "belcarra", "bowen island")


def _in_bbox(lat: float, lng: float, box: dict) -> bool:
    return (box["min_lat"] <= lat <= box["max_lat"]
            and box["min_lng"] <= lng <= box["max_lng"])


def classify_area(lat=None, lng=None, *text_parts) -> str:
    """Return 'east_van', 'burnaby', or 'other'.

    Priority: explicit other-city name (overrides box) -> lat/lng -> postal
    code -> hood keywords.
    """
    blob = " ".join(p for p in text_parts if p)
    low = blob.lower()

    # 0) Explicit competing-city name beats geometry. "Coquitlam" / "New
    #    Westminster" coordinates can land inside a bounding box, so reject by
    #    name first. ("Vancouver"/"Burnaby" are NOT in this list.)
    if any(c in low for c in _OTHER_METRO):
        return "other"
    if any(c in low for c in _OTHER_CITIES):
        return "other"

    # 1) Geo - most reliable when present.
    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
            if _in_bbox(lat, lng, config.EAST_VAN_BBOX):
                return "east_van"
            if _in_bbox(lat, lng, config.BURNABY_BBOX):
                return "burnaby"
            # Has coords but outside both boxes -> definitively other.
            return "other"
        except (TypeError, ValueError):
            pass

    # 2) Postal code - precise. A non-"V" code means it's not in BC at all.
    m = _POSTAL_RE.search(blob)
    if m:
        fsa = m.group(1).upper()
        if not fsa.startswith("V"):
            return "other"               # e.g. H4L = Quebec, M5V = Toronto
        if fsa in BURNABY_FSA:
            return "burnaby"
        if fsa in EAST_VAN_FSA:
            return "east_van"
        return "other"                   # other BC area (west side, Surrey, ...)

    # 3) Reject leaked out-of-region listings (no BC postal, but clear markers).
    if _OTHER_PROV_RE.search(blob) and not re.search(r"\bBC\b", blob):
        return "other"

    # 4) Keyword fallback (only when nothing above located it).
    if any(h in low for h in config.BURNABY_HOODS):
        return "burnaby"
    if any(h in low for h in config.EAST_VAN_HOODS):
        return "east_van"
    return "other"


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Distance in metres between two coordinates."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
