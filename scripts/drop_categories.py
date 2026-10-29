import asyncio
import sys
from sqlalchemy import text
from bridge_app.database import engine, DATABASE_URL

async def drop_categories():
    target = DATABASE_URL.split('@')[-1]
    print(f"⚠️  DANGER: This will permanently delete the 'categories' table from: {target}")
    
    if "--yes" not in sys.argv:
        confirm = input("Type 'DROP CATEGORIES' to confirm: ")
        if confirm != "DROP CATEGORIES":
            print("Operation aborted.")
            return

    async with engine.begin() as conn:
        print("Dropping categories table...")
        await conn.execute(text("DROP TABLE IF EXISTS categories;"))
        print("Categories table dropped.")

if __name__ == "__main__":
    asyncio.run(drop_categories())
