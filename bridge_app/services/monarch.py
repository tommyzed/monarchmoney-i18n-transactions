import os
import asyncio
import pickle
import pyotp
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from monarchmoney import MonarchMoney, RequireMFAException
from ..models import Credentials
from ..utils.crypto import decrypt

# Path for session persistence - logic says store in DB, but library uses file.
# We will use DB to store/retrieve the session pickle bytes.

async def get_monarch_client(db: AsyncSession, user_id: int):
    # Fetch credentials
    creds = await db.get(Credentials, user_id)
    if not creds:
        raise ValueError("No credentials found for user")

    mm = MonarchMoney()
    
    # Try to load session from DB
    if creds.monarch_session:
        import tempfile
        try:
            # Create a temp file to hold the session pickle
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(creds.monarch_session)
                tmp_path = tmp.name
            
            # Load session using library method
            mm.load_session(tmp_path)
            
            # Clean up temp file
            os.unlink(tmp_path)
            
            # Verify session is valid
            await mm.get_subscription_details()
            return mm
            
        except Exception as e:
            print(f"Session load/Verify failed: {e}")
            # Fallthrough to error
            pass
    
    # If we get here, session is missing or invalid.
    # We do NOT allow headless login anymore per user request for manual flow.
    raise ValueError("Monarch session expired or missing. Please run 'python scripts/interactive_login.py' to login.")

