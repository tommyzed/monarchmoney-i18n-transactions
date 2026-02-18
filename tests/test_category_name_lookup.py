import asyncio
import os
from unittest.mock import MagicMock, AsyncMock
from bridge_app.services.monarch import push_transaction

async def test_category_name_lookup():
    print("Testing push_transaction category name lookup...")
    
    # Mock MonarchMoney client
    mm = MagicMock()
    
    # Mock get_accounts
    mm.get_accounts = AsyncMock(return_value={
        "accounts": [{"id": "acc123", "displayName": "Euro Transactions"}]
    })
    
    # Mock get_transaction_categories
    mm.get_transaction_categories = AsyncMock(return_value={
        "categories": [
            {"id": "cat_food_id", "name": "Food & Dining"},
            {"id": "cat_transport_id", "name": "Transportation"},
            {"id": "cat_uncat_id", "name": "Uncategorized"}
        ]
    })
    
    # Mock create_transaction
    mm.create_transaction = AsyncMock(return_value={
        "createTransaction": {"transaction": {"id": "tx123"}}
    })
    
    # Mock other methods
    mm.update_transaction = AsyncMock()
    mm.get_transaction_tags = AsyncMock(return_value={"householdTransactionTags": []})
    mm.create_transaction_tag = AsyncMock(return_value={"createTransactionTag": {"tag": {"id": "tag1"}}})
    mm.set_transaction_tags = AsyncMock()

    # Set env var for account match
    os.environ["MM_ACCOUNT"] = "Euro Transactions"

    # Case 1: Category Name provided and matches
    print("\nCase 1: Category Name 'Food & Dining' -> Should map to 'cat_food_id'")
    data_1 = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Restaurant",
        "category_name": "Food & Dining"
    }
    
    await push_transaction(mm, data_1)
    call_args_1 = mm.create_transaction.call_args[1]
    print(f"Result: {call_args_1['category_id']}")
    
    if call_args_1['category_id'] == "cat_food_id":
        print("✅ SUCCESS: Category ID match")
    else:
        print(f"❌ FAILURE: Expected 'cat_food_id'")
        
    if data_1.get('category_name') == "Food & Dining":
        print("✅ SUCCESS: Data dictionary updated with category name")
    else:
        print(f"❌ FAILURE: Data dictionary missing category name. Got: {data_1.get('category_name')}")

    # Case 2: Category Name case-insensitive match
    print("\nCase 2: Category Name 'transportation' (lower) -> Should map to 'cat_transport_id'")
    data_2 = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Uber",
        "category_name": "transportation"
    }
    
    await push_transaction(mm, data_2)
    call_args_2 = mm.create_transaction.call_args[1]
    print(f"Result: {call_args_2['category_id']}")

    if call_args_2['category_id'] == "cat_transport_id":
        print("✅ SUCCESS")
    else:
        print(f"❌ FAILURE: Expected 'cat_transport_id'")

    # Case 3: Category Name not found -> Fallback to ID
    print("\nCase 3: Category Name 'Invalid' but valid ID provided -> Should map to 'cat_fallback_id'")
    data_3 = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Unknown",
        "category_name": "Invalid Category",
        "category_id": "cat_fallback_id"
    }
    
    await push_transaction(mm, data_3)
    call_args_3 = mm.create_transaction.call_args[1]
    print(f"Result: {call_args_3['category_id']}")

    if call_args_3['category_id'] == "cat_fallback_id":
        print("✅ SUCCESS")
    else:
        print(f"❌ FAILURE: Expected 'cat_fallback_id'")

    # Case 4: Category Name not found, No ID -> Fallback to Uncategorized
    print("\nCase 4: Category Name 'Invalid', No ID -> Should map to 'cat_uncat_id'")
    data_4 = {
        "date": "2023-11-01",
        "amount": 10.0,
        "currency": "USD",
        "merchant": "Unknown",
        "category_name": "Invalid Category"
    }
    
    await push_transaction(mm, data_4)
    call_args_4 = mm.create_transaction.call_args[1]
    print(f"Result: {call_args_4['category_id']}")

    if call_args_4['category_id'] == "cat_uncat_id":
        print("✅ SUCCESS: Fallback ID match")
    else:
        print(f"❌ FAILURE: Expected 'cat_uncat_id'")
        
    if data_4.get('category_name') == "Uncategorized":
        print("✅ SUCCESS: Data dictionary updated with fallback category name")
    else:
        print(f"❌ FAILURE: Data dictionary missing fallback category name. Got: {data_4.get('category_name')}")

if __name__ == "__main__":
    asyncio.run(test_category_name_lookup())
