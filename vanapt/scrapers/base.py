"""Shared HTTP fetching with browser-like headers + gzip handling."""
from __future__ import annotations

import gzip
import io
import urllib.error
import urllib.request

from .. import config


def fetch(url: str, headers: dict | None = None, data: bytes | None = None,
          timeout: int | None = None) -> str:
    """GET (or POST if data given) a URL, returning decoded text. Raises on
    network/HTTP error so the caller can mark the source as failed."""
    h = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en;q=0.9",
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout or config.HTTP_TIMEOUT) as resp:
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
