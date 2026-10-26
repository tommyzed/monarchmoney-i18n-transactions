import pytest
from bridge_app.database import engine

@pytest.fixture(autouse=True)
async def cleanup_db_pool():
    yield
    try:
        await engine.dispose()
    except Exception:
        pass
