import pytest
import os
import hashlib
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from bridge_app.main import app, get_db
from bridge_app.models import Credentials, FireSettings

# Setup the UNLOCK_SECRET and ensure main uses the same setup
UNLOCK_SECRET = "test_secret"
COOKIE_VALUE = hashlib.sha256(UNLOCK_SECRET.encode()).hexdigest()

@pytest.fixture(autouse=True)
def setup_secret_env(monkeypatch):
    monkeypatch.setenv("UNLOCK_SECRET", UNLOCK_SECRET)
    from bridge_app import main
    monkeypatch.setattr(main, "UNLOCK_SECRET", UNLOCK_SECRET)
    monkeypatch.setattr(main, "COOKIE_VALUE", COOKIE_VALUE)

@pytest.fixture
def mock_db():
    db = AsyncMock()

    mock_creds = Credentials(
        id=1,
        email="test@test.com",
        encrypted_payload=b"encrypted_secret",
    )
    mock_settings = FireSettings(
        id=1,
        current_age=30,
        retirement_age=55,
        annual_contribution=50000,
        annual_retirement_spending=40000,
        risk_tolerance="moderate",
        inflation_rate=0.03,
        final_age=85,
        social_security_enabled=False,
    )

    async def mock_execute(stmt):
        stmt_str = str(stmt)
        result_mock = MagicMock()

        if "credentials" in stmt_str:
            result_mock.scalars.return_value.first.return_value = mock_creds
        elif "fire_settings" in stmt_str:
            result_mock.scalar_one_or_none.return_value = mock_settings
        else:
            result_mock.scalar_one_or_none.return_value = None
            result_mock.scalars.return_value.first.return_value = None

        return result_mock

    db.execute = AsyncMock(side_effect=mock_execute)
    return db

@pytest.fixture
def mock_db_no_creds():
    db = AsyncMock()
    async def mock_execute(stmt):
        result_mock = MagicMock()
        result_mock.scalars.return_value.first.return_value = None
        result_mock.scalar_one_or_none.return_value = None
        return result_mock
    db.execute = AsyncMock(side_effect=mock_execute)
    return db

@pytest.fixture
async def client_unauthenticated():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture
async def client_authenticated():
    transport = ASGITransport(app=app)
    cookies = {"device_token": COOKIE_VALUE}
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as ac:
        yield ac

# ── Demo Mode Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_fire_simulation_unauthenticated_falls_back_to_demo(client_unauthenticated):
    """
    An unauthenticated request does not have the auth cookie set,
    so it should automatically fall back to demo mode and return a 200 simulation response.
    """
    response = await client_unauthenticated.post("/api/fire/simulate", json={})
    assert response.status_code == 200
    data = response.json()
    assert "years" in data
    assert "percentile_50" in data
    assert len(data["account_breakdown"]) == 0  # Demo mode has empty breakdown
    assert data["monthly_spend_avg"] == 6015

@pytest.mark.asyncio
async def test_run_fire_simulation_explicit_demo(client_authenticated):
    """
    An authenticated request with is_demo=True in the payload should also fall back to demo mode.
    """
    response = await client_authenticated.post("/api/fire/simulate", json={"is_demo": True})
    assert response.status_code == 200
    data = response.json()
    assert "years" in data
    assert len(data["account_breakdown"]) == 0
    assert data["monthly_spend_avg"] == 6015

# ── Live Mode Success / Failure Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_run_fire_simulation_live_success(client_authenticated, mock_db):
    """
    Fully successful run of the live simulation endpoint.
    - User is authenticated.
    - Credentials are loaded from the db.
    - Monarch client is successfully instantiated.
    - Accounts and cashflow are fetched.
    - Simulation is executed and results returned.
    """
    app.dependency_overrides[get_db] = lambda: mock_db

    mock_mm = AsyncMock()
    mock_mm.get_accounts.return_value = {
        "accounts": [
            {
                "id": "acc_1",
                "displayName": "Checking",
                "type": {"name": "depository", "display": "Cash"},
                "subtype": {"name": "checking", "display": "Checking"},
                "currentBalance": 50000.0,
                "displayBalance": 50000.0,
                "includeInNetWorth": True,
            },
            {
                "id": "acc_2",
                "displayName": "Roth IRA",
                "type": {"name": "brokerage", "display": "Investments"},
                "subtype": {"name": "roth", "display": "Roth IRA"},
                "currentBalance": 150000.0,
                "displayBalance": 150000.0,
                "includeInNetWorth": True,
            }
        ]
    }
    mock_mm.get_cashflow_summary.return_value = {
        "summary": [
            {
                "summary": {
                    "sumIncome": 100000.0,
                    "sumExpense": -60000.0,
                    "savings": 40000.0,
                    "savingsRate": 0.4,
                }
            }
        ]
    }

    with patch("bridge_app.main.get_monarch_client", new_callable=AsyncMock) as mock_get_client:
        mock_get_client.return_value = mock_mm

        response = await client_authenticated.post("/api/fire/simulate", json={"is_demo": False})
        assert response.status_code == 200

        data = response.json()
        assert "years" in data
        assert "percentile_50" in data
        assert data["current_portfolio"] == 200000.0  # Sum of Checking (50k) and Roth IRA (150k)
        assert len(data["account_breakdown"]) == 2
        assert data["monthly_spend_avg"] == 5000.0   # 60000 / 12

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_run_fire_simulation_no_credentials(client_authenticated, mock_db_no_creds):
    """
    In live mode, if credentials are missing from the DB, return 503 error.
    """
    app.dependency_overrides[get_db] = lambda: mock_db_no_creds

    response = await client_authenticated.post("/api/fire/simulate", json={"is_demo": False})
    assert response.status_code == 503
    assert "No Monarch credentials configured." in response.json()["detail"]

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_run_fire_simulation_monarch_client_instantiation_failed(client_authenticated, mock_db):
    """
    In live mode, if `get_monarch_client` raises an exception, return 503.
    """
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch("bridge_app.main.get_monarch_client", side_effect=Exception("Connection timed out")):
        response = await client_authenticated.post("/api/fire/simulate", json={"is_demo": False})
        assert response.status_code == 503
        assert "Monarch connection failed: Connection timed out" in response.json()["detail"]

    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_run_fire_simulation_fetch_accounts_failed(client_authenticated, mock_db):
    """
    In live mode, if fetching accounts fails, return 502.
    """
    app.dependency_overrides[get_db] = lambda: mock_db

    mock_mm = AsyncMock()
    mock_mm.get_accounts.side_effect = Exception("API rate limit exceeded")

    with patch("bridge_app.main.get_monarch_client", new_callable=AsyncMock) as mock_get_client:
        mock_get_client.return_value = mock_mm

        response = await client_authenticated.post("/api/fire/simulate", json={"is_demo": False})
        assert response.status_code == 502
        assert "Failed to fetch accounts: API rate limit exceeded" in response.json()["detail"]

    app.dependency_overrides.clear()
