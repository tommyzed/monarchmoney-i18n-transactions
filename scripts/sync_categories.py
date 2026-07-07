import asyncio
import os
from sqlalchemy.future import select
from sqlalchemy import update
from bridge_app.database import AsyncSessionLocal
from bridge_app.models import Credentials, Category
from bridge_app.services.monarch import get_monarch_client

# Heuristic Emoji Map
EMOJI_KEYWORDS = {
    "food": "🍔", "restaurant": "🍽️", "dining": "🍽️", "groceries": "🛒",
    "transport": "🚗", "gas": "⛽", "fuel": "⛽", "auto": "🚗", "parking": "🅿️",
    "travel": "✈️", "flight": "✈️", "airline": "✈️", "hotel": "🏨", "lodging": "🏨",
    "shopping": "🛍️", "clothing": "👕", "electronics": "📱", "home": "🏠",
    "utilities": "💡", "bill": "🧾", "internet": "🌐", "phone": "📱",
    "entertainment": "🎬", "movie": "🍿", "music": "🎵", "streaming": "📺",
    "health": "jq", "fitness": "💪", "gym": "🏋️", "medical": "medical_symbol", "doctor": "👨‍⚕️",
    "income": "💰", "salary": "💵", "paycheck": "💵",
    "transfer": "↔️", "payment": "💳",
    "uncategorized": "❓", "general": "📦"
}

def guess_emoji(name: str) -> str:
    name_lower = name.lower()
    for key, emoji in EMOJI_KEYWORDS.items():
        if key in name_lower:
            return emoji
    return "🏷️" # Default tag emoji

async def sync_categories():
    async with AsyncSessionLocal() as db:
        print("Fetching credentials...")
        import os
        mm_email = os.getenv("MM_EMAIL")
        if mm_email:
            creds_result = await db.execute(select(Credentials).where(Credentials.email == mm_email))
        else:
            creds_result = await db.execute(select(Credentials).where(Credentials.monarch_session.isnot(None)))
        creds = creds_result.scalars().first()
        
        if not creds:
             print("❌ No credentials found in DB. Please login first.")
             return
             
        try:
            print("Connecting to Monarch...")
            mm = await get_monarch_client(db, creds.id)
            
            print("Fetching categories from Monarch...")
            cat_data = await mm.get_transaction_categories()
            monarch_categories = cat_data.get('categories', [])
            print(f"✅ Found {len(monarch_categories)} categories in Monarch.")
            
            # Fetch all existing categories upfront to avoid N+1 queries
            all_categories_result = await db.execute(select(Category))
            existing_categories = {cat.category_name: cat for cat in all_categories_result.scalars()}

            count_new = 0
            count_updated = 0
            
            for m_cat in monarch_categories:
                name = m_cat['name']
                m_id = m_cat['id']
                emoji = guess_emoji(name)
                
                # Check if exists by Name
                existing = existing_categories.get(name)
                
                if existing:
                    # Update ID if missing
                    # Also update emoji if missing? Or overwrite? 
                    # User said "I will manually edit any that I disagree with."
                    # So maybe we only set emoji if it's currently null?
                    # But the requirement implies populate. Let's overwrite for now, user can edit.
                    # Actually, if we overwrite every run, user edits are lost.
                    # Best approach: Only set emoji if existing is None or empty.
                    # Always update monarch_category_id.
                    
                    changes = False
                    if existing.monarch_category_id != m_id:
                        existing.monarch_category_id = m_id
                        changes = True
                    
                    if not existing.category_emoji:
                         existing.category_emoji = emoji
                         changes = True
                         
                    if changes:
                        count_updated += 1
                else:
                    # Create New
                    new_cat = Category(
                        category_name=name,
                        monarch_category_id=m_id,
                        category_emoji=emoji
                    )
                    db.add(new_cat)
                    count_new += 1
            
            await db.commit()
            print(f"Sync Complete! New: {count_new}, Updated: {count_updated}")
            
        except Exception as e:
            print(f"❌ Error syncing categories: {e}")

if __name__ == "__main__":
    asyncio.run(sync_categories())
