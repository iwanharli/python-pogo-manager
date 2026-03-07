"""Shared HTTP fetching with disk cache, retry, and polite headers."""

import json
import time
import hashlib
import logging
import pathlib

import requests

log = logging.getLogger("pogo-seeder")

# ── Persistent session ───────────────────────────────────────

session = requests.Session()
session.headers.update({
    "User-Agent": "pogo-seeder/1.0 (https://github.com/iwanharli/pogo)",
    "Accept": "application/json",
})


def cache_path(cache_dir: pathlib.Path, url: str) -> pathlib.Path:
    """Return a deterministic cache file path for a URL."""
    h = hashlib.md5(url.encode()).hexdigest()
    return cache_dir / f"{h}.json"


def fetch(url: str, cache_dir: pathlib.Path, *,
          use_cache: bool = True, timeout: int = 30,
          max_retries: int = 3) -> dict | list:
    """Fetch JSON with optional disk cache and polite retry."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cp = cache_path(cache_dir, url)

    if use_cache and cp.exists():
        log.debug("CACHE HIT  %s", url)
        return json.loads(cp.read_text())

    for attempt in range(1, max_retries + 1):
        try:
            log.info("  ↓ %s", url)
            resp = session.get(url, timeout=timeout)

            if resp.status_code == 429:
                wait = 2 ** attempt
                log.warning("  ⏳ Rate-limited (429), waiting %ds...", wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            cp.write_text(json.dumps(data, ensure_ascii=False))
            log.debug("CACHED     %s", url)
            time.sleep(0.3)
            return data

        except requests.exceptions.ConnectionError:
            wait = 2 ** attempt
            log.warning("  ⚠ Connection error (%d/%d), retrying in %ds...",
                        attempt, max_retries, wait)
            time.sleep(wait)

    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts")
