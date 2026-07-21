import pytest
import httpx
import respx
from bridge_app.services.currency import _fetch_with_retry, get_exchange_rate, get_latest_rate

@pytest.mark.asyncio
async def test_fetch_with_retry_success(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest"
    respx_mock.get(url).mock(return_value=httpx.Response(200, json={"rates": {"USD": 1.0}}))

    response = await _fetch_with_retry(url, max_attempts=3, initial_delay=0.001)
    assert response.status_code == 200
    assert response.json() == {"rates": {"USD": 1.0}}

@pytest.mark.asyncio
async def test_fetch_with_retry_transient_then_success(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest"
    # First attempt: 500
    # Second attempt: 429
    # Third attempt: 200
    route = respx_mock.get(url)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, json={"rates": {"USD": 1.1}})
    ]

    reports = []
    async def mock_report(msg, percent=None):
        reports.append((msg, percent))

    response = await _fetch_with_retry(url, max_attempts=3, initial_delay=0.001, report_func=mock_report)
    assert response.status_code == 200
    assert response.json() == {"rates": {"USD": 1.1}}
    assert len(reports) == 2
    assert "attempt 1/3" in reports[0][0]
    assert "attempt 2/3" in reports[1][0]
    assert reports[0][1] == 62

@pytest.mark.asyncio
async def test_fetch_with_retry_all_transient_failures(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest"
    respx_mock.get(url).mock(return_value=httpx.Response(500))

    reports = []
    async def mock_report(msg, percent=None):
        reports.append((msg, percent))

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch_with_retry(url, max_attempts=2, initial_delay=0.001, report_func=mock_report)

    # max_attempts=2 means 1 retry attempt before raising, so 1 report call
    assert len(reports) == 1
    assert "attempt 1/2" in reports[0][0]

@pytest.mark.asyncio
async def test_fetch_with_retry_non_transient_failure(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest"
    respx_mock.get(url).mock(return_value=httpx.Response(400))

    reports = []
    async def mock_report(msg, percent=None):
        reports.append((msg, percent))

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await _fetch_with_retry(url, max_attempts=3, initial_delay=0.001, report_func=mock_report)

    assert exc_info.value.response.status_code == 400
    # Non-transient should fail immediately with NO retries
    assert len(reports) == 0

@pytest.mark.asyncio
async def test_fetch_with_retry_request_error(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest"
    # Simulating a ConnectError (which is a RequestError)
    respx_mock.get(url).side_effect = [
        httpx.ConnectError("Connection failed"),
        httpx.Response(200, json={"rates": {"USD": 1.2}})
    ]

    reports = []
    async def mock_report(msg, percent=None):
        reports.append((msg, percent))

    response = await _fetch_with_retry(url, max_attempts=3, initial_delay=0.001, report_func=mock_report)
    assert response.status_code == 200
    assert response.json() == {"rates": {"USD": 1.2}}
    assert len(reports) == 1
    assert "attempt 1/3" in reports[0][0]

@pytest.mark.asyncio
async def test_get_exchange_rate_success(respx_mock):
    url = "https://api.frankfurter.dev/v1/2026-10-27?base=EUR&symbols=USD"
    respx_mock.get(url).mock(return_value=httpx.Response(200, json={"rates": {"USD": 1.09}}))

    rate = await get_exchange_rate("EUR", "USD", "2026-10-27")
    assert rate == 1.09

@pytest.mark.asyncio
async def test_get_exchange_rate_fallback_to_latest(respx_mock, mocker):
    date_url = "https://api.frankfurter.dev/v1/2026-10-27?base=EUR&symbols=USD"
    # Mock date_url returning 404
    respx_mock.get(date_url).mock(return_value=httpx.Response(404))

    # Spy or mock get_latest_rate
    latest_mock = mocker.patch("bridge_app.services.currency.get_latest_rate", return_value=1.12)

    rate = await get_exchange_rate("EUR", "USD", "2026-10-27")
    assert rate == 1.12
    latest_mock.assert_called_once_with("EUR", "USD", report_func=None)

@pytest.mark.asyncio
async def test_get_exchange_rate_exception_propagation(respx_mock):
    url = "https://api.frankfurter.dev/v1/2026-10-27?base=EUR&symbols=USD"
    # Non-transient 400 error should raise
    respx_mock.get(url).mock(return_value=httpx.Response(400))

    with pytest.raises(httpx.HTTPStatusError):
        await get_exchange_rate("EUR", "USD", "2026-10-27")

@pytest.mark.asyncio
async def test_get_latest_rate_success(respx_mock):
    url = "https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD"
    respx_mock.get(url).mock(return_value=httpx.Response(200, json={"rates": {"USD": 1.15}}))

    rate = await get_latest_rate("EUR", "USD")
    assert rate == 1.15

@pytest.mark.asyncio
async def test_fetch_with_retry_report_func_exception(respx_mock):
    # What happens if report_func itself raises an error?
    url = "https://api.frankfurter.dev/v1/latest"
    respx_mock.get(url).side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"rates": {"USD": 1.1}})
    ]

    async def bad_report(msg, percent=None):
        raise ValueError("Oops, report failed")

    # Should log the failure but still proceed and succeed
    response = await _fetch_with_retry(url, max_attempts=3, initial_delay=0.001, report_func=bad_report)
    assert response.status_code == 200
    assert response.json() == {"rates": {"USD": 1.1}}
