import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from bridge_app.main import app
from bridge_app.models import FailedTransaction
from bridge_app.database import get_db, engine, Base
import hashlib

@pytest.fixture(autouse=True)
def setup_unlock_secret(monkeypatch):
    monkeypatch.setenv("UNLOCK_SECRET", "test_secret")
    from bridge_app import main
    monkeypatch.setattr(main, "UNLOCK_SECRET", "test_secret")
    monkeypatch.setattr(main, "COOKIE_VALUE", hashlib.sha256("test_secret".encode()).hexdigest())

@pytest.fixture
def auth_client():
    client = TestClient(app)
    cookie_value = hashlib.sha256("test_secret".encode()).hexdigest()
    client.cookies.set("device_token", cookie_value)
    return client

@pytest.mark.asyncio
async def test_get_failed_transactions_empty(auth_client):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for session in get_db():
        # Clear existing
        await session.execute(FailedTransaction.__table__.delete())
        await session.commit()

    response = auth_client.get("/api/failed-transactions")
    assert response.status_code == 200
    assert response.json() == []

@pytest.mark.asyncio
async def test_get_failed_transactions_with_data(auth_client):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for session in get_db():
        await session.execute(FailedTransaction.__table__.delete())

        # Create a test transaction
        tx = FailedTransaction(
            source_type="receipt",
            error_message="Test error",
            retry_count=1,
            user_currency="USD",
            parsed_data={"merchant": "Test Merchant", "amount": 10.5},
            manual_data=None,
            raw_content=b"dummy_image_data"
        )
        session.add(tx)
        await session.commit()
        await session.refresh(tx)

    response = auth_client.get("/api/failed-transactions")
    assert response.status_code == 200

    data = response.json()
    assert len(data) == 1

    item = data[0]
    assert item["source_type"] == "receipt"
    assert item["error_message"] == "Test error"
    assert item["retry_count"] == 1
    assert item["user_currency"] == "USD"
    assert item["merchant"] == "Test Merchant"
    assert item["amount"] == 10.5
    assert item["has_image"] is True
    assert "raw_content" not in item # Ensure large bytes are excluded
