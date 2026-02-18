import asyncio
from sqlalchemy import text
from bridge_app.database import engine

async def add_category_emoji():
    async with engine.begin() as conn:
        print("Checking for category_emoji column in categories...")
        
        # Check if column exists
        try:
            await conn.execute(text("SELECT category_emoji FROM categories LIMIT 1;"))
            print("Column 'category_emoji' already exists. Skipping.")
            return
        except Exception:
            # If it fails, likely column doesn't exist
            print("Column missing. Adding 'category_emoji'...")
            
        try:
            await conn.execute(text("ALTER TABLE categories ADD COLUMN category_emoji VARCHAR;"))
            print("Column 'category_emoji' added successfully.")
        except Exception as e:
            print(f"Failed to add column: {e}")

if __name__ == "__main__":
    asyncio.run(add_category_emoji())
