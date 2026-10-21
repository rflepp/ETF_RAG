"""Polite HTTP client: browser-like UA, per-host throttling, retries."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class Client:
    def __init__(self, min_interval: float = 1.0, retries: int = 3, timeout: float = 60.0):
        self.min_interval = min_interval
        self.retries = retries
        self._last: dict[str, float] = {}
        self._http = httpx.Client(headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        err: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(url)
            try:
                r = self._http.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                err = e
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (403, 404):
                    break
                time.sleep(2 ** attempt * 2)
        raise RuntimeError(f"{method} {url} failed: {err}")

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post_json(self, url: str, payload: dict, **kw) -> httpx.Response:
        return self.request("POST", url, json=payload, **kw)

    def download(self, url: str, dest: Path, expect: str = "application/pdf") -> str:
        """Download to dest, return sha256. Refuses responses of the wrong type (e.g. HTML gate pages)."""
        r = self.get(url)
        ctype = r.headers.get("content-type", "")
        if expect not in ctype or not r.content.startswith(b"%PDF"):
            raise RuntimeError(f"{url}: expected {expect}, got {ctype!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return hashlib.sha256(r.content).hexdigest()
