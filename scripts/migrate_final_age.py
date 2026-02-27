import asyncio
import sys
import os

sys.path.append(os.getcwd())

from bridge_app.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        print("Adding final_age to fire_settings...")
        try:
            await conn.execute(text("ALTER TABLE fire_settings ADD COLUMN final_age INTEGER DEFAULT 85;"))
            print("Successfully added final_age column.")
        except Exception as e:
            print(f"Migration error (column might already exist): {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
