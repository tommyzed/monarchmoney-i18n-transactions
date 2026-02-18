import asyncio
from sqlalchemy import text
from bridge_app.database import engine

async def drop_categories():
    async with engine.begin() as conn:
        print("Dropping categories table...")
        await conn.execute(text("DROP TABLE IF EXISTS categories;"))
        print("Categories table dropped.")

if __name__ == "__main__":
    asyncio.run(drop_categories())
