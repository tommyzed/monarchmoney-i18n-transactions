import asyncio
from bridge_app.database import AsyncSessionLocal
from bridge_app.models import Category
from bridge_app.main import app
from httpx import AsyncClient, ASGITransport

import hashlib
import os

# Set SECRET before import ideally, but we will run with env var
# We need to know the secret to make the cookie
SECRET = os.environ.get("UNLOCK_SECRET", "test")

async def test_hidden_categories():
    print(f"Testing Hidden Categories Logic with SECRET={SECRET}...")
    
    async with AsyncSessionLocal() as db:
        # 1. Create a visible and a hidden category
        visible_cat = Category(category_name="Visible Cat", category_emoji="👀", is_hidden=False)
        hidden_cat = Category(category_name="Hidden Cat", category_emoji="🙈", is_hidden=True)
        
        # Merge to handle existing
        await db.merge(visible_cat)
        await db.merge(hidden_cat)
        await db.commit()
        
    # 2. Query the API
    transport = ASGITransport(app=app)
    
    # Calculate cookie
    cookie_value = hashlib.sha256(SECRET.encode()).hexdigest()
    cookies = {"device_token": cookie_value}
    
    async with AsyncClient(transport=transport, base_url="http://test", cookies=cookies) as ac:
        response = await ac.get("/api/categories")
        if response.status_code != 200:
             print(f"❌ API Error: {response.status_code} {response.text}")
             return

        data = response.json()
        categories = data.get("categories", [])
        
        # 3. Verify
        found_visible = any(c['name'] == "Visible Cat" for c in categories)
        found_hidden = any(c['name'] == "Hidden Cat" for c in categories)
        
        if found_visible and not found_hidden:
            print("✅ SUCCESS: Visible category found, Hidden category NOT found.")
        else:
            print("❌ FAILURE:")
            print(f" - Found Visible: {found_visible}")
            print(f" - Found Hidden: {found_hidden}")

if __name__ == "__main__":
    asyncio.run(test_hidden_categories())
