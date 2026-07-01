import pytest
import httpx
import respx
import asyncio
from unittest.mock import AsyncMock, patch

from bridge_app.services.currency import (
    get_exchange_rate,
    get_latest_rate,
    _fetch_with_retry
)

# Mock report function
async def mock_report(msg, percent=None):
    pass

@pytest.mark.asyncio
async def test_get_latest_rate_success():
    with respx.mock:
        respx.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR").mock(
            return_value=httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2024-05-15", "rates": {"EUR": 0.85}})
        )
        rate = await get_latest_rate("USD", "EUR")
        assert rate == 0.85

@pytest.mark.asyncio
async def test_get_exchange_rate_success():
    with respx.mock:
        respx.get("https://api.frankfurter.dev/v1/2024-05-15?base=USD&symbols=EUR").mock(
            return_value=httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2024-05-15", "rates": {"EUR": 0.85}})
        )
        rate = await get_exchange_rate("USD", "EUR", "2024-05-15")
        assert rate == 0.85

@pytest.mark.asyncio
async def test_get_exchange_rate_404_fallback():
    with respx.mock:
        # Mock the specific date to return 404
        respx.get("https://api.frankfurter.dev/v1/2024-05-18?base=USD&symbols=EUR").mock(
            return_value=httpx.Response(404, json={"message": "not found"})
        )

        # Mock the fallback latest rate
        respx.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR").mock(
            return_value=httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2024-05-17", "rates": {"EUR": 0.86}})
        )

        rate = await get_exchange_rate("USD", "EUR", "2024-05-18")
        assert rate == 0.86

@pytest.mark.asyncio
async def test_fetch_with_retry_success_after_failure():
    with respx.mock:
        # Route that fails twice (e.g., 500 or 429) then succeeds
        route = respx.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR")
        route.side_effect = [
            httpx.Response(500),
            httpx.Response(429),
            httpx.Response(200, json={"amount": 1.0, "base": "USD", "date": "2024-05-17", "rates": {"EUR": 0.87}})
        ]

        # Mock asyncio.sleep to avoid waiting during test
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            report_mock = AsyncMock()
            rate = await get_latest_rate("USD", "EUR", report_func=report_mock)

            assert rate == 0.87
            assert route.call_count == 3
            assert mock_sleep.call_count == 2
            assert report_mock.call_count == 2

@pytest.mark.asyncio
async def test_fetch_with_retry_max_attempts():
    with respx.mock:
        route = respx.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR")
        route.side_effect = [httpx.Response(500), httpx.Response(500), httpx.Response(500)]

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await get_latest_rate("USD", "EUR")

            assert exc_info.value.response.status_code == 500
            assert route.call_count == 3
            assert mock_sleep.call_count == 2

@pytest.mark.asyncio
async def test_fetch_with_retry_non_transient_400():
    with respx.mock:
        route = respx.get("https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR")
        # 400 is not 429 and < 500, so it shouldn't retry
        route.mock(return_value=httpx.Response(400, json={"message": "bad request"}))

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await get_latest_rate("USD", "EUR")

        assert exc_info.value.response.status_code == 400
        assert route.call_count == 1
