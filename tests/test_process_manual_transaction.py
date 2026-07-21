import asyncio
import hashlib
import json
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from bridge_app.services.orchestrator import process_manual_transaction
from bridge_app.models import Log, Transaction, Credentials, MerchantMapping, Category

# Simple progress callback for verification
async def dummy_progress(msg, percent):
    pass

@pytest.mark.asyncio
async def test_process_manual_transaction_success():
    """
    Test standard successful execution of process_manual_transaction.
    Ensures:
      - Progress is reported.
      - Duplicates are checked.
      - Auto-mapping is applied.
      - Currency is converted (using mocked get_exchange_rate).
      - Monarch client is retrieved and transaction pushed.
      - Category emoji is fetched.
      - Transaction is saved to the db.
    """
    mock_db = AsyncMock()

    # Mock the Credentials and MerchantMapping / Category database queries.
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.id = 42

    # Return different mock entities depending on query
    def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "merchant_mappings" in stmt_str or "merchantmapping" in stmt_str:
            # Return no mapping first to keep it simple, or mock mapping if needed
            res.scalars.return_value.first.return_value = None
            res.scalar_one_or_none.return_value = None
        elif "categories" in stmt_str or "category" in stmt_str:
            mock_cat = MagicMock(spec=Category)
            mock_cat.category_emoji = "🍕"
            res.scalar_one_or_none.return_value = mock_cat
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    # Test Data: Manual Transaction in EUR (to test currency conversion)
    manual_data = {
        "amount": 100.0,
        "currency": "EUR",
        "date": "2026-11-01",
        "merchant": "La Parisienne",
        "category_name": "Food & Dining"
    }

    # Mock the external functions called inside orchestrator
    with patch("bridge_app.services.orchestrator.get_monarch_client", new_callable=AsyncMock) as mock_get_client, \
         patch("bridge_app.services.orchestrator.push_transaction", new_callable=AsyncMock) as mock_push, \
         patch("bridge_app.services.currency.get_exchange_rate", new_callable=AsyncMock) as mock_rate:

        # Monarch Mock responses
        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "monarch_tx_999"

        # ForEx Mock response: EUR to USD rate of 1.10
        mock_rate.return_value = 1.10

        # Run process_manual_transaction
        result = await process_manual_transaction(
            manual_data=manual_data,
            db=mock_db,
            progress_callback=dummy_progress,
            force_override=False
        )

        # Assertions
        assert result is not None
        assert result["currency"] == "USD"
        assert result["amount"] == 110.0  # 100 * 1.10
        assert result["original_amount"] == 100.0
        assert result["original_currency"] == "EUR"
        assert result["exchange_rate"] == 1.10
        assert result["monarch_tx_id"] == "monarch_tx_999"
        assert result["category_emoji"] == "🍕"

        # Verify DB additions: 1 Transaction, 1 Log
        assert mock_db.add.call_count == 2
        added_objects = [call[0][0] for call in mock_db.add.call_args_list]

        tx_obj = next((x for x in added_objects if isinstance(x, Transaction)), None)
        log_obj = next((x for x in added_objects if isinstance(x, Log)), None)

        assert tx_obj is not None
        assert tx_obj.image_hash.startswith("manual_")
        assert tx_obj.parsed_data == result

        assert log_obj is not None
        assert log_obj.merchant == "La Parisienne"
        assert log_obj.amount == -110.0  # Debited/Signed amount (is_credit is False)
        assert log_obj.currency == "USD"
        assert log_obj.date == "2026-11-01"
        assert log_obj.original_amount == 100.0
        assert log_obj.original_currency == "EUR"
        assert log_obj.monarch_tx_id == "monarch_tx_999"

        # Verify commit was called
        mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_manual_transaction_duplicate():
    """
    Test that process_manual_transaction detects duplicates and returns the existing transaction data.
    """
    mock_db = AsyncMock()

    # Create dummy existing transaction
    existing_parsed = {
        "amount": 50.0,
        "currency": "USD",
        "date": "2026-11-02",
        "merchant": "Dup Shop",
        "monarch_tx_id": "tx_dup_111"
    }
    existing_tx = Transaction(image_hash="manual_dummy_hash", parsed_data=existing_parsed)

    # When duplicate check executes select(Transaction), return existing_tx
    def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "transactions" in stmt_str or "transaction" in stmt_str:
            res.scalar_one_or_none.return_value = existing_tx
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    manual_data = {
        "amount": 50.0,
        "currency": "USD",
        "date": "2026-11-02",
        "merchant": "Dup Shop"
    }

    # Run
    result = await process_manual_transaction(
        manual_data=manual_data,
        db=mock_db,
        progress_callback=dummy_progress,
        force_override=False
    )

    # Assertions: Should return duplicate status and the existing transaction data
    assert result == {"status": "duplicate", "data": existing_parsed}
    # DB add/commit should NOT be called since duplicate returns early
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_process_manual_transaction_force_override():
    """
    Test that force_override bypasses duplicate check and generates a unique hash, completing successfully.
    """
    mock_db = AsyncMock()

    # Mock the Credentials DB queries.
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.id = 101

    # Return credentials when requested, and mock duplicate transaction
    # (duplicate return shouldn't matter because of force_override)
    existing_tx = Transaction(image_hash="manual_dummy_hash", parsed_data={})

    def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "transactions" in stmt_str:
            res.scalar_one_or_none.return_value = existing_tx
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    manual_data = {
        "amount": 25.0,
        "currency": "USD",
        "date": "2026-11-03",
        "merchant": "Forced Shop"
    }

    with patch("bridge_app.services.orchestrator.get_monarch_client", new_callable=AsyncMock) as mock_get_client, \
         patch("bridge_app.services.orchestrator.push_transaction", new_callable=AsyncMock) as mock_push:

        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "monarch_tx_forced"

        # Run with force_override=True
        result = await process_manual_transaction(
            manual_data=manual_data,
            db=mock_db,
            progress_callback=dummy_progress,
            force_override=True
        )

        assert result is not None
        assert result["monarch_tx_id"] == "monarch_tx_forced"

        # Verify db.add is called with unique hash (contains _forced_)
        assert mock_db.add.call_count == 2
        added_objects = [call[0][0] for call in mock_db.add.call_args_list]
        tx_obj = next((x for x in added_objects if isinstance(x, Transaction)), None)
        assert tx_obj is not None
        assert "_forced_" in tx_obj.image_hash


