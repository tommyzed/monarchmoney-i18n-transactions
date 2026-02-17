import asyncio
import os
from sqlalchemy import text
from bridge_app.database import engine

async def migrate():
    async with engine.begin() as conn:
        print("Checking for merchant_mappings table...")
        
        # Check if table exists
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='merchant_mappings';"
        ))
        table_exists = result.scalar_one_or_none()
        
        if not table_exists:
            print("Creating merchant_mappings table...")
            await conn.execute(text("""
                CREATE TABLE merchant_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_merchant_name VARCHAR NOT NULL UNIQUE,
                    monarch_merchant_name VARCHAR,
                    category_id VARCHAR
                );
            """))
            await conn.execute(text("""
                CREATE INDEX ix_merchant_mappings_receipt_merchant_name ON merchant_mappings (receipt_merchant_name);
            """))
            print("Table created successfully.")
        else:
            print("Table already exists. Skipping.")

if __name__ == "__main__":
    asyncio.run(migrate())
