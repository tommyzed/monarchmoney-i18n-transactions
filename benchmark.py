import asyncio
import time
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from bridge_app.models import Base, Category
from bridge_app.services.orchestrator import _fetch_category_emoji, _CATEGORY_EMOJI_CACHE
import bridge_app.services.orchestrator as orch

async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # insert 1000 categories
        for i in range(1000):
            cat = Category(category_name=f"Cat_{i}", category_emoji=f"E{i}")
            session.add(cat)
        await session.commit()

    return async_session

async def run_benchmark():
    async_session = await setup_db()

    # clear cache
    orch._CATEGORY_EMOJI_CACHE.clear()
    if hasattr(orch, '_CATEGORY_EMOJI_CACHE_LOADED'):
        orch._CATEGORY_EMOJI_CACHE_LOADED = False

    start_time = time.time()
    async with async_session() as session:
        for i in range(1000):
            data = {"category_name": f"Cat_{i}"}
            await _fetch_category_emoji(data, session)
            assert data["category_emoji"] == f"E{i}"

    end_time = time.time()
    print(f"Time taken for 1000 fetches: {end_time - start_time:.4f} seconds")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
