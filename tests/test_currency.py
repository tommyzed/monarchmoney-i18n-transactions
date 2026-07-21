import pytest
import respx
import httpx
from bridge_app.services.currency import get_exchange_rate, get_latest_rate

@pytest.mark.asyncio
@respx.mock
async def test_get_latest_rate_success():
    from_curr = "EUR"
    to_curr = "USD"
    url = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"

    mock_response = {
        "amount": 1.0,
        "base": "EUR",
        "date": "2026-11-20",
        "rates": {
            "USD": 1.085
        }
    }

    respx.get(url).mock(return_value=httpx.Response(200, json=mock_response))

    rate = await get_latest_rate(from_curr, to_curr)
    assert rate == 1.085

@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_success():
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"
    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"

    mock_response = {
        "amount": 1.0,
        "base": "EUR",
        "date": "2026-11-01",
        "rates": {
            "USD": 1.10
        }
    }

    respx.get(url).mock(return_value=httpx.Response(200, json=mock_response))

    rate = await get_exchange_rate(from_curr, to_curr, date_str)
    assert rate == 1.10

@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_fallback_on_404():
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"

    # URL for specific date
    date_url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"
    # URL for latest rate (fallback)
    latest_url = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"

    # Mock specific date endpoint to return 404
    respx.get(date_url).mock(return_value=httpx.Response(404))

    # Mock latest rate endpoint to return 200 with fallback value
    mock_latest_response = {
        "amount": 1.0,
        "base": "EUR",
        "date": "2026-11-20",
        "rates": {
            "USD": 1.085
        }
    }
    respx.get(latest_url).mock(return_value=httpx.Response(200, json=mock_latest_response))

    # This should trigger the fallback and return 1.085
    rate = await get_exchange_rate(from_curr, to_curr, date_str)
    assert rate == 1.085

@pytest.mark.asyncio
@respx.mock
async def test_get_exchange_rate_non_404_error():
    from_curr = "EUR"
    to_curr = "USD"
    date_str = "2026-11-01"
    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"

    # Mock date endpoint to return 500 Internal Server Error
    respx.get(url).mock(return_value=httpx.Response(500))

    with pytest.raises(httpx.HTTPStatusError):
        await get_exchange_rate(from_curr, to_curr, date_str)

@pytest.mark.asyncio
@respx.mock
async def test_get_latest_rate_retry_and_success(monkeypatch):
    from_curr = "EUR"
    to_curr = "USD"
    url = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"

    # Mock asyncio.sleep so we don't actually wait
    async def mock_sleep(delay):
        pass
    monkeypatch.setattr("asyncio.sleep", mock_sleep)

    # Record calls to the reporting function
    reported_messages = []
    async def mock_report(msg, percent):
        reported_messages.append((msg, percent))

    # Mocking the endpoint to return 500 once, then 200 with the rate
    route = respx.get(url)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={
            "amount": 1.0,
            "base": "EUR",
            "date": "2026-11-20",
            "rates": {
                "USD": 1.085
            }
        })
    ]

    rate = await get_latest_rate(from_curr, to_curr, report_func=mock_report)
    assert rate == 1.085
    assert len(reported_messages) == 1
    assert "ForEx API error" in reported_messages[0][0]
    assert reported_messages[0][1] == 62
