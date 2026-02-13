import hashlib
import asyncio
import json
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException
from ..models import Transaction
from .gemini import extract_transaction_data
from .monarch import get_monarch_client, push_transaction
from starlette.concurrency import run_in_threadpool

async def process_manual_transaction(manual_data: dict, db: AsyncSession, progress_callback=None, force_override: bool = False):
    """
    Process a manually entered transaction.
    """
    async def report(msg, percent=None):
        print(f"Progress: {msg} ({percent}%)")
        if progress_callback:
            await progress_callback(msg, percent)
            
    await report("Validating manual entry...", 10)
    
    # generate a synthetic hash for manual entries to prevent re-submission of the exact same form
    # We use a prefix to distinguish from file hashes
    data_string = json.dumps(manual_data, sort_keys=True)
    image_hash = "manual_" + hashlib.sha256(data_string.encode()).hexdigest()
    
    return await _process_transaction_data(manual_data, image_hash, db, report, force_override=force_override)

async def process_transaction(content: bytes, db: AsyncSession, progress_callback=None, user_currency: str = None, force_override: bool = False):
    """
    Process a file-based transaction (OCR).
    """
    async def report(msg, percent=None):
        print(f"Progress: {msg} ({percent}%)") 
        if progress_callback:
            await progress_callback(msg, percent)

    # 1. Read and Hash
    await report("Computing image hash...", 10)
    image_hash = hashlib.sha256(content).hexdigest()
    
    # 2. Deduplication Check (Fast check before OCR)
    if not force_override:
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
    data = None
    
    for attempt in range(max_retries + 1):
        if attempt > 0:
             await report(f"Retrying Gemini scan (Attempt {attempt+1})...", 35)

        data = await run_in_threadpool(extract_transaction_data, content)
        
        if data and "error" in data:
            err_str = str(data["error"])
            if "503" in err_str or "overloaded" in err_str.lower():
                if attempt < max_retries:
                    await report(f"Gemini overloaded, cooling down ({attempt+1}/{max_retries})... 🧊", 35)
                    await asyncio.sleep(2)
                    continue
                else:
                    await report("Gemini is too busy. Please wait a minute and try again.", 35)
            else:
                break
        else:
            break
            
    if not data or "error" in data:
        error_msg = data.get("error", "Unknown OCR error") if data else "Empty OCR response"
        raise HTTPException(status_code=500, detail=error_msg)

    # Inject/Override currency if provided by user during upload
    if user_currency:
        # We pass it to the shared processor, but we can also check it here if needed.
        # The shared processor handles the currency logic, so we will pass user_currency to it 
        # via the data dict or checks. 
        # Actually logic is in the shared block below.
        pass

    return await _process_transaction_data(data, image_hash, db, report, user_currency, force_override=force_override)

async def _process_transaction_data(data: dict, image_hash: str, db: AsyncSession, report_func, user_currency_override: str = None, force_override: bool = False):
    """
    Shared logic for processing transaction data, converting currency, pushing to Monarch, and saving.
    """
    
    # re-check duplicates here? 
    # For manual, we haven't checked yet. For File, we checked before OCR.
    # It doesn't hurt to check again, but for File it is redundant if we trust previous check.
    # Let's do a quick check if "manual_" prefix involves.
    if image_hash.startswith("manual_") and not force_override:
        await report_func("Checking for duplicates...", 20)
        stmt = select(Transaction).where(Transaction.image_hash == image_hash)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            return {"status": "duplicate", "data": existing.parsed_data}

    # 3b. Currency Conversion
    raw_currency = str(data.get("currency", "")).upper().strip()
    
    # Determine the effective original currency
    # If user_currency_override is set (from upload form), use it IF the OCR'd one is ambiguous or we trust user more?
    # Original logic used user_currency if present, else OCR.
    target_original = user_currency_override if user_currency_override else raw_currency
    
    # Normalize
    if target_original in ["EURO", "€"]: target_original = "EUR"
    if target_original in ["£", "POUND"]: target_original = "GBP"
    if target_original in ["¥", "YEN"]: target_original = "JPY"
    
    print(f"Currency Check: User='{user_currency_override}' OCR='{raw_currency}' -> Effective='{target_original}'")
    
    if target_original == "USD":
        data["currency"] = "USD"
        
    elif target_original in ["EUR", "GBP", "JPY"]:
        try:
            await report_func(f"Converting {target_original} to USD...", 60)
            from .currency import get_exchange_rate
            
            rate = await get_exchange_rate(target_original, "USD", data["date"])
            original_amount = float(data["amount"]) # Ensure float
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

    # 4. Monarch Push
    await report_func("Connecting to Monarch Money...", 70)
    from ..models import Credentials
    creds_result = await db.execute(select(Credentials))
    creds = creds_result.scalars().first()
    
    if not creds:
        raise HTTPException(status_code=400, detail="No Monarch credentials configured")
        
    try:
        await report_func("Creating transaction in Monarch...", 85)
        mm = await get_monarch_client(db, creds.id)
        tx_id = await push_transaction(mm, data)
        if tx_id:
            data['monarch_tx_id'] = tx_id
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Monarch Error: {str(e)}")
    
    # 5. Save Record
    await report_func("Finalizing...", 95)
    
    # If forcing, we need to alter the hash to avoid unique constraint violation
    # We still want to save the record of this specific run.
    if force_override:
        import uuid
        image_hash = f"{image_hash}_forced_{uuid.uuid4().hex[:8]}"
        
    new_tx = Transaction(image_hash=image_hash, parsed_data=data)
    db.add(new_tx)
    await db.commit()
    
    return data
