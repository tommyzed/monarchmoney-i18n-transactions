import asyncio
from sqlalchemy import select, delete
from bridge_app.database import get_db, engine, Base
from bridge_app.models import MerchantMapping, Transaction, Credentials
from bridge_app.services.orchestrator import _process_transaction_data

# Mock report function
async def mock_report(msg, percent=None):
    print(f"Mock Report: {msg} ({percent}%)")

async def test_auto_mapping():
    # Setup: Create tables in test DB
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for session in get_db():
        # Insert test mapping
        try:
            mapping = MerchantMapping(
                receipt_merchant_name="test cafe",
                monarch_merchant_name="Official Test Cafe",
                category_name="Food & Dining"
            )
            session.add(mapping)
            await session.commit()
        except Exception:
            await session.rollback()

        # Test Data
        data = {
            "date": "2026-10-27",
            "amount": 10.00,
            "currency": "USD",
            "merchant": "test cafe" # Lowercase to test case-insensitivity
        }
        image_hash = "test_hash_12345"

        try:
            print("\nRunning _process_transaction_data...")
            creds = Credentials(email="test@test.com", encrypted_payload=b"123")
            session.add(creds)
            try:
                await session.commit()
            except Exception:
                await session.rollback()

            try:
                await _process_transaction_data(data, image_hash, session, mock_report)
            except Exception as e:
                print(f"Caught expected exception during processing (likely Monarch connection): {e}")

            # Verify Data modification
            print(f"\nFinal Data Merchant: {data.get('merchant')}")
            print(f"Final Data Category: {data.get('category_name')}")

            if data.get("merchant") == "Official Test Cafe" and data.get("category_name") == "Food & Dining":
                print("SUCCESS: Mapping applied correctly!")
            else:
                print("FAILURE: Mapping NOT applied.")

        finally:
            # Cleanup test DB
            await session.execute(delete(MerchantMapping).where(MerchantMapping.receipt_merchant_name == "test cafe"))
            await session.execute(delete(Transaction).where(Transaction.image_hash.startswith(image_hash)))
            await session.execute(delete(Credentials).where(Credentials.email == "test@test.com"))
            await session.commit()

if __name__ == "__main__":
    asyncio.run(test_auto_mapping())
