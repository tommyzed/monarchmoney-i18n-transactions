import asyncio
from sqlalchemy import select
from bridge_app.database import get_db
from bridge_app.models import MerchantMapping
from bridge_app.services.orchestrator import process_manual_transaction

# Mock report function
async def mock_report(msg, percent=None):
    print(f"Mock Report: {msg} ({percent}%)")

async def test_manual_mapping():
    print("Starting manual mapping test...")
    
    async for session in get_db():
        # Check existing mappings
        stmt = select(MerchantMapping).limit(1)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            print(f"Found existing mapping: '{existing.receipt_merchant_name}' -> '{existing.monarch_merchant_name}'")
            receipt_name = existing.receipt_merchant_name
            target_name = existing.monarch_merchant_name
            target_cat = existing.category_id
        else:
            print("No existing mappings found. Attempting to insert one...")
            try:
                # Try to insert with a random name to avoid unique constraint on name
                # But ID collision is the issue...
                # Let's try to reset sequence? No. 
                # Let's just try to insert.
                cart_name = "Manual Coffee Test"
                mapping = MerchantMapping(
                    receipt_merchant_name=cart_name,
                    monarch_merchant_name="Starbucks Test",
                    category_id="99999"
                )
                session.add(mapping)
                await session.commit()
                receipt_name = cart_name
                target_name = "Starbucks Test"
                target_cat = "99999"
            except Exception as e:
                print(f"Insert failed: {e}")
                print("Cannot proceed with test without data.")
                return

        # Test Data
        manual_data = {
            "amount": 5.50,
            "currency": "USD",
            "date": "2026-11-01",
            "merchant": receipt_name.lower() # lowercase to test case-insensitivity
        }

        print(f"Processing manual data: {manual_data}")

        try:
            try:
                result = await process_manual_transaction(manual_data, session, mock_report)
                print("Process completed.")
            except Exception as e:
                print(f"Process ended (possibly expected error): {e}")

            # Verify Data modification in manual_data dict
            print(f"Final Info: Merchant='{manual_data.get('merchant')}' Category='{manual_data.get('category_id')}'")

            if manual_data.get("merchant") == target_name:
                print("SUCCESS: Manual mapping applied!")
            else:
                print(f"FAILURE: Manual mapping NOT applied. Expected '{target_name}', got '{manual_data.get('merchant')}'")

        finally:
            pass

if __name__ == "__main__":
    asyncio.run(test_manual_mapping())
