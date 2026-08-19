import asyncio
from google import genai

async def main():
    client = genai.Client(api_key="TEST")
    print(dir(client))
    print(hasattr(client, 'aio'))

asyncio.run(main())
