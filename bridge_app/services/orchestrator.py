import hashlib
import asyncio
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException
from ..models import Transaction
from .gemini import extract_transaction_data
from .monarch import get_monarch_client, push_transaction
from starlette.concurrency import run_in_threadpool

async def process_transaction(content: bytes, db: AsyncSession, progress_callback=None, user_currency: str = None):
    async def report(msg, percent=None):
        print(f"Progress: {msg} ({percent}%)") # Log to console as requested
        if progress_callback:
            await progress_callback(msg, percent)

    # 1. Read and Hash
    await report("Computing image hash...", 10)
    # content passed directly
    image_hash = hashlib.sha256(content).hexdigest()
    
    # 2. Deduplication Check
    await report("Checking for duplicates...", 20)
    stmt = select(Transaction).where(Transaction.image_hash == image_hash)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        print(f"DUPLICATE TRANSACTION DETECTED: Hash={image_hash}")
        print(f"Existing Data: {existing.parsed_data}")
        return {"status": "duplicate", "data": existing.parsed_data}
    
    # 3. OCR Extraction
    await report("Scanning receipt with Gemini AI...", 30)
    
    # Retry logic for overloaded Gemini API
    max_retries = 2
    for attempt in range(max_retries + 1):
        # Gemini SDK is synchronous, so we run it in a threadpool to avoid blocking the event loop
        # Check for error
        if attempt > 0:
             await report(f"Retrying Gemini scan (Attempt {attempt+1})...", 35)

        data = await run_in_threadpool(extract_transaction_data, content)
        
        if data and "error" in data:
            err_str = str(data["error"])
            # Check if it's a 503 / Overloaded error
            if "503" in err_str or "overloaded" in err_str.lower():
                if attempt < max_retries:
                    await report(f"Gemini overloaded, cooling down ({attempt+1}/{max_retries})... 🧊", 35)
                    await asyncio.sleep(2) # Wait 2 seconds
                    continue
                else:
                    # Final failure after retries
                    await report("Gemini is too busy. Please wait a minute and try again.", 35)
                    # We will raise the error below
            else:
                # Other error, don't retry
                break
        else:
            # Success
            break
    
    # LOGGING FOR VISIBILITY
    # LOGGING FOR VISIBILITY
    # print(f"\n\n--- EXTRACTED DATA ---\n{data}\n------------------------\n\n")
    
    
    # 3b. Currency Conversion
    
    # 3b. Currency Conversion
    raw_currency = str(data.get("currency", "")).upper().strip()
    
    # Determine the effective original currency
    target_original = user_currency if user_currency else raw_currency
    
    # Normalize simplified symbols/names if coming from OCR
    if target_original in ["EURO", "€"]: target_original = "EUR"
    if target_original in ["£", "POUND"]: target_original = "GBP"
    if target_original in ["¥", "YEN"]: target_original = "JPY"
    
    print(f"Currency Check: User='{user_currency}' OCR='{raw_currency}' -> Effective='{target_original}'")
    
    if target_original == "USD":
        data["currency"] = "USD"
        
    elif target_original in ["EUR", "GBP", "JPY"]:
        try:
            await report(f"Converting {target_original} to USD...", 60)
            from .currency import get_exchange_rate
            
            rate = await get_exchange_rate(target_original, "USD", data["date"])
            original_amount = data["amount"]
            converted_amount = round(original_amount * rate, 2)
            
            print(f"Converting {target_original} {original_amount} to USD {converted_amount} (Rate: {rate})")
            
            data["original_amount"] = original_amount
            data["original_currency"] = target_original
            data["amount"] = converted_amount
            data["currency"] = "USD"
            data["exchange_rate"] = rate
        except Exception as e:
             print(f"Conversion failed, using original: {e}")
             data["currency"] = target_original
    else:
        print(f"Skipping conversion: '{target_original}' not in supported list.")
        data["currency"] = target_original

    # Log final payload
    # Log final payload
    # print(f"FINAL PAYLOAD TO MONARCH: {data}")

    if not data or "error" in data:
        error_msg = data.get("error", "Unknown OCR error") if data else "Empty OCR response"
        # If OCR fails, we shouldn't save the hash maybe? OR save it as failed?
        # For now, just raise
        raise HTTPException(status_code=500, detail=error_msg)
        
    # 4. Monarch Push
    await report("Connecting to Monarch Money...", 70)
    from ..models import Credentials
    creds_result = await db.execute(select(Credentials))
    creds = creds_result.scalars().first()
    
    if not creds:
        # TODO: Better error or onboarding flow
        raise HTTPException(status_code=400, detail="No Monarch credentials configured")
        
    try:
        await report("Creating transaction in Monarch...", 85)
        mm = await get_monarch_client(db, creds.id)
        tx_id = await push_transaction(mm, data)
        if tx_id:
            data['monarch_tx_id'] = tx_id
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Monarch Error: {str(e)}")
    
    # 5. Save Record
    await report("Finalizing...", 95)
    new_tx = Transaction(image_hash=image_hash, parsed_data=data)
    db.add(new_tx)
    await db.commit()
    
    return data