@pytest.mark.asyncio
async def test_process_manual_transaction_with_auto_mapping():
    """
    Test that process_manual_transaction applies MerchantMapping from the database correctly.
    """
    mock_db = AsyncMock()

    # Credentials
    mock_creds = MagicMock(spec=Credentials)
    mock_creds.id = 55

    # Merchant Mapping: "starbucks" maps to "Starbucks Corp" with category "Coffee Shop"
    mock_mapping = MerchantMapping(
        receipt_merchant_name="starbucks",
        monarch_merchant_name="Starbucks Corp",
        category_name="Coffee Shop"
    )

    def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "merchant_mappings" in stmt_str or "merchantmapping" in stmt_str:
            res.scalars.return_value.first.return_value = mock_mapping
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    manual_data = {
        "amount": 4.50,
        "currency": "USD",
        "date": "2026-11-04",
        "merchant": "starbucks"
    }

    with patch("bridge_app.services.orchestrator.get_monarch_client", new_callable=AsyncMock) as mock_get_client, \
         patch("bridge_app.services.orchestrator.push_transaction", new_callable=AsyncMock) as mock_push:

        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "tx_mapped_123"

        result = await process_manual_transaction(
            manual_data=manual_data,
            db=mock_db,
            progress_callback=dummy_progress,
            force_override=False
        )

        assert result is not None
        assert result["merchant"] == "Starbucks Corp"
        assert result["original_merchant_name"] == "starbucks"
        assert result["category_name"] == "Coffee Shop"
