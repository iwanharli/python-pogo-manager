"""Async HTTP prefetcher — bulk-download URLs concurrently using aiohttp.

Usage:
    from lib.http_async import prefetch_all
    results = prefetch_all(urls, cache_dir, concurrency=20)
    # results = {url: data_dict, ...}
"""

import json
import asyncio
import hashlib
import logging
import pathlib

import aiohttp

log = logging.getLogger("pogo-seeder")

_HEADERS = {
    "User-Agent": "pogo-seeder/1.0 (https://github.com/iwanharli/pogo)",
    "Accept": "application/json",
}


def _cache_path(cache_dir: pathlib.Path, url: str) -> pathlib.Path:
    h = hashlib.md5(url.encode()).hexdigest()
    return cache_dir / f"{h}.json"


async def _fetch_one(session: aiohttp.ClientSession, url: str,
                     cache_dir: pathlib.Path, sem: asyncio.Semaphore,
                     max_retries: int = 3) -> tuple[str, dict | None]:
    """Fetch a single URL with cache check and retry."""
    cp = _cache_path(cache_dir, url)
    if cp.exists():
        data = json.loads(cp.read_text())
        return url, data

    async with sem:
        for attempt in range(1, max_retries + 1):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        wait = 2 ** attempt
                        log.debug("  ⏳ 429 rate-limited, waiting %ds (%s)", wait, url)
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    data = await resp.json()
                    cp.write_text(json.dumps(data, ensure_ascii=False))
                    return url, data

            except (aiohttp.ClientError, asyncio.TimeoutError):
                wait = 2 ** attempt
                log.debug("  ⚠ Error (%d/%d) for %s, retrying in %ds...",
                          attempt, max_retries, url, wait)
                await asyncio.sleep(wait)

        log.warning("  ❌ Failed to fetch %s after %d attempts", url, max_retries)
        return url, None


async def _prefetch(urls: list[str], cache_dir: pathlib.Path,
                    concurrency: int = 20) -> dict:
    """Fetch multiple URLs concurrently. Returns {url: data}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession(headers=_HEADERS) as session:
        tasks = [_fetch_one(session, url, cache_dir, sem) for url in urls]
        results = await asyncio.gather(*tasks)

    return {url: data for url, data in results if data is not None}


def prefetch_all(urls: list[str], cache_dir: pathlib.Path,
                 concurrency: int = 20) -> dict:
    """Synchronous wrapper: fetch all URLs concurrently via asyncio.

    Returns {url: data_dict}.
    """
    return asyncio.run(_prefetch(urls, cache_dir, concurrency))
