import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from bridge_app.database import DATABASE_URL

async def check_schema():
    print(f"Connecting to {DATABASE_URL}")
    engine = create_async_engine(DATABASE_URL)
    
    async with engine.begin() as conn:
        print("Querying columns for 'categories' table...")
        result = await conn.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='categories'"
        ))
        rows = result.fetchall()
        print("Columns found:")
        found = False
        for row in rows:
            print(f" - {row.column_name} ({row.data_type})")
            if row.column_name == 'monarch_category_id':
                found = True
        
        if found:
            print("✅ 'monarch_category_id' column exists.")
        else:
            print("❌ 'monarch_category_id' column MISSING!")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check_schema())
