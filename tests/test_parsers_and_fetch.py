from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.normalize import normalize_name
from app.scrapers.base import FetchError, ParseError, fetch_text
from app.scrapers.books import parse_listing as parse_books
from app.scrapers.mockshop import MOCK_SHOPS
from app.scrapers.mockshop import parse_listing as parse_mock
from mock_shop.catalog import SHOPS, build_products, carries, product_name

FIXTURES = Path(__file__).parent / "fixtures"


def test_mockshop_listing_parses_prices_stock_and_skips_broken_cards():
    html = (FIXTURES / "mockshop_page.html").read_text(encoding="utf-8")
    page = parse_mock(html, "http://shop.test/shop/fjordtech/?page=1", "SEK")

    assert page.total_pages == 28
    assert [i.external_id for i in page.items] == ["FJT00001", "FJT00002"]  # 3rd card has no price
    first, second = page.items
    assert first.price == Decimal("1299.00") and first.currency == "SEK" and first.in_stock
    assert second.price == Decimal("349.50") and not second.in_stock
    assert first.url == "http://shop.test/shop/fjordtech/p/FJT00001"


def test_mockshop_layout_change_is_reported_not_silently_empty():
    with pytest.raises(ParseError):
        parse_mock("<html><body><div>redesigned!</div></body></html>", "http://x/", "SEK")


def test_books_listing_parses_gbp_prices_and_availability():
    html = (FIXTURES / "books_page.html").read_text(encoding="utf-8")
    page = parse_books(html, "https://books.toscrape.com/catalogue/page-1.html")

    assert page.total_pages == 50
    a, b = page.items
    assert (a.external_id, a.name, a.price, a.in_stock) == (
        "a-light-in-the-attic_1000",
        "A Light in the Attic",  # full title from the attribute, not the truncated link text
        Decimal("51.77"),
        True,
    )
    assert b.price == Decimal("1053.74") and b.in_stock is False
    assert a.url == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"


def test_mock_shop_definitions_match_the_app_side_list():
    assert {(s.slug, s.name, s.currency) for s in SHOPS.values()} == set(MOCK_SHOPS)


def test_every_shop_names_the_same_product_so_that_it_normalises_to_one_key():
    for product in build_products(150):
        shops = [s for s in SHOPS.values() if carries(s, product)]
        keys = {normalize_name(product_name(s, product)) for s in shops}
        assert len(keys) == 1, (product, keys)


# --- fetch_text: retry / backoff ---------------------------------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _no_sleep(_: float) -> None:
    return None


async def test_fetch_retries_5xx_then_succeeds():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, text="ok")

    async with _client(handler) as client:
        assert await fetch_text(client, "http://x/", retries=3, sleep=_no_sleep) == "ok"
    assert len(calls) == 3


async def test_fetch_retries_timeouts():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="late but fine")

    async with _client(handler) as client:
        assert await fetch_text(client, "http://x/", sleep=_no_sleep) == "late but fine"


async def test_fetch_gives_up_after_retries_and_does_not_retry_404():
    calls = []

    def always_503(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    async with _client(always_503) as client:
        with pytest.raises(FetchError, match="4 attempts"):
            await fetch_text(client, "http://x/", retries=3, sleep=_no_sleep)
    assert len(calls) == 4

    calls.clear()

    def not_found(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    async with _client(not_found) as client:
        with pytest.raises(FetchError, match="404"):
            await fetch_text(client, "http://x/", retries=3, sleep=_no_sleep)
    assert len(calls) == 1
