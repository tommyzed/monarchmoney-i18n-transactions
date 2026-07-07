import asyncio
import os
import sys
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
import urllib.parse
import ssl

# Load environment
load_dotenv(override=True)
DATABASE_URL = os.getenv("DATABASE_URL")

print(f"--- DATABASE DEBUGGER ---")
print(f"URL: {DATABASE_URL.split('@')[-1] if DATABASE_URL else 'Not Set'}")

if not DATABASE_URL:
    print("❌ ERROR: DATABASE_URL is not set.")
    sys.exit(1)

# Fix URL for asyncpg
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Handle SSL/Params logic from database.py to ensure we test the EXACT same way
parsed = urllib.parse.urlparse(DATABASE_URL)
query_params = urllib.parse.parse_qs(parsed.query)
if "sslmode" in query_params or "channel_binding" in query_params:
    new_query = urllib.parse.urlencode({
        k: v for k, v in query_params.items() 
        if k not in ['sslmode', 'channel_binding']
    }, doseq=True)
    DATABASE_URL = urllib.parse.urlunparse(parsed._replace(query=new_query))

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE
connect_args = {
    "ssl": ssl_ctx,
    "timeout": 5,          # 5 seconds connection timeout
    "command_timeout": 5   # 5 seconds command timeout
}

print("Attempting to connect (Timeout: 5s)...")

async def test_connection():
    try:
        engine = create_async_engine(DATABASE_URL, echo=False, connect_args=connect_args)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            print(f"✅ SUCCESS: Connected! Result: {result.scalar()}")
    except Exception as e:
        print(f"❌ FAILED: Connection error: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())
