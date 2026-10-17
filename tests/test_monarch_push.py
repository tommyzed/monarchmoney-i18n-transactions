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

import datetime

async def test_push_transaction_cash():
    print("\nTesting push_transaction cash routing logic...")
    
    # Mock MonarchMoney client
    mm = MagicMock()
    
    # Mock get_accounts
    mm.get_accounts = AsyncMock(return_value={
        "accounts": [
            {"id": "acc_standard", "displayName": "Euro Transactions"},
            {"id": "acc_cash_usd_123", "displayName": "Cash USD Account"},
            {"id": "acc_cash_eur_456", "displayName": "Cash EUR Account"}
        ]
    })
    mm.get_transaction_categories = AsyncMock(return_value={
        "categories": [{"id": "cat_uncategorized", "name": "Uncategorized"}]
    })
    mm.create_transaction = AsyncMock(return_value={
        "createTransaction": {"transaction": {"id": "tx123"}}
    })
    mm.update_transaction = AsyncMock()
    mm.get_transaction_tags = AsyncMock(return_value={"householdTransactionTags": []})
    mm.create_transaction_tag = AsyncMock(return_value={"createTransactionTag": {"tag": {"id": "tag1"}}})
    mm.set_transaction_tags = AsyncMock()

    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # Clean up any preexisting env vars
    for k in ["MM_ACCOUNT_CASH", "MM_ACCOUNT_CASH_USD", "MM_ACCOUNT_CASH_EUR"]:
        if k in os.environ:
            del os.environ[k]

    # Case 1: is_cash is True, currency is USD -> routes to MM_ACCOUNT_CASH_USD
    print("Case 1: is_cash is True, currency is USD -> MM_ACCOUNT_CASH_USD")
    data_cash_usd = {
        "date": today_str,
        "amount": 15.0,
        "currency": "USD",
        "merchant": "Cash USD Merchant",
        "is_cash": True
    }
    os.environ["MM_ACCOUNT_CASH_USD"] = "acc_cash_usd_123"
    await push_transaction(mm, data_cash_usd)
    call_args = mm.create_transaction.call_args[1]
    assert call_args['account_id'] == "acc_cash_usd_123"
    assert call_args['date'] == today_str
    print("✅ SUCCESS: Routed to MM_ACCOUNT_CASH_USD successfully")

    # Case 2: is_cash is True, currency is EUR -> routes to MM_ACCOUNT_CASH_EUR
    print("Case 2: is_cash is True, currency is EUR -> MM_ACCOUNT_CASH_EUR")
    data_cash_eur = {
        "date": today_str,
        "amount": 20.0,
        "currency": "EUR",
        "merchant": "Cash EUR Merchant",
        "is_cash": True
    }
    os.environ["MM_ACCOUNT_CASH_EUR"] = "acc_cash_eur_456"
    await push_transaction(mm, data_cash_eur)
    call_args = mm.create_transaction.call_args[1]
    assert call_args['account_id'] == "acc_cash_eur_456"
    assert call_args['date'] == today_str
    print("✅ SUCCESS: Routed to MM_ACCOUNT_CASH_EUR successfully")

    # Case 3: is_cash is True, converted currency (original_currency="EUR", currency="USD")
    print("Case 3: is_cash is True, original_currency EUR -> MM_ACCOUNT_CASH_EUR")
    data_cash_converted = {
        "date": today_str,
        "amount": 22.0,
        "currency": "USD",
        "original_currency": "EUR",
        "original_amount": 20.0,
        "merchant": "Converted EUR Merchant",
        "is_cash": True
    }
    await push_transaction(mm, data_cash_converted)
    call_args = mm.create_transaction.call_args[1]
    assert call_args['account_id'] == "acc_cash_eur_456"
    print("✅ SUCCESS: Converted transaction routed to MM_ACCOUNT_CASH_EUR")

    # Case 4: is_cash is True, expected environment variable is NOT set
    print("Case 4: is_cash is True, env var not set")
    del os.environ["MM_ACCOUNT_CASH_USD"]
    try:
        await push_transaction(mm, data_cash_usd)
        print("❌ FAILURE: Expected ValueError when MM_ACCOUNT_CASH_USD is not set")
        assert False
    except ValueError as e:
        assert "MM_ACCOUNT_CASH_USD environment variable is not set" in str(e)
        print("✅ SUCCESS: Raised ValueError when MM_ACCOUNT_CASH_USD is not set")

    # Case 5: is_cash is True, cash account is set but not found in accounts
    print("Case 5: is_cash is True, cash account does not match")
    os.environ["MM_ACCOUNT_CASH_USD"] = "nonexistent_id"
    try:
        await push_transaction(mm, data_cash_usd)
        print("❌ FAILURE: Expected ValueError when cash account is not found")
        assert False
    except ValueError as e:
        assert "No account found with ID" in str(e)
        print("✅ SUCCESS: Raised ValueError when cash account is not found")

    # Case 6: is_cash is False, falls back to display name MM_ACCOUNT
    print("Case 6: is_cash is False, falls back to display name")
    data_non_cash = {
        "date": today_str,
        "amount": 15.0,
        "currency": "USD",
        "merchant": "Standard Merchant",
        "is_cash": False
    }
    os.environ["MM_ACCOUNT"] = "Euro Transactions"
    await push_transaction(mm, data_non_cash)
    call_args = mm.create_transaction.call_args[1]
    assert call_args['account_id'] == "acc_standard"
    assert call_args['date'] == today_str
    print("✅ SUCCESS: Fallback to standard displayName matching worked")

if __name__ == "__main__":
    async def main():
        await test_push_transaction_category()
        await test_push_transaction_notes()
        await test_push_transaction_cash()
    asyncio.run(main())
