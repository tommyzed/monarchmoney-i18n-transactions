import asyncio
from bridge_app.database import engine, Base
# Import models to ensure they are registered on Base.metadata
from bridge_app.models import Credentials, Transaction, MerchantMapping, Category, FireSettings, Log

async def main():
    print("🚀 Initializing database schema (creating tables)...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Database tables created/verified.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
