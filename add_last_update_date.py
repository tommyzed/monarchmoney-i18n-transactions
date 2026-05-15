import asyncio
import os
from dotenv import load_dotenv

load_dotenv(override=True)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

import urllib.parse
import ssl
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    parsed = urllib.parse.urlparse(DATABASE_URL)
    query_params = urllib.parse.parse_qs(parsed.query)
    
    new_query = urllib.parse.urlencode({
        k: v for k, v in query_params.items() 
        if k not in ['sslmode', 'channel_binding']
    }, doseq=True)
    
    clean_url = urllib.parse.urlunparse(parsed._replace(query=new_query))
    
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    
    engine = create_async_engine(
        clean_url,
        connect_args={
            "ssl": ssl_ctx,
            "timeout": 300,
            "command_timeout": 300
        }
    )
    
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE credentials ADD COLUMN last_update_date TIMESTAMP WITH TIME ZONE;"))
            print("Successfully added last_update_date column.")
        except Exception as e:
            print(f"Error adding column: {e}")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
