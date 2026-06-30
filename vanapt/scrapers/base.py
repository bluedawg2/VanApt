"""Shared HTTP fetching with browser-like headers + gzip handling."""
from __future__ import annotations

import gzip
import io
import os
import urllib.error
import urllib.parse
import urllib.request

from .. import config


def _scraperapi_url(url: str) -> str | None:
    """If SCRAPERAPI_KEY is set, wrap `url` so the request goes through
    ScraperAPI's rotating residential IPs instead of ours. Returns None when no
    key is configured (caller then fetches directly). Extra ScraperAPI options
    (e.g. 'premium=true' for residential, 'render=true') can be appended via the
    SCRAPERAPI_PARAMS env var without a code change."""
    key = os.environ.get("SCRAPERAPI_KEY")
    if not key:
        return None
    target = ("https://api.scraperapi.com/?api_key=" + key
              + "&url=" + urllib.parse.quote(url, safe=""))
    extra = os.environ.get("SCRAPERAPI_PARAMS", "country_code=ca")
    if extra:
        target += "&" + extra
    return target


def fetch(url: str, headers: dict | None = None, data: bytes | None = None,
          timeout: int | None = None, proxy: bool = False) -> str:
    """GET (or POST if data given) a URL, returning decoded text. Raises on
    network/HTTP error so the caller can mark the source as failed.

    proxy=True routes the request through ScraperAPI when SCRAPERAPI_KEY is set
    (used for Craigslist, which hard-blocks our datacenter/residential IP); with
    no key it falls back to a direct fetch, so behaviour is unchanged locally."""
    target = url
    via_proxy = False
    if proxy:
        wrapped = _scraperapi_url(url)
        if wrapped:
            target, via_proxy = wrapped, True
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(target, data=data, headers=h,
                                 method="POST" if data else "GET")
    # ScraperAPI retries the target internally and can take up to ~60s, so a
    # short caller timeout (e.g. detail pages pass 8s) must not abort it early.
    eff_timeout = timeout or config.HTTP_TIMEOUT
    if via_proxy:
        eff_timeout = max(eff_timeout, config.SCRAPERAPI_TIMEOUT)
    with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        charset = resp.headers.get_content_charset()
        if charset:
            return raw.decode(charset, errors="replace")
        # No declared charset: prefer UTF-8, but fall back to cp1252 (which
        # Craigslist uses for smart quotes/apostrophes) instead of mangling them.
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")
