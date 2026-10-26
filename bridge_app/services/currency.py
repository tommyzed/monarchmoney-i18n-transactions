import asyncio
from typing import Optional
import httpx
from cachetools import TTLCache

_FX_RATE_CACHE: TTLCache = TTLCache(maxsize=512, ttl=3600)
_http_client: Optional[httpx.AsyncClient] = None
_http_client_loop = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client, _http_client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _http_client is None or _http_client.is_closed or _http_client_loop is not current_loop:
        _http_client = httpx.AsyncClient(timeout=10.0)
        _http_client_loop = current_loop
    return _http_client


async def _fetch_with_retry(url: str, max_attempts: int = 3, initial_delay: float = 1.0, report_func=None) -> httpx.Response:
    """
    Fetch a URL with retries on transient errors (network errors, timeouts, HTTP 5xx, or HTTP 429).
    Does not retry on non-transient 4xx errors (like 404).
    """
    delay = initial_delay
    client = _get_http_client()
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            # Do not retry on non-transient 4xx errors
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
                if status_code < 500 and status_code != 429:
                    raise e
            
            if attempt == max_attempts:
                print(f"Frankfurter API call failed after {max_attempts} attempts: {repr(e)}")
                raise e
            
            # Server-side debug log
            print(f"[DEBUG] Frankfurter API call failed (attempt {attempt}/{max_attempts}): {repr(e)}. Retrying in {delay:.1f}s...")
            
            # Push status message to client UI
            if report_func:
                try:
                    await report_func(f"ForEx API error (attempt {attempt}/{max_attempts}). Retrying in {delay:.1f}s...", 62)
                except Exception as report_err:
                    print(f"Failed to report progress during retry: {report_err}")
            
            await asyncio.sleep(delay)
            delay *= 2

async def get_exchange_rate(from_curr: str, to_curr: str, date_str: str, report_func=None) -> float:
    """
    Fetch the exchange rate for a specific date using Frankfurter API.
    date_str: YYYY-MM-DD
    """
    cache_key = (from_curr.upper(), to_curr.upper(), date_str)
    if cache_key in _FX_RATE_CACHE:
        return _FX_RATE_CACHE[cache_key]

    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"
    
    try:
        response = await _fetch_with_retry(url, report_func=report_func)
        data = response.json()
        rate = data["rates"][to_curr]
        _FX_RATE_CACHE[cache_key] = rate
        return rate
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
             # Date might be today/weekend/future. Fallback to latest.
             print(f"Frankfurter 404 for {date_str}, trying without date (latest)")
             rate = await get_latest_rate(from_curr, to_curr, report_func=report_func)
             _FX_RATE_CACHE[cache_key] = rate
             return rate
        raise e
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Currency conversion error ({from_curr}->{to_curr}): {repr(e)}")
        raise e

async def get_latest_rate(from_curr: str, to_curr: str, report_func=None) -> float:
    cache_key = (from_curr.upper(), to_curr.upper(), "latest")
    if cache_key in _FX_RATE_CACHE:
        return _FX_RATE_CACHE[cache_key]

    url = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"
    response = await _fetch_with_retry(url, report_func=report_func)
    data = response.json()
    rate = data["rates"][to_curr]
    _FX_RATE_CACHE[cache_key] = rate
    return rate

