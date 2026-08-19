import asyncio
import time
from bridge_app.services.currency import get_exchange_rate

async def main():
    start = time.time()
    for _ in range(10):
        await get_exchange_rate("USD", "EUR", "2023-01-05")
    end = time.time()
    print(f"Time taken: {end - start:.4f} seconds")

asyncio.run(main())