async def push_transaction(mm: MonarchMoney, data: dict):
    # data: date, amount, currency, merchant
    # Find manual account
    accounts = await mm.get_accounts()
    # Logic to pick account
    # We look for a specific account named "Euro Transactions"
    target_account = None
    target_name = os.environ.get("MM_ACCOUNT", "Euro Transactions")
    
    for acc in accounts['accounts']:
        if acc['displayName'] == target_name:
             target_account = acc
             break
    
    if not target_account:
        raise ValueError(f"No account found with name '{target_name}'. Please create a new Manual account in Monarch named '{target_name}'.")

    # Determine amount sign: positive for credits, negative for expenses/debits
    parsed_amount = float(data['amount'])
    is_credit = data.get('is_credit', False)
    amount = abs(parsed_amount) if is_credit else -abs(parsed_amount)
    
    # Check for original currency conversion data
    # Check for original currency conversion data
    if "original_amount" in data:
        notes = (
            f"Original Price: {data['original_currency']} {data['original_amount']:.2f}\n"
            f"Exchange Rate: {data.get('exchange_rate', '?')} USD/{data['original_currency']}"
        )
    elif data['currency'] != 'USD':
        # Apply notes for non-USD that wasn't converted
        notes = f"Original Price: {data['currency']} {abs(amount):.2f}"
    else:
        # User requested redundancy for USD
        notes = f"Original Price: {data['currency']} {abs(amount):.2f}"

    if data.get('is_cash'):
        notes += "\n#cash"

    # Fetch categories to find a valid category_id (required by API)
    category_id = None
    target_category_name = data.get('category_name')
    fallback_category_id = data.get('category_id')

    try:
        categories_data = await mm.get_transaction_categories()
        categories = categories_data.get('categories', [])
        
        # 1. Try to match by Name if provided
        if target_category_name:
            for cat in categories:
                if cat['name'].lower() == target_category_name.lower():
                    category_id = cat['id']
                    data['category_name'] = cat['name'] # Update with official name (case)
                    print(f"Mapped category name '{target_category_name}' to ID '{category_id}'")
                    break
            if not category_id:
                print(f"Warning: Category name '{target_category_name}' not found in Monarch.")
        
        # 2. Fallback to provided ID if Name lookup failed or wasn't provided
        if not category_id and fallback_category_id:
            category_id = fallback_category_id
            # Try to find name for this ID
            for cat in categories:
                if cat['id'] == category_id:
                    data['category_name'] = cat['name']
                    break
        
        # 3. Fallback to "Uncategorized"
        if not category_id:
            for cat in categories:
                if cat['name'] == 'Uncategorized':
                    category_id = cat['id']
                    data['category_name'] = cat['name']
                    break
        
        # 4. Fallback to first available
        if not category_id and categories:
             category_id = categories[0]['id']
             data['category_name'] = categories[0]['name']
             print(f"Warning: 'Uncategorized' and provided mapping not found. Using fallback: {categories[0]['name']}")

    except Exception as e:
        print(f"Failed to fetch/resolve categories: {e}")
        # If we have a hard ID from mapping, we might still try to use it even if fetch failed?
        if fallback_category_id:
             category_id = fallback_category_id
             # We can't know the name if fetch failed, unless we trust what was passed (if any)
             if not data.get('category_name'):
                 data['category_name'] = "Unknown"

    if not category_id:
         raise ValueError("Could not determine a valid category_id for the transaction.")

    # Monarch API `create_transaction` date format? YYYY-MM-DD
    # LOG PAYLOAD
    payload_log = {
        "date": data['date'],
        "account_id": target_account['id'],
        "amount": amount,
        "merchant_name": data['merchant'],
        "notes": notes,
        "category_id": category_id
    }
    print(f"\n\n--- MONARCH PUSH PAYLOAD ---\n{payload_log}\n----------------------------\n")

    result = await mm.create_transaction(
        date=data['date'],
        account_id=target_account['id'],
        amount=amount, # In account currency (assuming manual is USD/EUR?)
        merchant_name=data['merchant'],
        notes=notes,
        category_id=category_id,
        update_balance=True
    )
    
    # Mark as Needs Review
    # create_transaction doesn't support this flag, so we update it immediately after.
    try:
        tx_id = result['createTransaction']['transaction']['id']
        await mm.update_transaction(transaction_id=tx_id, needs_review=True)
        print(f"Marked transaction {tx_id} as 'Needs Review'")
        
        # Apply Tags
        tags_to_apply = []
        
        # Base Tag
        base_tag_name = "Imported by MM Bridge"
        base_tag_color = "#2196F3" # Material Blue
        base_tag_id = None
        
        # Cash Tag
        cash_tag_name = "Cash"
        cash_tag_color = "#4CAF50" # Material Green
        cash_tag_id = None
        apply_cash_tag = data.get('is_cash')
        
        # 1. Find existing tags
        existing_tags = await mm.get_transaction_tags()
        for tag in existing_tags.get("householdTransactionTags", []):
            if tag["name"] == base_tag_name:
                base_tag_id = tag["id"]
                print(f"Found existing tag: {base_tag_name} with ID: {base_tag_id}")
            if apply_cash_tag and tag["name"] == cash_tag_name:
                cash_tag_id = tag["id"]
                print(f"Found existing tag: {cash_tag_name} with ID: {cash_tag_id}")
                
        # 2. Create missing tags
        if not base_tag_id:
            new_tag_res = await mm.create_transaction_tag(name=base_tag_name, color=base_tag_color)
            base_tag_id = new_tag_res["createTransactionTag"]["tag"]["id"]
            print(f"Created new tag: {base_tag_name} with ID: {base_tag_id}")
            
        if apply_cash_tag and not cash_tag_id:
            new_tag_res = await mm.create_transaction_tag(name=cash_tag_name, color=cash_tag_color)
            cash_tag_id = new_tag_res["createTransactionTag"]["tag"]["id"]
            print(f"Created new tag: {cash_tag_name} with ID: {cash_tag_id}")
            
        if base_tag_id:
            tags_to_apply.append(base_tag_id)
        if cash_tag_id:
            tags_to_apply.append(cash_tag_id)
            
        # 3. Apply tags
        if tags_to_apply:
            await mm.set_transaction_tags(transaction_id=tx_id, tag_ids=tags_to_apply)
            print(f"Tagged transaction {tx_id} with {len(tags_to_apply)} tags")
        
        return tx_id
            
    except Exception as e:
        print(f"Failed to apply post-creation updates (Needs Review / Tags): {e}")
        # If we created the transaction but failed updates, we should still return the ID if we have it
        if 'tx_id' in locals():
            return tx_id
