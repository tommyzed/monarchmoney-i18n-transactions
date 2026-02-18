import asyncio
import json
from httpx import AsyncClient, ASGITransport
from bridge_app.main import app
from bridge_app.database import AsyncSessionLocal
from bridge_app.models import MerchantMapping, Category
from sqlalchemy.future import select

import hashlib
import os

async def test_edit_mapping_endpoints():
    print("Testing Edit Mapping Endpoints...")
    
    # Calculate cookie for auth
    secret = "12342060006" # From debug output
    cookie_value = hashlib.sha256(secret.encode()).hexdigest()
    cookies = {"device_token": cookie_value}
    
    print(f"Using auth cookie: device_token={cookie_value[:5]}...")

    # 1. Setup Data
    # Ensure we have a category in local DB to test GET /api/categories (local path)
    async with AsyncSessionLocal() as db:
        new_cat = Category(category_name="Test Food", category_emoji="🥪")
        db.add(new_cat)
        try:
            await db.commit()
        except Exception:
            await db.rollback() # Might already exist
            
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as ac:
        
        # 2. Test GET /api/categories
        print("Testing GET /api/categories...")
        resp = await ac.get("/api/categories")
        if resp.status_code == 200:
            data = resp.json()
            cats = data.get("categories", [])
            print(f"✅ Users fetched: {len(cats)}")
            
            # Check for our test category
            found = any(c['name'] == "Test Food" and c['emoji'] == "🥪" for c in cats)
            if found:
                print("✅ Found 'Test Food' category")
            else:
                print("⚠️ 'Test Food' not found (did DB commit work or did it fallback to API?)")
        else:
            print(f"❌ Failed to fetch categories: {resp.status_code} {resp.text}")

        # 3. Test POST /api/mapping
        print("Testing POST /api/mapping...")
        payload = {
            "receipt_merchant_name": "Test Cafe",
            "monarch_merchant_name": "Official Test Cafe",
            "category_name": "Test Food"
        }
        
        resp = await ac.post("/api/mapping", json=payload)
        if resp.status_code == 200:
            print("✅ Mapping saved successfully")
        else:
            print(f"❌ Failed to save mapping: {resp.status_code} {resp.text}")
            
        # 4. Verify DB
        async with AsyncSessionLocal() as db:
            stmt = select(MerchantMapping).where(MerchantMapping.receipt_merchant_name == "Test Cafe")
            result = await db.execute(stmt)
            mapping = result.scalar_one_or_none()
            
            if mapping:
                if mapping.monarch_merchant_name == "Official Test Cafe" and mapping.category_name == "Test Food":
                    print("✅ DB Verification: Mapping correct")
                else:
                    print(f"❌ DB Verification Failed: Check values locally. Got {mapping.monarch_merchant_name}, {mapping.category_name}")
            else:
                print("❌ DB Verification Failed: Mapping not found")

if __name__ == "__main__":
    asyncio.run(test_edit_mapping_endpoints())
