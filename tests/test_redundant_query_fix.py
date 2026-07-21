import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from bridge_app.services.orchestrator import _push_to_monarch

@pytest.mark.asyncio
async def test_push_to_monarch_with_passed_creds():
    mock_db = AsyncMock()
    mock_report = AsyncMock()

    mock_creds = MagicMock()
    mock_creds.id = 1

    # We patch push_transaction and get_monarch_client
    with patch('bridge_app.services.orchestrator.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.orchestrator.push_transaction', new_callable=AsyncMock) as mock_push:

        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "tx_123"

        data = {
            "date": "2026-11-01",
            "amount": 10.0,
            "currency": "USD",
            "merchant": "Burger King"
        }

        # When creds is passed, db.execute should not be called to query Credentials
        await _push_to_monarch(data, mock_db, mock_report, creds=mock_creds)

        assert mock_db.execute.call_count == 0
        print("✅ SUCCESS: Passed credentials bypassed database query entirely")

@pytest.mark.asyncio
async def test_push_to_monarch_without_passed_creds():
    mock_db = AsyncMock()
    mock_report = AsyncMock()

    mock_creds = MagicMock()
    mock_creds.id = 1

    # Mock executing selection of credentials
    mock_res = MagicMock()
    mock_res.scalars.return_value.first.return_value = mock_creds
    mock_db.execute.return_value = mock_res

    # We patch push_transaction and get_monarch_client
    with patch('bridge_app.services.orchestrator.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.orchestrator.push_transaction', new_callable=AsyncMock) as mock_push:

        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "tx_123"

        data = {
            "date": "2026-11-01",
            "amount": 10.0,
            "currency": "USD",
            "merchant": "Burger King"
        }

        # When creds is NOT passed, db.execute should be called
        await _push_to_monarch(data, mock_db, mock_report)

        assert mock_db.execute.call_count > 0
        print("✅ SUCCESS: Missing credentials triggered database query")
