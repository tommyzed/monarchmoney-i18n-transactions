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
        "date": "2026-11-01",
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
        "date": "2026-11-01",
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

async def test_push_transaction_notes():
    print("\nTesting push_transaction notes appending logic...")
    
    # Mock MonarchMoney client
    mm = MagicMock()
    mm.get_accounts = AsyncMock(return_value={
        "accounts": [{"id": "acc123", "displayName": "Euro Transactions"}]
    })
    mm.get_transaction_categories = AsyncMock(return_value={
        "categories": [{"id": "cat_uncategorized", "name": "Uncategorized"}]
    })
    mm.create_transaction = AsyncMock(return_value={
        "createTransaction": {"transaction": {"id": "tx123"}}
    })
    mm.update_transaction = MagicMock() # Mock as normal mock or AsyncMock
    mm.update_transaction = AsyncMock()
    mm.get_transaction_tags = AsyncMock(return_value={"householdTransactionTags": []})
    mm.create_transaction_tag = AsyncMock(return_value={"createTransactionTag": {"tag": {"id": "tag1"}}})
    mm.set_transaction_tags = AsyncMock()

    # Case 1: No notes in data
    print("Case 1: No notes")
    data_no_notes = {
        "date": "2026-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Test Merchant"
    }
    os.environ["MM_ACCOUNT"] = "Euro Transactions"
    await push_transaction(mm, data_no_notes)
    call_args = mm.create_transaction.call_args[1]
    print(f"Called with notes: {repr(call_args['notes'])}")
    assert "Original Price: USD 10.00" in call_args['notes']
    assert "Lunch with business client" not in call_args['notes']

    # Case 2: Notes in data (Manual Entry flow)
    print("Case 2: Notes present")
    data_with_notes = {
        "date": "2026-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Test Merchant",
        "notes": "Lunch with business client"
    }
    await push_transaction(mm, data_with_notes)
    call_args = mm.create_transaction.call_args[1]
    print(f"Called with notes: {repr(call_args['notes'])}")
    assert "Original Price: USD 10.00" in call_args['notes']
    assert "Lunch with business client" in call_args['notes']
    assert call_args['notes'] == "Original Price: USD 10.00\nLunch with business client"
    print("✅ SUCCESS: Appended user-entered notes to generated notes")

if __name__ == "__main__":
    async def main():
        await test_push_transaction_category()
        await test_push_transaction_notes()
    asyncio.run(main())
