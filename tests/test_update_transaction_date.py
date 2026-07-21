import pytest
import os
import hashlib
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from bridge_app.main import app, get_db, UpdateDateRequest
from bridge_app.models import Credentials, Log

# Set up UNLOCK_SECRET for GhostSecurityMiddleware
UNLOCK_SECRET = "test_secret"
os.environ["UNLOCK_SECRET"] = UNLOCK_SECRET
COOKIE_VALUE = hashlib.sha256(UNLOCK_SECRET.encode()).hexdigest()
cookies = {"device_token": COOKIE_VALUE}

@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db

@pytest.fixture
def client(mock_db):
    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, cookies=cookies) as tc:
        yield tc
    app.dependency_overrides.clear()

def test_update_transaction_date_usd_standard_path(client, mock_db):
    # Setup mocks
    mock_creds = Credentials(id=1)
    mock_log = Log(
        id=42,
        merchant="Starbucks",
        amount=-5.50,
        currency="USD",
        date="2026-11-02",
        monarch_tx_id="tx_starbucks_1"
    )

    def mock_execute_fn(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str or "log" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_tx_id": "tx_starbucks_1",
        "new_date": "2026-11-03",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields:

        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}

        response = client.post("/api/transaction/update-date", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_date"] == "2026-11-03"
        assert data["new_amount"] == 5.50
        assert data["exchange_rate"] is None
        assert data["original_currency"] == "USD"

        # Verify that mock_update_fields was called without amount/notes
        mock_update_fields.assert_called_once_with(
            mock_get_client.return_value,
            transaction_id="tx_starbucks_1",
            date="2026-11-03"
        )

        # Verify that log date was updated
        assert mock_log.date == "2026-11-03"


def test_update_transaction_date_usd_compatibility_path(client, mock_db):
    # Setup mocks
    mock_creds = Credentials(id=1)
    mock_log = Log(
        id=42,
        merchant="Starbucks",
        amount=-5.50,
        currency="USD",
        date="2026-11-02",
        monarch_tx_id="tx_starbucks_1"
    )

    def mock_execute_fn(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str or "log" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_id": "tx_starbucks_1",
        "date": "2026-11-03",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields:

        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}

        # Call the alternate endpoint route
        response = client.post("/api/update-date", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_date"] == "2026-11-03"
        assert data["new_amount"] == 5.50
        assert data["exchange_rate"] is None
        assert data["original_currency"] == "USD"

        # Verify that mock_update_fields was called without amount/notes
        mock_update_fields.assert_called_once_with(
            mock_get_client.return_value,
            transaction_id="tx_starbucks_1",
            date="2026-11-03"
        )


def test_update_transaction_date_empty_currency(client, mock_db):
    # Setup mocks
    mock_creds = Credentials(id=1)
    mock_log = Log(
        id=42,
        merchant="Starbucks",
        amount=-5.50,
        currency="USD",
        date="2026-11-02",
        monarch_tx_id="tx_starbucks_2"
    )

    def mock_execute_fn(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str or "log" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_tx_id": "tx_starbucks_2",
        "new_date": "2026-11-03",
        "original_currency": " ",
        "original_amount": 5.50,
        "is_credit": False
    }

    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields:

        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}

        response = client.post("/api/transaction/update-date", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_date"] == "2026-11-03"
        assert data["new_amount"] == 5.50
        assert data["exchange_rate"] is None
        assert data["original_currency"] == ""

        # Verify that mock_update_fields was called without amount/notes
        mock_update_fields.assert_called_once_with(
            mock_get_client.return_value,
            transaction_id="tx_starbucks_2",
            date="2026-11-03"
        )

        # Verify that log date was updated
        assert mock_log.date == "2026-11-03"


def test_update_transaction_date_foreign_currency_debit(client, mock_db):
    # Setup mocks
    mock_creds = Credentials(id=1)
    mock_log = Log(
        id=43,
        merchant="French Bakery",
        amount=-10.00,
        currency="USD",
        date="2026-11-02",
        original_amount=8.00,
        original_currency="EUR",
        is_cash=False,
        monarch_tx_id="tx_bakery_1"
    )

    def mock_execute_fn(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str or "log" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_tx_id": "tx_bakery_1",
        "new_date": "2026-11-03",
        "original_currency": "EUR",
        "original_amount": 8.00,
        "is_credit": False
    }

    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields, \
         patch('bridge_app.services.currency.get_exchange_rate', new_callable=AsyncMock) as mock_rate:

        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}
        mock_rate.return_value = 1.25  # 8.00 EUR * 1.25 = 10.00 USD

        response = client.post("/api/transaction/update-date", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_date"] == "2026-11-03"
        assert data["new_amount"] == 10.00
        assert data["exchange_rate"] == 1.25
        assert data["original_currency"] == "EUR"
        assert data["amount_updated"] is True

        # For debit, amount should be negative: -10.00
        expected_notes = "Original Price: EUR 8.00\nExchange Rate: 1.25 USD/EUR"
        mock_update_fields.assert_called_once_with(
            mock_get_client.return_value,
            transaction_id="tx_bakery_1",
            date="2026-11-03",
            amount=-10.00,
            notes=expected_notes
        )

        # Verify that log date and amount were updated
        assert mock_log.date == "2026-11-03"
        assert mock_log.amount == -10.00


def test_update_transaction_date_foreign_currency_credit(client, mock_db):
    # Setup mocks
    mock_creds = Credentials(id=1)
    mock_log = Log(
        id=44,
        merchant="Refund",
        amount=5.00,
        currency="USD",
        date="2026-11-02",
        original_amount=4.00,
        original_currency="EUR",
        is_cash=False,
        monarch_tx_id="tx_refund_1"
    )

    def mock_execute_fn(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str or "log" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_tx_id": "tx_refund_1",
        "new_date": "2026-11-03",
        "original_currency": "EUR",
        "original_amount": 4.00,
        "is_credit": True
    }

    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields, \
         patch('bridge_app.services.currency.get_exchange_rate', new_callable=AsyncMock) as mock_rate:

        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}
        mock_rate.return_value = 1.25  # 4.00 EUR * 1.25 = 5.00 USD

        response = client.post("/api/transaction/update-date", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_date"] == "2026-11-03"
        assert data["new_amount"] == 5.00
        assert data["exchange_rate"] == 1.25
        assert data["original_currency"] == "EUR"
        assert data["amount_updated"] is True

        # For credit, amount should be positive: 5.00
        expected_notes = "Original Price: EUR 4.00\nExchange Rate: 1.25 USD/EUR"
        mock_update_fields.assert_called_once_with(
            mock_get_client.return_value,
            transaction_id="tx_refund_1",
            date="2026-11-03",
            amount=5.00,
            notes=expected_notes
        )

        # Verify that log date and amount were updated
        assert mock_log.date == "2026-11-03"
        assert mock_log.amount == 5.00


def test_update_transaction_date_missing_credentials(client, mock_db):
    # Mock Credentials query to return None (no credentials configured)
    def mock_execute_fn(stmt):
        res = MagicMock()
        res.scalars.return_value.first.return_value = None
        return res

    mock_db.execute.side_effect = mock_execute_fn

    payload = {
        "monarch_tx_id": "tx_starbucks_1",
        "new_date": "2026-11-03",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    response = client.post("/api/transaction/update-date", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "No Monarch credentials configured"


def test_update_transaction_date_exception_handling(client, mock_db):
    # Mock general exception
    mock_db.execute.side_effect = Exception("Database disk image is malformed")

    payload = {
        "monarch_tx_id": "tx_starbucks_1",
        "new_date": "2026-11-03",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    response = client.post("/api/transaction/update-date", json=payload)

    assert response.status_code == 500
    assert "Database disk image is malformed" in response.json()["detail"]


def test_update_transaction_date_missing_date_error(client, mock_db):
    # Payload missing date information entirely
    payload = {
        "monarch_tx_id": "tx_starbucks_1",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    response = client.post("/api/update-date", json=payload)
    assert response.status_code == 422
    assert "date or new_date is required" in response.json()["detail"]


def test_update_transaction_date_missing_id_error(client, mock_db):
    # Payload missing monarch id information entirely
    payload = {
        "new_date": "2026-11-03",
        "original_currency": "USD",
        "original_amount": 5.50,
        "is_credit": False
    }

    response = client.post("/api/update-date", json=payload)
    assert response.status_code == 422
    assert "monarch_tx_id or monarch_id is required" in response.json()["detail"]
