import asyncio
from bridge_app.database import engine, Base
# Import models to ensure they are registered on Base.metadata
import bridge_app.models  # noqa: F401


async def main():
    print("🚀 Initializing database schema (creating tables)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created/verified.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
