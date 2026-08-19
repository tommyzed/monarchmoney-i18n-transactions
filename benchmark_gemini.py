import asyncio
import time
import os
import io
from PIL import Image

def sync_open_image(image_bytes):
    # Simulating what gemini.py does
    img = Image.open(io.BytesIO(image_bytes))
    return img

async def main():
    # We want to measure event loop blocking.
    # If we run synchronous image.open, the event loop is blocked.
    # If we run it in a threadpool, the event loop is free to run other tasks.
    img = Image.new('RGB', (4000, 3000), color = 'red')
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG')
    image_bytes = img_byte_arr.getvalue()

    # Function to monitor event loop lag
    async def monitor_loop():
        start = time.perf_counter()
        await asyncio.sleep(0.1)
        return time.perf_counter() - start - 0.1

    print("Benchmarking event loop blocking (Sync vs Async/Threadpool)")

    # 1. Sync
    start = time.perf_counter()
    monitor_task = asyncio.create_task(monitor_loop())

    for _ in range(50):
        sync_open_image(image_bytes)
        # also decode image like what gemini SDK might do
        # well Gemini SDK gets an Image object, so it will likely serialize it to bytes or similar
        img2 = Image.open(io.BytesIO(image_bytes))
        img2.load()

    lag = await monitor_task
    print(f"Sync event loop lag: {lag*1000:.2f}ms")

    # 2. Async
    start = time.perf_counter()
    monitor_task = asyncio.create_task(monitor_loop())

    tasks = []
    for _ in range(50):
        tasks.append(asyncio.to_thread(sync_open_image, image_bytes))
    await asyncio.gather(*tasks)

    lag = await monitor_task
    print(f"Async event loop lag: {lag*1000:.2f}ms")

if __name__ == "__main__":
    asyncio.run(main())
