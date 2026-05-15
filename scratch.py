import asyncio
from bridge_app.database import get_db
from bridge_app.models import Credentials
from sqlalchemy import select

async def main():
    async for db in get_db():
        creds_result = await db.execute(select(Credentials).where(Credentials.monarch_session.isnot(None)))
        creds = creds_result.scalars().first()
        print("Creds by session isnot None:", creds)

        break

asyncio.run(main())
