"""Auto-discover free proxies from public APIs to bypass geo-restrictions."""

import asyncio
import logging
import random

import aiohttp

logger = logging.getLogger("scalper")

# Public free proxy APIs — return proxies in various formats
_PROXY_SOURCES = [
    # ProxyScrape: HTTP proxies from non-US countries
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=DE,NL,SG,JP,KR,GB,FR,CH,AT,SE,FI,HK,AU&anonymity=elite,anonymous",
    # Free proxy list API
    "https://www.proxy-list.download/api/v1/get?type=https&country=DE",
    "https://www.proxy-list.download/api/v1/get?type=https&country=NL",
    "https://www.proxy-list.download/api/v1/get?type=https&country=SG",
    "https://www.proxy-list.download/api/v1/get?type=https&country=FR",
]

# Binance test URL (lightweight, just checks if we can reach the API)
_BINANCE_TEST_URL = "https://fapi.binance.com/fapi/v1/ping"


async def _fetch_proxy_list(session: aiohttp.ClientSession, url: str) -> list[str]:
    """Fetch proxy list from a single source."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                text = await resp.text()
                proxies = []
                for line in text.strip().split("\n"):
                    line = line.strip()
                    if line and ":" in line:
                        # Ensure http:// prefix
                        if not line.startswith("http"):
                            line = f"http://{line}"
                        proxies.append(line)
                return proxies
    except Exception as e:
        logger.debug(f"Failed to fetch proxies from {url[:50]}: {e}")
    return []


async def _test_proxy(proxy: str, timeout: int = 8) -> bool:
    """Test if a proxy can reach Binance futures API without geo-block."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _BINANCE_TEST_URL,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=timeout),
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    return True
                elif resp.status == 451:
                    return False  # Geo-blocked even through proxy
                else:
                    return False
    except Exception:
        return False


async def find_working_proxy(max_candidates: int = 30, max_workers: int = 10) -> str | None:
    """
    Fetch free proxies from multiple sources and test them against Binance.
    Returns the first working proxy URL, or None if none work.
    """
    logger.info("Auto-proxy: searching for free proxies to bypass geo-restriction...")

    all_proxies = []
    async with aiohttp.ClientSession() as session:
        # Fetch from all sources concurrently
        tasks = [_fetch_proxy_list(session, url) for url in _PROXY_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                all_proxies.extend(result)

    # Deduplicate and shuffle
    all_proxies = list(set(all_proxies))
    random.shuffle(all_proxies)

    if not all_proxies:
        logger.warning("Auto-proxy: no proxy candidates found from any source")
        return None

    logger.info(f"Auto-proxy: found {len(all_proxies)} candidates, testing top {min(max_candidates, len(all_proxies))}...")

    # Test proxies in parallel batches
    candidates = all_proxies[:max_candidates]

    for batch_start in range(0, len(candidates), max_workers):
        batch = candidates[batch_start:batch_start + max_workers]
        tasks = [_test_proxy(proxy) for proxy in batch]
        results = await asyncio.gather(*tasks)

        for proxy, works in zip(batch, results):
            if works:
                logger.info(f"Auto-proxy: found working proxy → {proxy}")
                return proxy

    logger.warning("Auto-proxy: no working proxy found among candidates")
    return None
