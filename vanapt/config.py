"""Central configuration: search defaults, source toggles, area definitions."""
from __future__ import annotations

# ---- Default filters (the UI can override these per request) --------------
DEFAULT_MAX_PRICE = 1500          # default budget shown in the UI (slide up to COLLECT_MAX_PRICE)
DEFAULT_TARGET_PRICE = 1500       # the "most likely" target
DEFAULT_MIN_BEDROOMS = 1
DEFAULT_MAX_BEDROOMS = 1
INCLUDE_ROOM_SHARES = True        # 2BR looking-for-roommate / room-in-shared

# Collection-time ceiling. We never store anything above this (keeps the DB
# focused on relevant inventory). UI filtering happens below this.
COLLECT_MAX_PRICE = 2200

# Craigslist price bands. RSS returns only the ~25 most-recent items per query,
# so we slice the price range into bands to widen coverage substantially.
CRAIGSLIST_PRICE_BANDS = [
    (0, 1100),
    (1100, 1300),
    (1300, 1450),
    (1450, 1600),
    (1600, 1750),
    (1750, 1900),
    (1900, COLLECT_MAX_PRICE),
]

# Which scrapers run on refresh. Disable any that get noisy/blocked.
ENABLED_SOURCES = {
    "craigslist": True,    # RSS - reliable backbone (apts + rooms + sublets)
    "kijiji": True,        # best-effort (Next.js embedded JSON)
    "zumper": True,        # best-effort (public JSON API; also covers PadMapper)
    "rentals_ca": True,    # best-effort (embedded JSON)
    # facebook is import-only (login-walled) - see /api/import
}

# Per-request network timeout (seconds)
HTTP_TIMEOUT = 20

# Polite, browser-like headers. Sites are far less likely to block a
# residential machine sending these than a bare urllib request.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---- Area definitions: East Vancouver & Burnaby ---------------------------
# Boundary Rd (~ -123.0235 lng) separates Vancouver from Burnaby.
# East Van = roughly east of Main/Ontario St (~ -123.10) to Boundary Rd.
EAST_VAN_BBOX = dict(min_lat=49.20, max_lat=49.298, min_lng=-123.108, max_lng=-123.0235)
BURNABY_BBOX = dict(min_lat=49.185, max_lat=49.298, min_lng=-123.0235, max_lng=-122.886)

EAST_VAN_HOODS = [
    "east van", "east vancouver", "eastside", "east side", "mount pleasant",
    "commercial drive", "commercial-drive", "the drive", "grandview",
    "woodland", "victoria-fraserview", "victoria fraserview", "fraserview",
    "kensington", "cedar cottage", "cedar-cottage", "renfrew", "collingwood",
    "killarney", "fraser", "main st", "main street", "strathcona", "hastings",
    "hastings-sunrise", "hastings sunrise", "nanaimo", "joyce", "knight",
    "fraserhood", "sunset", "trout lake", "clark", "kingsway", "rupert",
    "vancouver east", "van east", "e van", "e. van",
]
BURNABY_HOODS = [
    "burnaby", "metrotown", "brentwood", "lougheed", "edmonds", "highgate",
    "burquitlam", "central park", "deer lake", "capitol hill", "willingdon",
    "sperling", "holdom", "gilmore", "royal oak", "metropolis", "burnaby north",
    "burnaby south", "burnaby heights", "north burnaby", "south burnaby",
]
