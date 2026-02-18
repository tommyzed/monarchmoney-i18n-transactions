import asyncio
from sqlalchemy import text
from bridge_app.database import engine

async def add_column():
    async with engine.begin() as conn:
        print("Checking for category_name column in merchant_mappings...")
        
        # Check if column exists (Postgres)
        # We can try to select it, or check information_schema
        try:
            await conn.execute(text("SELECT category_name FROM merchant_mappings LIMIT 1;"))
            print("Column 'category_name' already exists. Skipping.")
            return
        except Exception:
            # If it fails, likely column doesn't exist
            print("Column missing. Adding 'category_name'...")
            
        try:
            await conn.execute(text("ALTER TABLE merchant_mappings ADD COLUMN category_name VARCHAR;"))
            print("Column 'category_name' added successfully.")
        except Exception as e:
            print(f"Failed to add column: {e}")

if __name__ == "__main__":
    asyncio.run(add_column())
