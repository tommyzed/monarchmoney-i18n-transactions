import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

# Create a mock MonarchMoney class
class MockMonarchMoney:
    def __init__(self):
        self.get_accounts = AsyncMock(return_value={"accounts": [{"displayName": "Euro Transactions", "id": "acc_1"}]})
        self.get_transaction_categories = AsyncMock(return_value={"categories": [{"name": "Uncategorized", "id": "cat_1"}]})
        self.create_transaction = AsyncMock(return_value={"createTransaction": {"transaction": {"id": "tx_1"}}})
        self.update_transaction = AsyncMock()
        self.get_transaction_tags = AsyncMock(return_value={"householdTransactionTags": [{"name": "Imported by MM Bridge", "id": "tag_1"}, {"name": "Cash", "id": "tag_2"}]})
        self.create_transaction_tag = AsyncMock()
        self.set_transaction_tags = AsyncMock()

from bridge_app.services.monarch import push_transaction
import os

os.environ["MM_ACCOUNT"] = "Euro Transactions"

async def main():
    mm = MockMonarchMoney()

    # We want to measure the time taken to run push_transaction 100 times.
    # To simulate the API delay, let's add a small sleep to get_transaction_tags mock.
    async def mock_get_tags():
        await asyncio.sleep(0.05) # 50ms delay per API call
        return {"householdTransactionTags": [{"name": "Imported by MM Bridge", "id": "tag_1"}, {"name": "Cash", "id": "tag_2"}]}
    mm.get_transaction_tags = mock_get_tags

    data = {
        "date": "2023-10-01",
        "amount": 10.0,
        "currency": "EUR",
        "merchant": "Test Merchant",
        "is_credit": False,
        "is_cash": True
    }

    start = time.time()
    for _ in range(50):
        await push_transaction(mm, data)
    end = time.time()

    print(f"Time taken for 50 transactions: {end - start:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
