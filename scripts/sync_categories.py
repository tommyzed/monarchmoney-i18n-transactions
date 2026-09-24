import asyncio
import os
from sqlalchemy.future import select
from sqlalchemy import text
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
    "health": "🏥", "fitness": "💪", "gym": "🏋️", "medical": "🏥", "doctor": "👨‍⚕️",
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
        mm_email = os.getenv("MM_EMAIL")
        if mm_email:
            creds_result = await db.execute(select(Credentials).where(Credentials.email == mm_email))
        else:
            creds_result = await db.execute(select(Credentials).where(Credentials.monarch_cookies.isnot(None)))
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
            print(f"✅ Found {len(monarch_categories)} categories in Monarch.\n")
            
            # Build lookup of existing categories by monarch_category_id
            all_categories_result = await db.execute(select(Category))
            existing_by_id = {}
            existing_by_name = {}
            for cat in all_categories_result.scalars():
                if cat.monarch_category_id:
                    existing_by_id[cat.monarch_category_id] = cat
                existing_by_name[cat.category_name] = cat

            count_renamed = 0
            count_new = 0
            count_unchanged = 0
            
            for m_cat in monarch_categories:
                m_name = m_cat['name']
                m_id = m_cat['id']
                
                existing = existing_by_id.get(m_id)
                
                if existing:
                    if existing.category_name != m_name:
                        # Category was renamed in Monarch.
                        # Since category_name is the PK, we need to:
                        # 1. Remember the old values we want to preserve
                        # 2. Delete the old row
                        # 3. Insert a new row with the new name, preserving is_hidden and emoji
                        old_name = existing.category_name
                        old_emoji = existing.category_emoji
                        old_hidden = existing.is_hidden
                        
                        # Delete the old row via raw SQL to avoid PK issues
                        await db.execute(
                            text("DELETE FROM categories WHERE category_name = :name"),
                            {"name": old_name}
                        )
                        
                        # Insert with new name, preserving is_hidden and emoji
                        new_cat = Category(
                            category_name=m_name,
                            monarch_category_id=m_id,
                            category_emoji=old_emoji or guess_emoji(m_name),
                            is_hidden=old_hidden
                        )
                        db.add(new_cat)
                        
                        print(f"  📝 RENAMED: \"{old_name}\" → \"{m_name}\"")
                        count_renamed += 1
                    else:
                        count_unchanged += 1
                else:
                    # Check if a row exists with this name but no monarch_category_id
                    name_match = existing_by_name.get(m_name)
                    if name_match and not name_match.monarch_category_id:
                        # Link the existing row to Monarch
                        name_match.monarch_category_id = m_id
                        if not name_match.category_emoji:
                            name_match.category_emoji = guess_emoji(m_name)
                        print(f"  🔗 LINKED: \"{m_name}\" → {m_id}")
                        count_renamed += 1
                    else:
                        # Brand new category
                        new_cat = Category(
                            category_name=m_name,
                            monarch_category_id=m_id,
                            category_emoji=guess_emoji(m_name),
                            is_hidden=False
                        )
                        db.add(new_cat)
                        print(f"  ✨ NEW: \"{m_name}\"")
                        count_new += 1
            
            await db.commit()
            print(f"\n✅ Sync Complete!")
            print(f"   Renamed:   {count_renamed}")
            print(f"   New:       {count_new}")
            print(f"   Unchanged: {count_unchanged}")
            
        except Exception as e:
            print(f"❌ Error syncing categories: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(sync_categories())
