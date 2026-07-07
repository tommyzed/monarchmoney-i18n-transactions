import asyncio
from sqlalchemy import text
from bridge_app.database import engine

async def migrate_categories():
    async with engine.begin() as conn:
        print("Checking for categories table...")
        
        # Check if table exists (Postgres compatible)
        result = await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name = 'categories';"
        ))
        table_exists = result.scalar_one_or_none()
        
        if not table_exists:
            print("Creating categories table...")
            # Using standard SQL, adjusting for potential split with database.py logic if needed.
            # But here we are using sqlalchemy text directly.
            # Note: models.py uses Integer PK, schema.sql uses SERIAL (postgres style).
            # SQLite uses INTEGER PRIMARY KEY AUTOINCREMENT.
            
            await conn.execute(text("""
                CREATE TABLE categories (
                    category_name VARCHAR PRIMARY KEY,
                    category_emoji VARCHAR
                );
            """))
            print("Categories table created successfully.")
        else:
            print("Categories table already exists. Skipping.")

if __name__ == "__main__":
    asyncio.run(migrate_categories())
