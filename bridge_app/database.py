import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bridge.db")

connect_args = {}
import urllib.parse

import ssl

# Fix driver and sslmode for asyncpg
if DATABASE_URL.startswith("postgresql"):
    # Ensure correct driver
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    
    # Parse query params robustly
    parsed = urllib.parse.urlparse(DATABASE_URL)
    query_params = urllib.parse.parse_qs(parsed.query)
    
    # Check for SSL requirements in query
    # asyncpg prefers these in connect_args, not URL params usually
    if "sslmode" in query_params or "channel_binding" in query_params:
        # Strip them from the URL
        new_query = urllib.parse.urlencode({
            k: v for k, v in query_params.items() 
            if k not in ['sslmode', 'channel_binding']
        }, doseq=True)
        
        DATABASE_URL = urllib.parse.urlunparse(parsed._replace(query=new_query))
        
        # Enforce SSL for Neon/Cloud Postgres with explicit context to avoid hangs
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        
        # Increase connection timeout to 300s (5m) to handle Neon cold starts/latency
        connect_args = {
            "ssl": ssl_ctx,
            "timeout": 300,
            "command_timeout": 300
        }

print(f"🧱 LIFESPAN: Connecting to {DATABASE_URL.split('@')[-1]}")
print(f"🧱 LIFESPAN: connect_args={connect_args}")

engine = create_async_engine(DATABASE_URL, echo=False, connect_args=connect_args)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
