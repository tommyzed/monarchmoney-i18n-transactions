import asyncio
import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

load_dotenv(override=True)

# **** NOTE: THIS DOES NOT CURRENTLY WORK ****
# Force check for DATABASE_URL
url = os.getenv("DATABASE_URL")
if not url:
    print("Error: DATABASE_URL not found in .env")
    exit(1)

print(f"Deploying schema to: {url.split('@')[-1]}") # Mask password

from bridge_app.database import engine, Base
# Import models to ensure they are registered on Base.metadata
import bridge_app.models  # noqa: F401

async def deploy():
    async with engine.begin() as conn:
        print("Creating tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("Schema successfully deployed!")

if __name__ == "__main__":
    try:
        asyncio.run(deploy())
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Deployment failed: {e}")
