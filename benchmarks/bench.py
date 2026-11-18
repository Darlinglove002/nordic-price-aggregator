"""Synchronous vs asyncio scraping of the same mock catalogue, same parsers.

    MOCK_PORT=8098 MOCK_LATENCY_MS=250 MOCK_FAIL_RATE=0 python -m mock_shop.server
    python -m benchmarks.bench --url http://localhost:8098

Both variants fetch every page of the four mock shops and parse it with the very same
``parse_listing``; only the I/O strategy differs. Latency is simulated by the mock server,
so absolute numbers say nothing about real sites - the *ratio* is the point.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx

from app.scrapers.mockshop import MOCK_SHOPS, MockShopScraper, parse_listing


def run_sync(base_url: str) -> int:
    """Classic blocking loop: one request at a time."""
    total = 0
    with httpx.Client(timeout=10) as client:
        for slug, _name, currency in MOCK_SHOPS:
            url = f"{base_url}/shop/{slug}/"
            page = 1
            total_pages = 1
            while page <= total_pages:
                response = client.get(url, params={"page": page})
                response.raise_for_status()
                parsed = parse_listing(response.text, str(response.url), currency)
                total_pages = parsed.total_pages
                total += len(parsed.items)
                page += 1
    return total


async def run_async(base_url: str, concurrency: int) -> int:
    async with httpx.AsyncClient(
        timeout=10, limits=httpx.Limits(max_connections=concurrency * len(MOCK_SHOPS) * 2)
    ) as client:
        scrapers = [
            MockShopScraper(
                client,
                base_url=base_url,
                slug=slug,
                name=name,
                currency=currency,
                concurrency=concurrency,
                retries=0,
            )
            for slug, name, currency in MOCK_SHOPS
        ]
        results = await asyncio.gather(*(s.scrape() for s in scrapers))
    assert all(r.pages_failed == 0 for r in results)
    return sum(len(r.items) for r in results)


def wait_for_server(base_url: str, attempts: int = 30) -> dict:
    for _ in range(attempts):
        try:
            return httpx.get(f"{base_url}/healthz", timeout=2).json()
        except httpx.TransportError:
            time.sleep(1)
    raise SystemExit(f"mock shop at {base_url} is not reachable")


def timed(fn, *args) -> tuple[float, int]:
    start = time.perf_counter()
    count = fn(*args)
    return time.perf_counter() - start, count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8098")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    health = wait_for_server(args.url)
    print(f"catalogue: {health['products']} products across {len(MOCK_SHOPS)} shops\n")

    run_sync(args.url)  # warm-up: fills the mock's page cache so no variant pays for it
    rows: list[tuple[str, float, int]] = []
    runs = [("sync (httpx.Client, sequential)", lambda: timed(run_sync, args.url))]
    for c in (1, 5, 10, 20):
        runs.append(
            (
                f"async, {c} concurrent / shop",
                lambda c=c: timed(lambda: asyncio.run(run_async(args.url, c))),
            )
        )

    for label, runner in runs:
        times, counts = [], set()
        for _ in range(args.repeats):
            seconds, count = runner()
            times.append(seconds)
            counts.add(count)
        assert len(counts) == 1, f"inconsistent item counts: {counts}"
        rows.append((label, statistics.median(times), counts.pop()))

    baseline = rows[0][1]
    print(f"{'variant':36} {'median s':>9} {'items':>6} {'speed-up':>9}")
    for label, seconds, count in rows:
        print(f"{label:36} {seconds:9.2f} {count:6d} {baseline / seconds:8.1f}x")


if __name__ == "__main__":
    main()
