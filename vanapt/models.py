"""Listing model + parsing helpers + cross-source fingerprinting."""
from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Listing:
    source: str                       # craigslist | kijiji | zumper | ...
    source_id: str                    # original id on that site
    url: str
    title: str = ""
    description: str = ""
    price: Optional[int] = None       # CAD / month
    bedrooms: Optional[float] = None  # 0 = studio/bachelor
    bathrooms: Optional[float] = None
    sqft: Optional[int] = None
    listing_type: str = "unit"        # "unit" | "room_share"
    area: str = "other"               # east_van | burnaby | other
    neighborhood: str = ""
    address: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    image_url: str = ""
    available_date: str = ""
    posted_at: str = ""               # ISO-ish string from source
    contact: str = ""

    def uid(self) -> str:
        """Stable per-source identity (survives re-scrapes)."""
        raw = f"{self.source}:{self.source_id or self.url}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def dedup_fingerprint(self) -> str:
        """Coarse cross-source key for grouping likely-duplicate posts.

        Combines price + bedrooms + a normalized title token set. Geo/address
        similarity is handled separately in dedup.py for finer grouping.
        """
        beds = "x" if self.bedrooms is None else f"{self.bedrooms:g}"
        price = "x" if self.price is None else str(round(self.price / 25) * 25)
        toks = _title_tokens(self.title)[:6]
        raw = f"{price}|{beds}|{'-'.join(toks)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Text parsing helpers (shared across scrapers)
# ---------------------------------------------------------------------------
_STOP = {
    "the", "a", "an", "in", "for", "with", "and", "to", "of", "br", "bdrm",
    "bedroom", "bedrooms", "bed", "apartment", "apt", "suite", "rent", "rental",
    "ft2", "sqft", "sq", "ft", "available", "now", "new", "near", "at", "on",
}


