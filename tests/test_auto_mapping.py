import asyncio
from sqlalchemy import select
from bridge_app.database import get_db, engine, Base
from bridge_app.models import MerchantMapping, Transaction
from bridge_app.services.orchestrator import _process_transaction_data

# Mock report function
async def mock_report(msg, percent=None):
    print(f"Mock Report: {msg} ({percent}%)")

async def test_auto_mapping():
    # Setup: Create a mapping
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async for session in get_db():
        # Clean up previous tests
        await session.execute(select(MerchantMapping).where(MerchantMapping.receipt_merchant_name == "Test Cafe"))
        # await session.delete(existing) if we had one, but let's just use a fresh or ensure cleanup.
        # Simpler: just try to insert, if fails, we know it exists.
        
        # Insert test mapping
        try:
            mapping = MerchantMapping(
                receipt_merchant_name="Test Cafe",
                monarch_merchant_name="Official Test Cafe",
                category_id="12345678"
            )
            session.add(mapping)
            await session.commit()
        except:
            await session.rollback()
            # Assuming it exists, let's just proceed or fetch it to be sure
            pass

        # Test Data
        data = {
            "date": "2026-10-27",
            "amount": 10.00,
            "currency": "USD",
            "merchant": "test cafe" # Lowercase to test case-insensitivity
        }
        image_hash = "test_hash_12345"

        try:
            # Run the processing logic
            print("\nRunning _process_transaction_data...")
            # We mock DB and report_func. 
            # Note: _process_transaction_data commits to DB, so we should clean up created transaction later.
            
            # We need to mock push_transaction inside orchestrator or handle the error.
            # Since we can't easily mock the import inside the function without patching, 
            # and we don't want to make real network calls or need Credentials...
            # Actually, `_process_transaction_data` tries to fetch Credentials. 
            # If no credentials, it raises HTTPException 400.
            
            # Let's insert dummy credentials first.
             # Insert test mapping
            from bridge_app.models import Credentials
            try:
                creds = Credentials(email="test@test.com", encrypted_payload=b"123")
                session.add(creds)
                await session.commit()
            except:
                await session.rollback()

            # Now run
            # It will fail at push_transaction likely, but the mapping happens BEFORE that.
            # We can inspect `data` dictionary to see if it was modified.
            
            # Use a dummy DB session that we can inspect? 
            # Actually, the mapping logic is:
            # 1. Check duplicates
            # 2. Auto-mapping Check (THIS IS WHAT WE WANT)
            # 3. Currency
            # 4. Monarch Push (This will fail)
            
            try:
                await _process_transaction_data(data, image_hash, session, mock_report)
            except Exception as e:
                print(f"Caught expected exception during processing (likely Monarch connection): {e}")

            # Verify Data modification
            print(f"\nFinal Data Merchant: {data.get('merchant')}")
            print(f"Final Data Category: {data.get('category_id')}")

            if data.get("merchant") == "Official Test Cafe" and data.get("category_id") == "12345678":
                print("SUCCESS: Mapping applied correctly!")
            else:
                print("FAILURE: Mapping NOT applied.")

        finally:
            # Cleanup
            # delete mapping
            await session.execute(select(MerchantMapping).where(MerchantMapping.receipt_merchant_name == "Test Cafe"))
            # delete transaction if created
            await session.execute(select(Transaction).where(Transaction.image_hash == image_hash))
            await session.commit()

if __name__ == "__main__":
    asyncio.run(test_auto_mapping())
