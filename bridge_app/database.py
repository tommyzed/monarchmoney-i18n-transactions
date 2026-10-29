import os
import sys
import urllib.parse
import ssl
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

def is_test_environment() -> bool:
    if os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING") == "1":
        return True
    if "pytest" in sys.modules:
        return True
    for arg in sys.argv:
        lower_arg = arg.lower()
        if "pytest" in lower_arg or "test_" in lower_arg or lower_arg.startswith("tests/"):
            return True
    return False

IN_TEST = is_test_environment()

# If running in tests, DO NOT override with .env production variables
if IN_TEST:
    load_dotenv(override=False)
    test_db_url = os.getenv("TEST_DATABASE_URL")
    current_url = os.getenv("DATABASE_URL", "")
    is_prod_url = any(prod in current_url for prod in ["neon.tech", "ep-"])
    if test_db_url:
        DATABASE_URL = test_db_url
    elif not current_url or is_prod_url:
        DATABASE_URL = "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true"
    else:
        DATABASE_URL = current_url
else:
    load_dotenv(override=True)
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bridge.db")

# HARD SAFETY GUARD: Block any production database in test mode
PROD_INDICATORS = ["neon.tech", "ep-"]
if IN_TEST and any(prod in DATABASE_URL for prod in PROD_INDICATORS):
    raise RuntimeError(
        f"CRITICAL SAFETY BLOCK: Database URL '{DATABASE_URL.split('@')[-1]}' contains production host indicators "
        f"and cannot be used during test execution! Tests must run against an isolated local or in-memory database."
    )

connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy.pool import StaticPool
    connect_args = {"check_same_thread": False}
    pool_kwargs = {}
    if ":memory:" in DATABASE_URL:
        pool_kwargs["poolclass"] = StaticPool
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args=connect_args,
        **pool_kwargs
    )
elif DATABASE_URL.startswith("postgres"):
    # Ensure correct driver
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    # Parse query params robustly
    parsed = urllib.parse.urlparse(DATABASE_URL)
    query_params = urllib.parse.parse_qs(parsed.query)
    
    # Check for SSL requirements in query
    if "sslmode" in query_params or "channel_binding" in query_params:
        new_query = urllib.parse.urlencode({
            k: v for k, v in query_params.items() 
            if k not in ['sslmode', 'channel_binding']
        }, doseq=True)
        
        DATABASE_URL = urllib.parse.urlunparse(parsed._replace(query=new_query))
        
        sslmode = query_params.get("sslmode", [""])[0]

        if sslmode:
            valid_sslmodes = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
            if sslmode not in valid_sslmodes:
                raise ValueError(f"Invalid sslmode parameter: {sslmode}")

        if sslmode == "disable":
            ssl_ctx = False
        else:
            ssl_ctx = ssl.create_default_context()
        
        connect_args = {
            "ssl": ssl_ctx,
            "timeout": 300,
            "command_timeout": 300
        }

    engine = create_async_engine(
        DATABASE_URL, 
        echo=False, 
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300
    )
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args=connect_args
    )

print(f"🧱 LIFESPAN: Connecting to {DATABASE_URL.split('@')[-1]}")
print(f"🧱 LIFESPAN: connect_args={connect_args}")

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
