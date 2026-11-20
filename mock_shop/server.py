"""Tiny aiohttp server pretending to be four Nordic web shops.

    MOCK_PRODUCTS       size of the shared catalogue            (default 600)
    MOCK_LATENCY_MS     base latency of every shop page request (default 250)
    MOCK_FAIL_RATE      probability of a 503 per request        (default 0)
    MOCK_EPOCH_SECONDS  auto-advance prices/stock every N secs  (default 0 = manual)

Never expose this to the internet: /_admin/advance has no authentication.
"""

from __future__ import annotations

import asyncio
import os
import random
from collections.abc import AsyncIterator
from functools import lru_cache

from aiohttp import web

from mock_shop.catalog import SHOPS, build_products, render_page

LATENCY_MS = float(os.getenv("MOCK_LATENCY_MS", "250"))
FAIL_RATE = float(os.getenv("MOCK_FAIL_RATE", "0"))
EPOCH_SECONDS = float(os.getenv("MOCK_EPOCH_SECONDS", "0"))
PRODUCTS = build_products(int(os.getenv("MOCK_PRODUCTS", "600")))


@lru_cache(maxsize=8192)
def cached_page(slug: str, page: int, epoch: int) -> str | None:
    """Pages are pure functions of (shop, page, epoch): render once, so the mock's own CPU
    cost stays out of scraper benchmarks."""
    return render_page(SHOPS[slug], PRODUCTS, page, epoch)


async def shop_page(request: web.Request) -> web.Response:
    shop = SHOPS.get(request.match_info["slug"])
    if shop is None:
        raise web.HTTPNotFound(text="unknown shop")
    try:
        page = int(request.query.get("page", "1"))
    except ValueError:
        raise web.HTTPBadRequest(text="page must be an integer") from None

    await asyncio.sleep(max(0.0, LATENCY_MS * random.uniform(0.7, 1.3) / 1000))
    if FAIL_RATE and random.random() < FAIL_RATE:
        raise web.HTTPServiceUnavailable(text="mock overload")

    body = cached_page(shop.slug, page, request.app["epoch"])
    if body is None:
        raise web.HTTPNotFound(text="no such page")
    return web.Response(text=body, content_type="text/html")


async def advance(request: web.Request) -> web.Response:
    request.app["epoch"] += 1
    return web.json_response({"epoch": request.app["epoch"]})


async def state(request: web.Request) -> web.Response:
    return web.json_response({"epoch": request.app["epoch"], "products": len(PRODUCTS)})


async def _auto_advance(app: web.Application) -> AsyncIterator[None]:
    async def loop() -> None:
        while True:
            await asyncio.sleep(EPOCH_SECONDS)
            app["epoch"] += 1

    task = asyncio.create_task(loop()) if EPOCH_SECONDS > 0 else None
    yield
    if task:
        task.cancel()


def make_app() -> web.Application:
    app = web.Application()
    app["epoch"] = 0
    app.add_routes(
        [
            web.get("/shop/{slug}/", shop_page),
            web.get("/healthz", state),
            web.get("/_admin/state", state),
            web.post("/_admin/advance", advance),
        ]
    )
    app.cleanup_ctx.append(_auto_advance)
    return app


if __name__ == "__main__":
    web.run_app(
        make_app(),
        host=os.getenv("MOCK_HOST", "0.0.0.0"),  # noqa: S104
        port=int(os.getenv("MOCK_PORT", "8099")),
    )
