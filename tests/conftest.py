import os
import sys
import pytest

# Enforce test environment flags BEFORE anything imports bridge_app
os.environ["TESTING"] = "1"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true"

import bridge_app.database
import bridge_app.models

# Canonical test engine and sessionmaker
TEST_ENGINE = bridge_app.database.engine
TEST_SESSION_LOCAL = bridge_app.database.AsyncSessionLocal

@pytest.fixture(autouse=True)
async def init_test_db():
    """Ensure in-memory SQLite tables exist for every test and point to canonical test engine."""
    import bridge_app.database as db_mod
    db_mod.engine = TEST_ENGINE
    db_mod.AsyncSessionLocal = TEST_SESSION_LOCAL
    if "bridge_app.main" in sys.modules:
        import bridge_app.main as main_mod
        main_mod.engine = TEST_ENGINE
        main_mod.AsyncSessionLocal = TEST_SESSION_LOCAL

    async with TEST_ENGINE.begin() as conn:
        await conn.run_sync(bridge_app.models.Base.metadata.create_all)
    yield
