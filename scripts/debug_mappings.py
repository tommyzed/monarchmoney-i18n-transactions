import asyncio
import sys
from sqlalchemy import select, func
from bridge_app.database import get_db, engine, Base
from bridge_app.models import MerchantMapping

async def check_mappings(search_term=None):
    async for session in get_db():
        if search_term:
            print(f"Searching for mappings matching: '{search_term}'")
            # clean search term
            term = search_term.strip().lower()
            
            stmt = select(MerchantMapping).where(func.lower(MerchantMapping.receipt_merchant_name) == term)
            result = await session.execute(stmt)
            match = result.scalar_one_or_none()
            
            if match:
                print(f"✅ MATCH FOUND!")
                print(f"  Input: '{search_term}'")
                print(f"  Mapping: '{match.receipt_merchant_name}' -> '{match.monarch_merchant_name}' (Cat: {match.category_id})")
            else:
                print(f"❌ NO MATCH FOUND for '{search_term}'")
                print("Note: The match must be exact (case-insensitive). Check for symbols or extra spaces.")
                
        else:
            print("Listing ALL Mappings:")
            stmt = select(MerchantMapping)
            result = await session.execute(stmt)
            mappings = result.scalars().all()
            
            if not mappings:
                print("  (No mappings found in database)")
            
            for m in mappings:
                print(f"  - ID {m.id}: '{m.receipt_merchant_name}' -> '{m.monarch_merchant_name}' (Cat: {m.category_id})")

if __name__ == "__main__":
    import sys
    term = sys.argv[1] if len(sys.argv) > 1 else None
    
    # Ensure tables exist just in case
    # asyncio.run(migrate()) # We skip this, assume app ran it.
    
    asyncio.run(check_mappings(term))
