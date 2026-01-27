import hashlib
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException
from ..models import Transaction
from .gemini import extract_transaction_data
from .monarch import get_monarch_client, push_transaction

async def process_transaction(file: UploadFile, db: AsyncSession):
    # 1. Read and Hash
    content = await file.read()
    image_hash = hashlib.sha256(content).hexdigest()
    
    # 2. Deduplication Check
    stmt = select(Transaction).where(Transaction.image_hash == image_hash)
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()
    
    if existing:
        return {"status": "duplicate", "data": existing.parsed_data}
    
    # 3. OCR Extraction
    data = extract_transaction_data(content)
    if not data or "error" in data:
        error_msg = data.get("error", "Unknown OCR error") if data else "Empty OCR response"
        # If OCR fails, we shouldn't save the hash maybe? OR save it as failed?
        # For now, just raise
        raise HTTPException(status_code=500, detail=error_msg)
        
    # 4. Monarch Push
    from ..models import Credentials
    creds_result = await db.execute(select(Credentials))
    creds = creds_result.scalars().first()
    
    if not creds:
        # TODO: Better error or onboarding flow
        raise HTTPException(status_code=400, detail="No Monarch credentials configured")
        
    try:
        mm = await get_monarch_client(db, creds.id)
        await push_transaction(mm, data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Monarch Error: {str(e)}")
    
    # 5. Save Record
    new_tx = Transaction(image_hash=image_hash, parsed_data=data)
    db.add(new_tx)
    await db.commit()
    
    return data
