import httpx

async def get_exchange_rate(from_curr: str, to_curr: str, date_str: str) -> float:
    """
    Fetch the exchange rate for a specific date using Frankfurter API.
    date_str: YYYY-MM-DD
    """
    # Frankfurter API format
    url = f"https://api.frankfurter.dev/v1/{date_str}?base={from_curr}&symbols={to_curr}"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            return data["rates"][to_curr]
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                 # Date might be today/weekend/future. Fallback to latest.
                 print(f"Frankfurter 404 for {date_str}, trying without date (latest)")
                 return await get_latest_rate(from_curr, to_curr)
            raise e
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Currency conversion error ({from_curr}->{to_curr}): {repr(e)}")
            raise e

async def get_latest_rate(from_curr: str, to_curr: str) -> float:
    url = f"https://api.frankfurter.dev/v1/latest?base={from_curr}&symbols={to_curr}"
    async with httpx.AsyncClient() as client:
         response = await client.get(url)
         data = response.json()
         return data["rates"][to_curr]
