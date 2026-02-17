import asyncio
import os
from unittest.mock import MagicMock, AsyncMock
from bridge_app.services.monarch import push_transaction

async def test_push_transaction_category():
    print("Testing push_transaction category logic...")
    
    # Mock MonarchMoney client
    mm = MagicMock()
    
    # Mock get_accounts
    mm.get_accounts = AsyncMock(return_value={
        "accounts": [{"id": "acc123", "displayName": "Euro Transactions"}]
    })
    
    # Mock get_transaction_categories
    mm.get_transaction_categories = AsyncMock(return_value={
        "categories": [
            {"id": "cat_uncategorized", "name": "Uncategorized"},
            {"id": "cat_food", "name": "Food"}
        ]
    })
    
    # Mock create_transaction
    mm.create_transaction = AsyncMock(return_value={
        "createTransaction": {"transaction": {"id": "tx123"}}
    })
    
    # Mock other methods to avoid errors
    mm.update_transaction = AsyncMock()
    mm.get_transaction_tags = AsyncMock(return_value={"householdTransactionTags": []})
    mm.create_transaction_tag = AsyncMock(return_value={"createTransactionTag": {"tag": {"id": "tag1"}}})
    mm.set_transaction_tags = AsyncMock()

    # Case 1: Category ID provided in data
    print("Case 1: Explicit Category ID")
    data_with_cat = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Test Merchant",
        "category_id": "cat_custom_123"
    }
    
    # Set env var for account match
    os.environ["MM_ACCOUNT"] = "Euro Transactions"
    
    await push_transaction(mm, data_with_cat)
    
    # Check arguments called
    call_args = mm.create_transaction.call_args[1]
    print(f"Called with category_id: {call_args['category_id']}")
    
    if call_args['category_id'] == "cat_custom_123":
        print("✅ SUCCESS: Used provided category_id")
    else:
        print(f"❌ FAILURE: Expected 'cat_custom_123', got '{call_args['category_id']}'")

    # Case 2: No Category ID provided (Fallback)
    print("\nCase 2: No Category ID (Fallback)")
    data_no_cat = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Test Merchant"
        # No category_id
    }
    
    await push_transaction(mm, data_no_cat)
    
    # Check arguments
    call_args = mm.create_transaction.call_args[1]
    print(f"Called with category_id: {call_args['category_id']}")
    
    if call_args['category_id'] == "cat_uncategorized":
        print("✅ SUCCESS: Used fallback 'Uncategorized'")
    else:
        print(f"❌ FAILURE: Expected 'cat_uncategorized', got '{call_args['category_id']}'")

if __name__ == "__main__":
    asyncio.run(test_push_transaction_category())
