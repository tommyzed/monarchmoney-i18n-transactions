import asyncio
import time
import os
import io
from PIL import Image
from bridge_app.services.gemini import extract_transaction_data

async def monitor_loop():
    start = time.perf_counter()
    await asyncio.sleep(0.1)
    return time.perf_counter() - start - 0.1

async def main():
    img = Image.new('RGB', (4000, 3000), color = 'red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    image_bytes = img_byte_arr.getvalue()

    os.environ["GEMINI_API_KEY"] = "fake"
    import bridge_app.services.gemini as gemini_module

    class MockAioClient:
        class Models:
            async def generate_content(self, model, contents):
                class Response:
                    text = '{"date": "2024-01-01", "amount": 10.0, "currency": "USD", "merchant": "Test", "is_credit": false}'
                await asyncio.sleep(0.05)
                return Response()
        models = Models()

    class MockClient:
        aio = MockAioClient()

    class MockGenai:
        def Client(self, api_key=None):
            return MockClient()

    gemini_module.genai = MockGenai()

    print("Benchmarking extract_transaction_data (with asyncio.to_thread for Image.open)")
    start = time.perf_counter()
    monitor_task = asyncio.create_task(monitor_loop())

    tasks = []
    for _ in range(50):
        tasks.append(extract_transaction_data(image_bytes))
    await asyncio.gather(*tasks)

    lag = await monitor_task
    print(f"Async event loop lag with new implementation: {lag*1000:.2f}ms")

if __name__ == "__main__":
    asyncio.run(main())
