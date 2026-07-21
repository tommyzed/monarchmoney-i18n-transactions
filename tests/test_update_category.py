import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi import HTTPException
from sqlalchemy import select
from bridge_app.main import update_transaction_category, UpdateCategoryRequest
from bridge_app.models import Credentials, Category, Transaction

@pytest.mark.asyncio
async def test_update_transaction_category_success():
    # Mock Request
    req = UpdateCategoryRequest(
        monarch_tx_id="tx_9999",
        category_name="Dining Out"
    )

    # Mock DB session
    db = AsyncMock()

    # Setup mocked query results:
    # 1. Credentials
    mock_creds = Credentials(id=1, email="test@test.com", encrypted_payload=b"payload")

    # 2. Category
    mock_category = Category(
        category_name="Dining Out",
        monarch_category_id="cat_dining",
        category_emoji="🍔"
    )

    # 3. Transaction
    mock_transaction = Transaction(
        id=123,
        image_hash="abc",
        parsed_data={"monarch_tx_id": "tx_9999", "category_name": "Old Category"}
    )

    # We need to simulate the execution of select statements in SQLAlchemy.
    # update_transaction_category executes select statements sequentially:
    #   - select(Credentials)
    #   - select(Category)
    #   - select(Transaction).where(...)

    # We mock execute to return proper result objects based on the SQL query executed.
    async def mock_execute(stmt):
        stmt_str = str(stmt)
        result = MagicMock()
        if "credentials" in stmt_str:
            result.scalars.return_value.first.return_value = mock_creds
        elif "categories" in stmt_str:
            result.scalar_one_or_none.return_value = mock_category
        elif "transactions" in stmt_str:
            result.scalar_one_or_none.return_value = mock_transaction
        return result

    db.execute = mock_execute

    # Mock the get_monarch_client helper
    mock_mm = AsyncMock()
    mock_mm.update_transaction = AsyncMock()

    with patch("bridge_app.main.get_monarch_client", return_value=mock_mm) as mock_get_client:
        response = await update_transaction_category(req, db=db)

        # Verify get_monarch_client called with correctly mapped ID
        mock_get_client.assert_called_once_with(db, 1)

        # Verify update_transaction on monarch client was called with expected IDs
        mock_mm.update_transaction.assert_called_once_with(
            transaction_id="tx_9999",
            category_id="cat_dining"
        )

        # Verify response returned success and emoji
        assert response == {"status": "success", "category_emoji": "🍔"}

        # Verify transaction's parsed_data was updated locally
        assert mock_transaction.parsed_data["category_name"] == "Dining Out"
        assert mock_transaction.parsed_data["category_emoji"] == "🍔"

        # Verify commit was called on db
        db.commit.assert_awaited_once()
