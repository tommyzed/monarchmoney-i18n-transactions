import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from bridge_app.services.orchestrator import _process_transaction_data
# from bridge_app.models import Category # Importing models might need DB connection context or cause issues if not careful, but usually okay for class def

async def test_orchestrator_emoji_lookup():
    print("Testing orchestrator category emoji lookup...")

    # Mock DB Session
    mock_db = AsyncMock()

    # Mock DB Credentials query result
    mock_creds = MagicMock()
    mock_creds.id = 1
    
    # Mock Category query result
    mock_category = MagicMock()
    mock_category.category_emoji = "🍔"
    # To avoid weirdness with merchant mapping also returning this mock (and overwriting merchant name), 
    # we can set monarch_merchant_name on it too, just in case mapping check hits it.
    mock_category.monarch_merchant_name = "Mock Merchant"
    mock_category.category_id = "cat_123"
    mock_category.category_name = "Food & Dining"

    # Universal Mock Result for db.execute
    # Handles .scalars().first() -> Credentials
    # Handles .scalar_one_or_none() -> Category / Mapping / Duplicate
    universal_mock_result = MagicMock()
    universal_mock_result.scalars.return_value.first.return_value = mock_creds
    universal_mock_result.scalar_one_or_none.return_value = mock_category
    
    # Wire it up
    mock_db.execute.return_value = universal_mock_result

    # Mock report function
    async def mock_report(msg, percent=None):
        pass

    # Patch get_monarch_client and push_transaction
    # We patch them where they are IMPORTED in orchestrator.py
    with patch('bridge_app.services.orchestrator.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.orchestrator.push_transaction', new_callable=AsyncMock) as mock_push:
        
        mock_get_client.return_value = MagicMock() # Mock mm client
        mock_push.return_value = "tx_123"
        
        # Test Data
        data = {
            "date": "2023-11-01",
            "amount": 10.0, 
            "currency": "USD",
            "merchant": "Burger King",
            "category_name": "Food & Dining"
        }
        
        # Run
        # force_override=True to skip duplicate check (which might return our universal mock and say duplicate found)
        # Actually, if duplicate check returns `mock_category`, it evaluates to True. 
        # So orchestrator thinks "duplicate found" and returns early!
        # force_override=True bypasses this check.
        
        print("Running _process_transaction_data with force_override=True...")
        result = await _process_transaction_data(data, "hash123", mock_db, mock_report, force_override=True)
        
        # Verify
        print(f"Result Data: {result}")
        
        if result.get("category_emoji") == "🍔":
            print("✅ SUCCESS: Emoji found in result data")
        else:
            print(f"❌ FAILURE: Expected emoji '🍔', got '{result.get('category_emoji')}'")

if __name__ == "__main__":
    asyncio.run(test_orchestrator_emoji_lookup())