def _title_tokens(title: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return [w for w in words if w not in _STOP and len(w) > 2]


def parse_price(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\$\s?([\d,]{3,})", text)
    if not m:
        m = re.search(r"\b([\d,]{3,})\s*(?:/\s*mo|per month|monthly|cad)", text, re.I)
    if not m:
        return None
    try:
        val = int(m.group(1).replace(",", ""))
        return val if 200 <= val <= 20000 else None
    except ValueError:
        return None


def parse_bedrooms(text: str) -> Optional[float]:
    if not text:
        return None
    t = text.lower()
    if re.search(r"\b(studio|bachelor)\b", t):
        return 0.0
    m = re.search(r"(\d+(?:\.\d)?)\s*(?:br|bed|bdrm|bedroom)", t)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_sqft(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(\d{3,5})\s*(?:ft2|sqft|sq\.?\s*ft)", text.lower())
    if m:
        try:
            v = int(m.group(1))
            return v if 100 <= v <= 10000 else None
        except ValueError:
            return None
    return None


def parse_neighborhood(title: str) -> str:
    """Craigslist puts the hood in trailing parens: '... (East Vancouver)'."""
    if not title:
        return ""
    m = re.search(r"\(([^)]{2,40})\)\s*$", title.strip())
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Availability date parsing  ->  "" (unknown) | "now" | "YYYY-MM-DD"
# ---------------------------------------------------------------------------
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MON_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_NOW_RE = re.compile(
    r"\b(available|avail|move[\s-]?in|ready|vacant|occupancy)\b[^.;\n]{0,15}"
    r"\b(now|immediately|asap|today|right away)\b"
    r"|\b(immediate (occupancy|possession)|available immediately|move in today)\b",
    re.I)
# "available July 1", "from Aug. 1st 2026", "move-in september"
_CTX_RE = re.compile(
    r"(?:available|avail|from|starting|start|as of|effective|move[\s-]?in|ready|occupancy|date)\b"
    r"[^a-z0-9]{0,14}(?P<mon>" + _MON_ALT + r")\.?\s*(?P<day>\d{1,2})?(?:st|nd|rd|th)?"
    r"(?:[,/\s]+(?P<year>\d{4}))?", re.I)
_MD_RE = re.compile(  # "July 1, 2026" / "Aug 1st"
    r"\b(?P<mon>" + _MON_ALT + r")\.?\s*(?P<day>\d{1,2})(?:st|nd|rd|th)?"
    r"(?:[,/\s]+(?P<year>\d{4}))?", re.I)
_DM_RE = re.compile(  # "1st of August 2026"
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>" + _MON_ALT + r")"
    r"(?:[,/\s]+(?P<year>\d{4}))?", re.I)


def parse_available_date(text: str, today: datetime.date | None = None) -> str:
    """Extract a move-in date. Returns "now", an ISO date, or "" if unknown.
    A parsed date that's already in the past collapses to "now"."""
    if not text:
        return ""
    today = today or datetime.date.today()
    if _NOW_RE.search(text):
        return "now"
    for rx in (_CTX_RE, _MD_RE, _DM_RE):
        m = rx.search(text)
        if not m:
            continue
        mon = _MONTHS.get((m.group("mon") or "").lower().rstrip("."))
        if not mon:
            continue
        gd = m.groupdict()
        day = int(gd["day"]) if gd.get("day") else 1
        if not 1 <= day <= 31:
            day = 1
        year = int(gd["year"]) if gd.get("year") else (
            today.year if mon >= today.month else today.year + 1)
        try:
            d = datetime.date(year, mon, day)
        except ValueError:
            continue
        return "now" if d <= today else d.isoformat()
    return ""


# Below this many ft² a self-contained suite essentially doesn't exist in this
# market — it's a room in someone's home. Used as a backstop when the detail
# page's description can't be fetched.
ROOM_SQFT_MAX = 200

_ROOM_HINTS = re.compile(
    r"\b(roommate|room ?mate|room for rent|rooms? (for rent|available)"
    r"|furnished room|private room|room rental"
    r"|room in (a |an |the )?(house|home|apartment|suite|condo)"
    r"|shared (kitchen|bath|bathroom|washroom)"
    r"|share .{0,12}(apartment|place|suite|condo|house|kitchen)"
    r"|looking for a? ?(roommate|female|male|person))\b",
    re.I,
)


def looks_like_room_share(title: str, description: str = "") -> bool:
    return bool(_ROOM_HINTS.search(f"{title} {description}"))


# ---------------------------------------------------------------------------
# Amenity parsing  ->  flags pulled from the listing's title + description
# ---------------------------------------------------------------------------
_FURNISHED_RE = re.compile(r"\bfurnished\b", re.I)
_UNFURNISHED_RE = re.compile(r"\bunfurnished\b|\bnot furnished\b|\bun-furnished\b", re.I)
_PARKING_YES_RE = re.compile(
    r"\b(parking included|incl[^.]{0,8}parking|with parking|underground parking|"
    r"secure parking|covered parking|gated parking|\d+\s*parking|one parking|"
    r"parking (stall|spot|space|spaces|available|included)|garage|carport|"
    r"free parking|street parking included)\b", re.I)
_PARKING_NO_RE = re.compile(
    r"\bno parking\b|\bparking not included\b|\bstreet parking only\b|"
    r"\bno on[- ]?site parking\b", re.I)
_LAUNDRY_IN_RE = re.compile(
    r"\b(in[\s-]?suite laundry|in[\s-]?unit laundry|ensuite laundry|"
    r"laundry in[\s-]?(suite|unit)|own laundry|private laundry|"
    r"washer\s*/?\s*&?\s*dryer in|in[\s-]?suite washer|laundry in the (suite|unit))\b", re.I)
_LAUNDRY_SHARED_RE = re.compile(
    r"\b(shared laundry|common laundry|coin laundry|coin[\s-]?op|"
    r"laundry (room|on[\s-]?site|facilities)|on[\s-]?site laundry)\b", re.I)
_LEASE_MTM_RE = re.compile(
    r"\bmonth[\s-]?to[\s-]?month\b|\bmtm\b|\bflexible (lease|term|tenancy)\b|"
    r"\bshort[\s-]?term\b|\bno (lease|fixed term)\b", re.I)
_LEASE_FIXED_RE = re.compile(
    r"(?:(?:minimum|min|fixed|lease|term)[^.\n]{0,18}?(\d{1,2})\s*(?:month|mo)\b)"
    r"|(?:(\d{1,2})\s*(?:month|mo)\b[^.\n]{0,12}?(?:lease|term|minimum|min))", re.I)
_LEASE_YEAR_RE = re.compile(
    r"\b(1[\s-]?year|one[\s-]?year|12[\s-]?month|annual lease|yearly lease)\b", re.I)


def parse_amenities(title: str, description: str = "") -> dict:
    """Pull pet/parking/laundry/furnished/lease hints from listing text.

    Returns a dict with:
      furnished:  1 if explicitly furnished (0 = unknown / not stated)
      parking:    1 included, 0 explicitly none, None unknown
      laundry:    "in_suite" | "shared" | None
      lease_term: "" | "month-to-month" | "<n>-month" | "1-year"
    Only positive signals are recorded; silence means "unknown", never "no".
    """
    t = f"{title} {description}"
    furnished = 1 if (_FURNISHED_RE.search(t) and not _UNFURNISHED_RE.search(t)) else 0

    parking = None
    if _PARKING_NO_RE.search(t):
        parking = 0
    elif _PARKING_YES_RE.search(t):
        parking = 1

    laundry = None
    if _LAUNDRY_IN_RE.search(t):
        laundry = "in_suite"
    elif _LAUNDRY_SHARED_RE.search(t):
        laundry = "shared"

    lease_term = ""
    if _LEASE_MTM_RE.search(t):
        lease_term = "month-to-month"
    else:
        m = _LEASE_FIXED_RE.search(t)
        if m:
            n = m.group(1) or m.group(2)
            lease_term = f"{int(n)}-month"
        elif _LEASE_YEAR_RE.search(t):
            lease_term = "1-year"

    return {"furnished": furnished, "parking": parking,
            "laundry": laundry, "lease_term": lease_term}
