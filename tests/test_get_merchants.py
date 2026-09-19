import pytest
from httpx import AsyncClient, ASGITransport
import hashlib
from bridge_app.main import app, COOKIE_VALUE
from bridge_app.database import get_db, engine, Base
from bridge_app.models import Merchant
from sqlalchemy import delete

@pytest.fixture
def auth_cookies():
    # Retrieve the cookie value that is already configured in the app
    if COOKIE_VALUE:
        return {"device_token": COOKIE_VALUE}
    return {}

@pytest.fixture
async def setup_merchants():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for db in get_db():
        # Clean up
        await db.execute(delete(Merchant))

        # Add test merchants
        merchants = [
            Merchant(name="Apple Store", is_starred=True),
            Merchant(name="Amazon", is_starred=False),
            Merchant(name="Applebees", is_starred=False),
            Merchant(name="Best Buy", is_starred=True),
            Merchant(name="Target", is_starred=False)
        ]
        db.add_all(merchants)
        await db.commit()

        yield db

        # Cleanup after
        await db.execute(delete(Merchant))
        await db.commit()

@pytest.mark.asyncio
async def test_get_merchants_no_filters(setup_merchants, auth_cookies):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants")

    assert response.status_code == 200
    data = response.json()
    assert "merchants" in data
    assert len(data["merchants"]) == 5

    # Check ordering: starred first, then alphabetical
    names = [m["name"] for m in data["merchants"]]
    assert names == ["Apple Store", "Best Buy", "Amazon", "Applebees", "Target"]

@pytest.mark.asyncio
async def test_get_merchants_starred_filter(setup_merchants, auth_cookies):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants?starred=true")

    assert response.status_code == 200
    data = response.json()
    assert len(data["merchants"]) == 2
    names = [m["name"] for m in data["merchants"]]
    assert names == ["Apple Store", "Best Buy"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants?starred=false")

    assert response.status_code == 200
    data = response.json()
    assert len(data["merchants"]) == 3
    names = [m["name"] for m in data["merchants"]]
    assert names == ["Amazon", "Applebees", "Target"]

@pytest.mark.asyncio
async def test_get_merchants_query_filter(setup_merchants, auth_cookies):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants?q=apple")

    assert response.status_code == 200
    data = response.json()
    assert len(data["merchants"]) == 2
    names = [m["name"] for m in data["merchants"]]
    assert names == ["Apple Store", "Applebees"]

@pytest.mark.asyncio
async def test_get_merchants_combined_filters(setup_merchants, auth_cookies):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants?q=apple&starred=true")

    assert response.status_code == 200
    data = response.json()
    assert len(data["merchants"]) == 1
    assert data["merchants"][0]["name"] == "Apple Store"

@pytest.mark.asyncio
async def test_get_merchants_empty_results(setup_merchants, auth_cookies):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=auth_cookies) as ac:
        response = await ac.get("/api/merchants?q=walmart")

    assert response.status_code == 200
    data = response.json()
    assert len(data["merchants"]) == 0
