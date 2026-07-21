import asyncio
import httpx
import pytest
import respx
from unittest.mock import AsyncMock

from bridge_app.services.currency import get_exchange_rate, get_latest_rate, _fetch_with_retry

@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_success():
    # Mocking successful API call
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"
    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"

    respx.get(url).respond(
        status_code=200,
        json={"rates": {"USD": 1.10}}
    )

    rate = await get_exchange_rate(from_curr, to_curr, date_str)
    assert rate == 1.10


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_fallback_on_404(monkeypatch):
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"
    url_date = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"
    url_latest = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"

    # Mock 404 for specific date, and 200 for latest
    respx.get(url_date).respond(status_code=404)
    respx.get(url_latest).respond(
        status_code=200,
        json={"rates": {"USD": 1.12}}
    )

    # We can also mock report_func to verify it works or check if it doesn't fail
    report_func = AsyncMock()

    rate = await get_exchange_rate(from_curr, to_curr, date_str, report_func=report_func)
    assert rate == 1.12


@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_raises_on_non_404_error():
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"
    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"

    # A non-transient 400 error
    respx.get(url).respond(status_code=400)

    with pytest.raises(httpx.HTTPStatusError):
        await get_exchange_rate(from_curr, to_curr, date_str)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_with_retry_retries_and_fails_on_500(monkeypatch):
    # Mock asyncio.sleep to run immediately
    async def mock_sleep(delay):
        pass
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    url = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD"
    # Respond with 500 status code
    route = respx.get(url).respond(status_code=500)

    report_func = AsyncMock()

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_with_retry(url, max_attempts=3, initial_delay=0.1, report_func=report_func)

    # Ensure 3 attempts were made
    assert route.call_count == 3
    # Ensure report_func was called
    assert report_func.call_count == 2  # Called on failure of attempt 1 and 2, but not 3


@pytest.mark.asyncio
@respx.mock
async def test_fetch_with_retry_succeeds_on_retry(monkeypatch):
    async def mock_sleep(delay):
        pass
    monkeypatch.setattr(asyncio, "sleep", mock_sleep)

    url = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD"

    # First attempt fails, second succeeds
    route = respx.get(url)
    route.side_effect = [
        httpx.Response(status_code=500),
        httpx.Response(status_code=200, json={"rates": {"USD": 1.15}})
    ]

    response = await _fetch_with_retry(url, max_attempts=3, initial_delay=0.1)
    assert response.status_code == 200
    assert response.json()["rates"]["USD"] == 1.15
    assert route.call_count == 2
