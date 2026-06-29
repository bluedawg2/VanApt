"""rentals.ca (best-effort).

rentals.ca is a Next.js app that embeds search results as JSON. We fetch the
Vancouver and Burnaby listing pages and extract via the generic JSON walker.
PadMapper/Zumper share a backend, so rentals.ca + zumper together approximate
that aggregator inventory. Best-effort: degrades to nothing if blocked.
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

PAGES = [
    "https://rentals.ca/vancouver/1-bedroom-apartments",
    "https://rentals.ca/vancouver/bachelor-studio-apartments",
    "https://rentals.ca/burnaby/1-bedroom-apartments",
    "https://rentals.ca/burnaby/bachelor-studio-apartments",
]


def _to_listing(d: dict) -> Listing | None:
    url = extract_field(d, URL_KEYS)
    if isinstance(url, str) and url.startswith("/"):
        url = "https://rentals.ca" + url
    if not isinstance(url, str) or "rentals.ca" not in url:
        return None

    title = extract_field(d, TITLE_KEYS) or ""
    price = _num(extract_field(d, PRICE_KEYS))
    desc = d.get("description") or d.get("summary") or ""
    addr = d.get("address") or d.get("location") or ""
    if isinstance(addr, dict):
        addr = " ".join(str(addr.get(k, "")) for k in ("street", "city")).strip() or \
               addr.get("name", "")
    lat = _num(extract_field(d, LAT_KEYS))
    lng = _num(extract_field(d, LNG_KEYS))
    beds = _num(extract_field(d, BED_KEYS))
    sqft = _num(extract_field(d, SQFT_KEYS))

    blob = f"{title} {desc} {addr}"
    li = Listing(
        source="rentals_ca",
        source_id=re.sub(r"\W+", "-", url)[-40:],
        url=url,
        title=str(title)[:300],
        description=str(desc)[:2000],
        price=normalize_rent(price) if price else parse_price(blob),
        bedrooms=beds if beds is not None else parse_bedrooms(blob),
        sqft=int(sqft) if sqft and sqft > 50 else parse_sqft(blob),
        listing_type="room_share" if looks_like_room_share(str(title), str(desc)) else "unit",
        address=str(addr)[:200],
        lat=lat,
        lng=lng,
        available_date=parse_available_date(blob),
    )
    li.area = classify_area(lat, lng, str(addr), str(title), str(desc))
    return li


def scrape() -> list[Listing]:
    out: dict[str, Listing] = {}
    ok = False
    for url in PAGES:
        try:
            html = fetch(url)
            ok = True
        except Exception:
            continue
        for blob in embedded_json_blobs(html):
            for d in find_listings(blob):
                li = _to_listing(d)
                if li and li.price:
                    out[li.url] = li
    if not ok:
        raise RuntimeError("rentals.ca: all pages failed to load")
    return list(out.values())
