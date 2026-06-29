"""Kijiji Vancouver rentals (best-effort).

Kijiji is a Next.js app behind occasional bot protection. We fetch the
apartments/condos category pages for Vancouver + Burnaby and extract listings
from the embedded JSON. Marked best-effort: if Kijiji changes its data shape or
blocks the IP, this source simply returns nothing and the app keeps working.
"""
from __future__ import annotations

import re

from ..geo import classify_area
from ..models import (Listing, parse_bedrooms, parse_price, parse_sqft,
                      parse_available_date, looks_like_room_share)
from .base import fetch
from .jsonwalk import (embedded_json_blobs, find_listings, extract_field,
                       _num, normalize_rent, PRICE_KEYS, TITLE_KEYS, URL_KEYS,
                       LAT_KEYS, LNG_KEYS, BED_KEYS, SQFT_KEYS)

# Apartments/Condos for rent. l1700287 = Vancouver, l1700281 = Burnaby.
# (cat, base-url-without-page). Paginated with /page-N/ inserted before the cN..
BASES = [
    ("unit", "https://www.kijiji.ca/b-apartments-condos/vancouver", "c37l1700287"),
    ("unit", "https://www.kijiji.ca/b-apartments-condos/burnaby", "c37l1700281"),
    ("room_share", "https://www.kijiji.ca/b-room-rental-roommate/vancouver", "c36l1700287"),
    ("room_share", "https://www.kijiji.ca/b-room-rental-roommate/burnaby", "c36l1700281"),
]
MAX_PAGES = 3


def _to_listing(d: dict, lt: str) -> Listing | None:
    url = extract_field(d, URL_KEYS)
    if isinstance(url, str) and url.startswith("/"):
        url = "https://www.kijiji.ca" + url
    if not isinstance(url, str) or "kijiji.ca" not in url:
        # Still accept it if there's an id we can build a link from.
        _id = d.get("id") or d.get("listingId") or d.get("adId")
        if _id:
            url = f"https://www.kijiji.ca/v-apartments-condos/{_id}"
        else:
            return None

    title = extract_field(d, TITLE_KEYS) or ""
    if isinstance(title, dict):
        title = title.get("value") or title.get("en") or ""
    price = normalize_rent(extract_field(d, PRICE_KEYS))  # Kijiji stores cents
    desc = d.get("description") or ""
    if isinstance(desc, dict):
        desc = desc.get("value") or ""

    lat = _num(extract_field(d, LAT_KEYS))
    lng = _num(extract_field(d, LNG_KEYS))
    beds = _num(extract_field(d, BED_KEYS))
    sqft = _num(extract_field(d, SQFT_KEYS))
    addr = d.get("address") or d.get("location") or ""
    if isinstance(addr, dict):
        addr = addr.get("address") or addr.get("name") or ""

    blob = f"{title} {desc} {addr}"
    # Structured beds is often a building-wide 0/min; prefer a count parsed from
    # the title when the title clearly states one.
    parsed_beds = parse_bedrooms(blob)
    if beds is None or (beds == 0 and parsed_beds):
        beds = parsed_beds
    if price is None:
        price = parse_price(blob)
    src_id = re.search(r"/(\d+)$", url)
    if lt == "unit" and looks_like_room_share(str(title), str(desc)):
        lt = "room_share"

    li = Listing(
        source="kijiji",
        source_id=src_id.group(1) if src_id else url,
        url=url,
        title=str(title)[:300],
        description=str(desc)[:2000],
        price=price,
        bedrooms=beds,
        sqft=int(sqft) if sqft and 50 < sqft < 100000 else parse_sqft(blob),
        listing_type=lt,
        address=str(addr)[:200],
        lat=lat,
        lng=lng,
        available_date=parse_available_date(f"{title} {desc}"),
    )
    li.area = classify_area(lat, lng, str(addr), str(title), str(desc))
    return li


def scrape() -> list[Listing]:
    out: dict[str, Listing] = {}
    ok = False
    for lt, base, cat in BASES:
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}/{cat}" if page == 1 else f"{base}/page-{page}/{cat}"
            try:
                html = fetch(url)
                ok = True
            except Exception:
                break
            before = len(out)
            for blob in embedded_json_blobs(html):
                for d in find_listings(blob):
                    li = _to_listing(d, lt)
                    if li and li.price:
                        out[li.url] = li
            if len(out) == before and page > 1:
                break  # page added nothing new -> stop paging this category
    if not ok:
        raise RuntimeError("kijiji: all category pages failed to load")
    return list(out.values())
