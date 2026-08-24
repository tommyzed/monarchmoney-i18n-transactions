import uuid
import asyncio
import os
import json
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from .database import engine, Base, get_db, AsyncSessionLocal
from contextlib import asynccontextmanager
from .services.orchestrator import process_transaction
from .services.monarch import get_monarch_client, get_latest_credentials
from .models import Credentials, MerchantMapping, Category, FireSettings, Transaction, Log, FailedTransaction, Merchant, SpendingReport
from sqlalchemy.future import select
from sqlalchemy import delete, func
from pydantic import BaseModel
from typing import Optional, Any
from datetime import datetime, timedelta

DEMO_DEFAULTS = {
    "current_age": 45,
    "retirement_age": 65,
    "annual_contribution": 100000,
    "annual_retirement_spending": 80000,
    "risk_tolerance": "moderate",
    "inflation_rate": 0.03,
    "final_age": 85,
    "social_security_enabled": False,
    "social_security_pia": 0,
    "social_security_fra": 67,
    "social_security_birth_month": 1,
    "social_security_birth_year": 1980,
    "social_security_withdrawal_month": 1,
    "social_security_withdrawal_year": 2047,
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("📦 LIFESPAN: Checking database connection (this might take a moment if connecting remotely)...")
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("✅ LIFESPAN: Database connected.")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ LIFESPAN: Database tables verified/created.")
    except Exception as e:
        print(f"❌ LIFESPAN: Database connection failed: {e}")
        # We might want to re-raise or continue depending on severity, but for diagnosis, printing is key.
        raise e
    print("✨ LIFESPAN: Startup complete.")
    yield

app = FastAPI(lifespan=lifespan)

# --- Security Configuration (Ghost Cookie) ---
UNLOCK_SECRET = os.environ.get("UNLOCK_SECRET")
DEVICE_TOKEN_COOKIE = "device_token"
# Token value is a hash of the secret to avoid exposing it directly in the cookie if inspected
COOKIE_VALUE = hashlib.sha256(UNLOCK_SECRET.encode()).hexdigest() if UNLOCK_SECRET else None

class GhostSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Default state
        request.state.is_authenticated = False

        # Check for cookie if secret is configured
        if UNLOCK_SECRET:
            token = request.cookies.get(DEVICE_TOKEN_COOKIE)
            if token == COOKIE_VALUE:
                request.state.is_authenticated = True
                return await call_next(request)

        # --- Unauthenticated access rules ---

        # Allow activation endpoint
        if request.url.path == "/s":
            return await call_next(request)
            
        # Allow static assets (manifest, Service Worker, icons) to support PWA installation.
        if request.url.path in ["/manifest.json", "/sw.js", "/favicon.ico"]:
            response = await call_next(request)
            if request.url.path == "/sw.js":
                response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        if request.url.path.endswith((".png", ".jpg", ".css", ".js", ".gif")):
             return await call_next(request)

        # Allow FIRE dashboard demo access
        if request.url.path == "/fire" or request.url.path.startswith("/api/fire"):
            return await call_next(request)

        
        # If no secret is configured, block protected routes
        if not UNLOCK_SECRET:
            return Response(status_code=401, content="Unauthorized - Security not configured on server")

        # GHOST MODE: Return 404 Not Found if unauthorized
        return Response(status_code=404, content="Not Found")

app.add_middleware(GhostSecurityMiddleware)

@app.get("/s")
async def activate(request: Request, s: str):
    """
    Sets the Ghost Cookie to unlock the device.
    Usage: /s?s=YOUR_SECRET
    """
    if not UNLOCK_SECRET:
        return Response(status_code=500, content="Security not configured on server.")
        
    if s != UNLOCK_SECRET:
        # Fake a 404 if secret is wrong to prevent guessing
        return Response(status_code=404, content="Not Found")
    
    html_content = """
    <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="manifest" href="/manifest.json">
            <link rel="icon" type="image/png" href="/icon.png">
            <style>
                body { font-family: sans-serif; text-align: center; padding: 2rem; background: #f0fdf4; color: #166534; }
                .card { background: white; padding: 2rem; border-radius: 1rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>🔓 Device Activated</h1>
                <p>You have successfully unlocked access to the bridge.</p>
                <p>You can now close this window and use the app.</p>
                <a href="/" style="display:inline-block; margin-top:1rem; padding:0.5rem 1rem; background:#166534; color:white; text-decoration:none; border-radius:0.5rem;">Go to App</a>
            </div>
        </body>
    </html>
    """
    
    is_secure = request.url.scheme == "https"

    response = HTMLResponse(content=html_content)
    response.set_cookie(
        key=DEVICE_TOKEN_COOKIE,
        value=COOKIE_VALUE,
        max_age=60*60*24*365*10, # 10 years
        httponly=True,
        samesite="lax",
        secure=is_secure
    )
    return response

# Simple in-memory job store
# Structure: { job_id: { "status": "processing" | "completed" | "failed", "result": dict, "error": str, "inputs": dict, "failed_tx_id": int } }
jobs = {}


async def process_background_job(job_id: str, content: bytes, user_currency: str = None, manual_data: dict = None, force_override: bool = False):
    """
    Background task to process the transaction using a fresh DB session.
    """
    print(f"Starting background job {job_id}")
    
    jobs[job_id] = {"status": "processing", "step": "Initializing...", "progress": 0}
    
    async def progress_callback(step_msg, percent=None):
        jobs[job_id]["step"] = step_msg
        if percent is not None:
            jobs[job_id]["progress"] = percent

    try:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    print(f"Job {job_id}: Attempt {attempt+1}...")
                
                async with AsyncSessionLocal() as db:
                    if manual_data:
                         from .services.orchestrator import process_manual_transaction
                         result = await process_manual_transaction(manual_data, db, progress_callback=progress_callback, force_override=force_override)
                    else:
                         result = await process_transaction(content, db, progress_callback=progress_callback, user_currency=user_currency, force_override=force_override)
                    
                    merchant_name = None
                    target_dict = None
                    if isinstance(result, dict):
                        if "merchant" in result:
                            merchant_name = result.get("merchant")
                            target_dict = result
                        elif "data" in result and isinstance(result["data"], dict) and "merchant" in result["data"]:
                            merchant_name = result["data"].get("merchant")
                            target_dict = result["data"]
                    
                    if merchant_name and target_dict is not None:
                        m_stmt = select(Merchant.is_starred).where(func.lower(Merchant.name) == merchant_name.strip().lower())
                        m_res = await db.execute(m_stmt)
                        is_starred_val = m_res.scalar_one_or_none()
                        target_dict["is_starred"] = bool(is_starred_val) if is_starred_val is not None else False
                
                # Success
                jobs[job_id] = {
                    "status": "completed", 
                    "result": result, 
                    "progress": 100,
                    # Store inputs for potential retry/force submit
                    "inputs": {
                        "content": content,
                        "user_currency": user_currency,
                        "manual_data": manual_data
                    }
                }
                print(f"Job {job_id} completed successfully")
                return # Exit function on success
                
            except Exception as e:
                # Check for DB connection errors
                error_str = str(e)
                is_db_error = "InterfaceError" in str(type(e).__name__) or "connection is closed" in error_str
                
                if is_db_error and attempt < max_retries - 1:
                    print(f"⚠️ DB Connection Error (Attempt {attempt+1}): {e}")
                    print("Turning the database snooze button... 💤⏰")
                    
                    # Update UI to inform user
                    jobs[job_id]["step"] = "Waking up database... 🥱"
                    await asyncio.sleep(2) # Wait for DB to wake up
                    continue
                else:
                    # Not a DB error or out of retries, raise to outer handler
                    raise e
                    
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Job {job_id} FAILED:\n{error_details}")
        
        # User-friendly error mapping
        err_msg = str(e)
        if "Connection" in err_msg or "timeout" in err_msg.lower():
            display_error = "Database connection timed out. Please try again later."
        elif "GEMINI_API_KEY" in err_msg:
            display_error = "Server configuration error: Gemini API Key missing."
        elif "Monarch" in err_msg:
            display_error = f"Monarch Error: {err_msg}"
        elif "Currency" in err_msg:
            display_error = f"Currency Error: {err_msg}"
        else:
            display_error = f"I hit a snag: {err_msg}"

        # Save to FailedTransaction in DB
        failed_tx_id = None
        try:
            img_hash = None
            if content:
                img_hash = hashlib.sha256(content).hexdigest()
            elif manual_data:
                data_string = json.dumps(manual_data, sort_keys=True)
                img_hash = "manual_" + hashlib.sha256(data_string.encode()).hexdigest()

            parsed_data = getattr(e, "parsed_data", None)
            async with AsyncSessionLocal() as db:
                failed_tx = FailedTransaction(
                    source_type="manual" if manual_data else "receipt",
                    image_hash=img_hash,
                    raw_content=content if content else None,
                    user_currency=user_currency,
                    parsed_data=parsed_data,
                    manual_data=manual_data,
                    error_message=display_error,
                    retry_count=0
                )
                db.add(failed_tx)
                await db.commit()
                await db.refresh(failed_tx)
                failed_tx_id = failed_tx.id
                print(f"💾 Saved failed transaction ID {failed_tx_id} to database.")
        except Exception as save_err:
            print(f"⚠️ Error saving failed transaction to database: {save_err}")

        jobs[job_id] = {
            "status": "failed", 
            "error": display_error, 
            "failed_tx_id": failed_tx_id,
            "progress": 0,
            "inputs": {
                "content": content,
                "user_currency": user_currency,
                "manual_data": manual_data,
                "failed_tx_id": failed_tx_id
            }
        }

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_receipt(
    file: UploadFile = File(...),
    currency: str = Form(None),
    db: AsyncSession = Depends(get_db)
):
    content = await file.read()
    try:
        result = await process_transaction(content, db, user_currency=currency)
        return {"status": "success", "data": result}
    except Exception as e:
        print(f"Error processing transaction in /upload: {e}")
        try:
            img_hash = hashlib.sha256(content).hexdigest()
            parsed_data = getattr(e, "parsed_data", None)
            failed_tx = FailedTransaction(
                source_type="receipt",
                image_hash=img_hash,
                raw_content=content,
                user_currency=currency,
                parsed_data=parsed_data,
                error_message=str(e),
                retry_count=0
            )
            db.add(failed_tx)
            await db.commit()
        except Exception as save_err:
            print(f"⚠️ Error saving failed transaction in /upload: {save_err}")

        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/job/{job_id}")
async def get_job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Exclude inputs (bytes) to avoid JSON serialization errors
    return {k: v for k, v in job.items() if k != "inputs"}

@app.post("/job/{job_id}/retry")
async def retry_job(job_id: str, force: bool = False, background_tasks: BackgroundTasks = None):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    inputs = job.get("inputs")
    if not inputs:
        raise HTTPException(status_code=400, detail="Cannot retry this job (inputs not saved)")
        
    print(f"Retrying job {job_id} with force={force}")

    # Clean up previous failed_tx if one was created
    failed_tx_id = inputs.get("failed_tx_id")
    if failed_tx_id:
        try:
            async with AsyncSessionLocal() as db:
                failed_tx = await db.get(FailedTransaction, failed_tx_id)
                if failed_tx:
                    await db.delete(failed_tx)
                    await db.commit()
                    print(f"Cleaned up previous failed_tx {failed_tx_id} for retry.")
        except Exception as cleanup_err:
            print(f"Error cleaning up failed_tx before retry: {cleanup_err}")
    
    # Reset job status
    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = 0
    jobs[job_id]["step"] = "Retrying..."
    
    # Restart background task
    background_tasks.add_task(
        process_background_job, 
        job_id, 
        inputs.get("content"), 
        inputs.get("user_currency"), 
        inputs.get("manual_data"),
        force_override=force
    )
    
    return {"status": "ok"}


# Reuse the loading page HTML for both routes
LOADING_HTML = """
<html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="manifest" href="/manifest.json">
        <link rel="icon" type="image/png" href="/icon.png">
        <title>💶 Monarch Money Bridge</title>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Sriracha&display=swap" rel="stylesheet">
        <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
        <style>
            * { box-sizing: border-box; }
            body { 
                font-family: 'Sriracha', cursive;  
                padding: 2rem; 
                text-align: center; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                min-height: 100dvh;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                margin: 0;
                color: #333;
            }
            .card { 
                background: linear-gradient(135deg, #fcc5a7 0%, #fcc5a7 100%);
                padding: 2.5rem;
                border-radius: 20px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.2);
                max-width: 500px;
                width: 100%;
                backdrop-filter: blur(10px);
                display: none; /* Hidden by default */
                flex-direction: column;
                align-items: center;
                position: relative;
            }
            .deep-link-menu {
                position: absolute;
                top: 20px;
                right: 20px;
                z-index: 50;
            }

            /* Hamburger Button */
            .hamburger-btn {
                background: rgba(255, 255, 255, 0.25);
                border: 1px solid rgba(255, 255, 255, 0.15);
                cursor: pointer;
                display: flex;
                flex-direction: column;
                justify-content: center;
                align-items: center;
                gap: 5px;
                width: 42px;
                height: 42px;
                border-radius: 50%;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                position: relative;
                z-index: 100;
                padding: 0;
                box-sizing: border-box;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
            }

            .hamburger-btn:hover {
                background: rgba(255, 255, 255, 0.45);
                transform: scale(1.05);
                box-shadow: 0 6px 16px rgba(0, 0, 0, 0.1);
            }

            .hamburger-btn:active {
                transform: scale(0.95);
            }

            .hamburger-bar {
                display: block;
                width: 20px;
                height: 2px;
                background-color: #d35400;
                border-radius: 2px;
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            }

            /* Animate Hamburger to X */
            .hamburger-btn.open .hamburger-bar:nth-child(1) {
                transform: translateY(7px) rotate(45deg);
                background-color: #667eea;
            }

            .hamburger-btn.open .hamburger-bar:nth-child(2) {
                opacity: 0;
                transform: scale(0);
            }

            .hamburger-btn.open .hamburger-bar:nth-child(3) {
                transform: translateY(-7px) rotate(-45deg);
                background-color: #667eea;
            }

            .deep-link-dropdown {
                display: none;
                position: absolute;
                right: 0;
                top: calc(100% + 8px);
                background: rgba(255, 255, 255, 0.95);
                backdrop-filter: blur(10px);
                -webkit-backdrop-filter: blur(10px);
                border-radius: 16px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
                width: 200px;
                overflow: hidden;
                flex-direction: column;
                text-align: left;
                border: 1px solid rgba(255, 255, 255, 0.2);
                z-index: 99;
            }

            .deep-link-item {
                padding: 12px 18px;
                text-decoration: none;
                color: #333;
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 0.95rem;
                transition: all 0.25s ease;
            }

            .menu-divider {
                height: 1.5px;
                background-color: rgba(102, 126, 234, 0.35);
                margin: 6px 12px;
                border: none;
                border-radius: 1px;
            }

            .deep-link-item:hover {
                background: #f8faff;
                color: #667eea;
                padding-left: 24px;
            }

            /* History Table Styles (Flex-based list) */
            .history-list {
                width: 100%;
                margin-top: 1rem;
            }
            .history-header {
                display: flex;
                justify-content: space-between;
                font-weight: bold;
                color: #d35400;
                border-bottom: 2px solid #fcc5a7;
                padding: 12px 8px;
                font-size: 0.9rem;
            }
            .history-header-merchant { flex: 2; text-align: left; }
            .history-header-amount { flex: 1; text-align: right; margin-right: 12px; }
            .history-header-date { flex: 1; text-align: center; max-width: 90px; }

            .history-row-wrapper {
                position: relative;
                overflow: visible;
                width: 100%;
                transition: transform 0.15s ease-out;
                box-sizing: border-box;
                background: transparent;
                border-bottom: 1px solid #fcc5a7;
            }
            .history-row-wrapper:last-child {
                border-bottom: none;
            }
            .history-row-delete-btn {
                position: absolute;
                right: -80px;
                top: 0;
                bottom: 0;
                width: 80px;
                background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%);
                color: white;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: bold;
                font-size: 0.85rem;
                cursor: pointer;
                box-sizing: border-box;
            }

            .history-row-content {
                width: 100%;
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px 8px;
                background: transparent;
                box-sizing: border-box;
                font-size: 0.9rem;
            }
            
            @media (hover: hover) {
                .history-row-wrapper:hover {
                    background: rgba(255, 255, 255, 0.15);
                }
            }

            .history-row-wrapper.swiped {
                transform: translateX(-80px);
            }

            .amount-green {
                color: #16a34a;
                font-weight: bold;
            }

            .amount-red {
                color: #dc2626;
                font-weight: bold;
            }
            .title { font-weight: bold; font-size: 1.5rem; margin-top: 0; margin-bottom: 1rem; color: green; }
            .btn { 
                background: linear-gradient(to right, #667eea, #764ba2); 
                color: #fff; 
                padding: 0.8rem 2rem; 
                border-radius: 50px; 
                text-decoration: none; 
                display: inline-block; 
                margin-top: 1.5rem; 
                cursor: pointer; 
                border: none;
                font-size: 1rem;
                font-weight: bold;
                box-shadow: 0 4px 15px rgba(0,0,0,0.2);
                transition: transform 0.2s;
                font-family: inherit;
            }
            #resultCard .btn {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }
            .btn:hover { transform: translateY(-2px); }
            .detail-row { display: flex; justify-content: space-between; align-items: center; margin: 0.25rem auto; border-bottom: 1px solid #eee; padding-bottom: 0.25rem; width: 100%; gap: 1rem; }
            .label { color: #666; }
            .value { font-weight: 600; text-align: right; }

            /* Editable date pill */
            .date-pill {
                font-weight: 600;
                color: #667eea;
                cursor: pointer;
                border-bottom: 2px dashed #667eea;
                padding-bottom: 1px;
                display: inline-flex;
                align-items: center;
                gap: 4px;
                transition: color 0.2s, border-color 0.2s;
                user-select: none;
            }
            .date-pill:hover { color: #764ba2; border-color: #764ba2; }
            .date-pill.updating { opacity: 0.5; pointer-events: none; }
            
            /* Editable category pill */
            .category-pill {
                font-weight: 600;
                color: #667eea;
                cursor: pointer;
                border-bottom: 2px dashed #667eea;
                padding-bottom: 1px;
                display: inline-flex;
                align-items: center;
                gap: 4px;
                transition: color 0.2s, border-color 0.2s;
                user-select: none;
            }
            .category-pill:hover { color: #764ba2; border-color: #764ba2; }
            .category-pill.updating { opacity: 0.5; pointer-events: none; }

            #datePicker {
                position: absolute;
                opacity: 0;
                pointer-events: none;
                width: 0;
                height: 0;
            }
            
            /* Mobile Optimizations */
            @media (max-width: 480px) {
                body {
                    padding: 1rem;
                }
            }
            
            /* Loading Animation */
            #loadingOverlay {
                display: flex; 
                position: fixed; 
                top: 0; left: 0; 
                width: 100%; height: 100%; 
                z-index: 1000; 
                justify-content: center; 
                align-items: center; 
                flex-direction: column;
            }
            .bouncer { font-size: 4rem; animation: bounce 1s infinite alternate; }
            #loadingTitle { color: #fff; margin-top: 20px; font-size: 1rem; }
            #loadingOverlay p { color: #ddd; }
            
            @keyframes bounce {
                from { transform: translateY(0); }
                to { transform: translateY(-20px) rotate(5deg); }
            }
            
            /* Error State */
            .error-text { color: #e53e3e; font-weight: bold; font-size: 1.5rem; }
            /* Progress Bar */
            .progress-container {
                width: 100%;
                background-color: #e0e7ff;
                border-radius: 9999px;
                height: 8px;
                margin-bottom: 20px;
                overflow: hidden;
                max-width: 300px;
            }
            .progress-bar {
                height: 100%;
                background-color: #4f46e5;
                width: 0%;
                transition: width 0.5s ease-out;
                border-radius: 9999px;
            }
            
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }

            .spinning-emoji {
                display: inline-block;
                animation: spin 2s linear infinite;
                font-size: 30px;
                margin-left: 10px;
                vertical-align: middle;
            }

            #detailsContainer {
                background: #ffffff;
                border-radius: 16px;
                padding: 1.5rem;
                width: 100%;
                margin-top: 1rem;
                box-sizing: border-box;
                border: 1px solid rgba(0, 0, 0, 0.05);
                box-shadow: 0 4px 6px rgba(0,0,0,0.05);
            }

            /* Toast Notification */
            .toast {
                visibility: hidden;
                min-width: 250px;
                background-color: #333;
                color: #fff;
                text-align: center;
                border-radius: 8px;
                padding: 12px;
                position: fixed;
                z-index: 3500;
                left: 50%;
                bottom: 30px;
                transform: translateX(-50%);
                font-size: 0.95rem;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                opacity: 0;
                transition: opacity 0.3s, bottom 0.3s;
            }

            .toast.show {
                visibility: visible;
                opacity: 1;
                bottom: 50px;
            }
            
            .toast.success { background-color: #be185d; }
            .toast.error { background-color: #9f1239; }

            /* Failed Transactions Styles */
            .failed-badge {
                background: #ef4444;
                color: white;
                border-radius: 999px;
                font-size: 0.75rem;
                padding: 2px 7px;
                font-weight: bold;
                margin-left: 5px;
            }
            .failed-item-card {
                background: rgba(255, 255, 255, 0.92);
                border: 1px solid rgba(239, 68, 68, 0.25);
                border-radius: 12px;
                padding: 12px 14px;
                margin-bottom: 10px;
                box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
                transition: all 0.2s ease;
                text-align: left;
            }
            .failed-item-card:hover {
                background: #ffffff;
                box-shadow: 0 4px 10px rgba(0, 0, 0, 0.08);
            }
            .failed-item-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 4px;
            }
            .failed-item-merchant {
                font-weight: bold;
                color: #1f2937;
                font-size: 0.95rem;
                display: flex;
                align-items: center;
                gap: 6px;
            }
            .failed-item-amount {
                font-weight: bold;
                color: #b91c1c;
                font-size: 0.95rem;
            }
            .failed-error-banner {
                background: #fee2e2;
                color: #991b1b;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 0.78rem;
                margin: 6px 0;
                line-height: 1.3;
                word-break: break-word;
            }
            .failed-item-actions {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-top: 8px;
            }
            .failed-meta {
                font-size: 0.75rem;
                color: #6b7280;
            }
            .failed-btn-group {
                display: flex;
                gap: 6px;
            }
            .btn-failed-retry {
                background: #4f46e5;
                color: white;
                border: none;
                padding: 4px 10px;
                border-radius: 6px;
                font-size: 0.8rem;
                cursor: pointer;
                font-weight: bold;
                transition: background 0.15s;
            }
            .btn-failed-retry:hover { background: #4338ca; }
            .btn-failed-edit {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #d1d5db;
                padding: 4px 8px;
                border-radius: 6px;
                font-size: 0.8rem;
                cursor: pointer;
            }
            .btn-failed-edit:hover { background: #e5e7eb; }
            .btn-failed-del {
                background: #fee2e2;
                color: #991b1b;
                border: 1px solid #fecaca;
                padding: 4px 8px;
                border-radius: 6px;
                font-size: 0.8rem;
                cursor: pointer;
            }
            .btn-failed-del:hover { background: #fca5a5; }
        </style>
    </head>
    <body>
        <!-- Loading State (Visible initially) -->
        <div id="loadingOverlay">
            <img src="/elf.gif" alt="Dancing Elf" style="height: 120px; margin-bottom: 20px;">
            
            <!-- Progress Bar -->
            <div class="progress-container" id="progressContainer">
                <div class="progress-bar" id="progressBar"></div>
            </div>
            <h4 id="loadingTitle">Our AI Elves are hard at work! <span class="spinning-emoji">🧙‍♂️</span></h4>
            <p id="loadingSubtitle">Our AI elves are reading your receipt! 🧙‍♂️</p>
        </div>
        
        <!-- Success/duplicate/Error Card (Hidden initially) -->
        <div id="resultCard" class="card">
            <!-- Deep Link & Nav Menu -->
            <div class="deep-link-menu">
                <button class="hamburger-btn" id="deepLinkTrigger" title="Menu" aria-label="Toggle Menu">
                    <span class="hamburger-bar"></span>
                    <span class="hamburger-bar"></span>
                    <span class="hamburger-bar"></span>
                </button>
                <div class="deep-link-dropdown" id="deepLinkDropdown">
                    <a href="#" id="historyLogLink" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">📜</span>
                        <span>History Log</span>
                    </a>
                    <a href="#" id="manageStarredLink" class="deep-link-item" onclick="openManageStarredModal(event)">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">⭐</span>
                        <span>Starred Merchants</span>
                    </a>
                    <a href="#" id="failedTxnsLink" class="deep-link-item" onclick="openFailedModal(event)">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">⚠️</span>
                        <span style="flex: 1;">Failed Txns</span>
                        <span id="failedBadge" style="display:none; background:#ef4444; color:white; border-radius:999px; font-size:0.75rem; padding:2px 7px; font-weight:bold; margin-left:5px;">0</span>
                    </a>
                    <div class="menu-divider"></div>
                    <a href="/spending" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">📊</span>
                        <span>Spending Report</span>
                    </a>
                    <a href="/fire" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">🔥</span>
                        <span>FIRE Dashboard</span>
                    </a>
                    <div class="menu-divider"></div>
                    <a href="intent://accounts#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Faccounts;end"
                        target="_blank" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">💳</span>
                        <span>Accounts</span>
                    </a>
                    <a href="intent://transactions?needsReview=true&needsReviewUnassigned=true&transactionVisibility=all_transactions#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%3FisPending%3Dtrue;end"
                        target="_blank" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">🔍</span>
                        <span>Needs Review</span>
                    </a>
                    <a href="intent://transactions?isPending=true#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%3FisPending%3Dtrue;end"
                        target="_blank" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">⏳</span>
                        <span>Pending Txns</span>
                    </a>
                    <a href="intent://transactions?tags=239319736443617746&transactionVisibility=all_transactions#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%3Ftags%3D239319736443617746%26transactionVisibility%3Dall_transactions;end"
                        target="_blank" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">💵</span>
                        <span>Cash Txns</span>
                    </a>
                    <div class="menu-divider"></div>
                    <a href="#" id="updateAppLink" class="deep-link-item">
                        <span style="font-size: 1.1rem; display: inline-block; width: 20px; text-align: center;">🔄</span>
                        <span>Update App</span>
                    </a>
                </div>
            </div>
            <div id="cardIcon" style="font-size: 3rem; margin-bottom: 0.2rem;">🎉</div>
            <p id="cardTitle" class="title">Transaction Processed</p>
            
            <div id="detailsContainer">
                <div class="detail-row" style="align-items: center;">
                    <span class="label">Merchant</span>
                    <div style="display: flex; align-items: center; gap: 8px; justify-content: flex-end; flex: 1;">
                        <button id="starMerchantBtn" onclick="toggleProcessedMerchantStar()" title="Star this merchant" style="background: none; border: none; font-size: 1.3rem; cursor: pointer; padding: 0; line-height: 1; transition: transform 0.15s ease;" onmouseover="this.style.transform='scale(1.2)'" onmouseout="this.style.transform='scale(1)'">☆</button>
                        <span id="merchantValue" class="value">--</span>
                    </div>
                </div>
                <div class="detail-row">
                    <span class="label">Amount</span>
                    <span id="amountValue" class="value">--</span>
                </div>

                <div class="detail-row" style="position: relative;">
                    <span class="label">Date</span>
                    <span id="dateValue" class="value date-pill" title="Tap to correct date" onclick="openDatePicker()">--</span>
                    <input type="date" id="datePicker" aria-label="Date picker">
                </div>
                <div class="detail-row" style="position: relative;">
                    <span class="label">Category</span>
                    <span id="categoryValue" class="value category-pill" title="Tap to change category" onclick="openCategorySelector()">--</span>
                    <select id="inlineCategorySelect" style="display: none; font-size: 0.9rem; padding: 4px; border-radius: 6px; border: 1px solid #d1d5db; background: white; max-width: 180px; z-index: 10;" aria-label="Category selector"></select>
                </div>
                <div class="detail-row">
                    <span class="label">Added to</span>
                    <span id="accountValue" class="value">__MM_ACCOUNT__</span>
                </div>
            </div>

            <!-- Historical name legend — shown only when used_historical_name is true -->
            <div id="historicalLegend" style="display:none; font-size:0.75rem; color:#677ae3; font-style:italic; margin-top:0.75rem; text-align:center;">💜 matched from history</div>
            
            <div id="errorContainer" style="display:none; text-align: center;">
                <p id="errorMessage" style="color: #b91c1c; font-weight: bold; margin: 1rem 0; font-size: 1.05rem;"></p>
                <div style="margin-top: 0.8rem; padding: 0.75rem; background: #fff3cd; color: #856404; border-radius: 10px; font-size: 0.88rem; border: 1px solid #ffeeba; line-height: 1.4;">
                    💾 <strong>Saved to Failed Transactions!</strong><br>
                    You can retry it now or at a later time from the menu.
                </div>
            </div>
            
            <div id="successActions" style="display: flex; gap: 10px; width: 100%; justify-content: center; margin-top: 1.5rem; flex-wrap: nowrap;">
                <button id="editMappingBtn" class="btn" style="flex: 1; min-width: 0; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; margin-top: 0; background: linear-gradient(to right, #fcad03, #f76b1c); white-space: nowrap;" onclick="openMappingModal()">Edit Mapping</button>
                <button id="forceSubmitBtn" class="btn" style="display:none; flex: 1; min-width: 0; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; background: linear-gradient(to right, #ef4444, #b91c1c); margin-top: 0; white-space: nowrap;" onclick="forceSubmit()">Force Submit</button>
                <a href="/" class="btn" style="flex: 1; min-width: 0; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; margin-top: 0; white-space: nowrap;">Return 🏡</a>
            </div>

            <div id="errorActions" style="display: none; gap: 10px; width: 100%; justify-content: center; margin-top: 1.5rem; flex-wrap: wrap;">
                <button id="retryErrorBtn" class="btn" style="flex: 1; min-width: 120px; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; margin-top: 0; background: linear-gradient(to right, #4f46e5, #7c3aed); white-space: nowrap;" onclick="forceSubmit()">🔄 Retry Now</button>
                <button id="viewFailedBtn" class="btn" style="flex: 1; min-width: 140px; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; margin-top: 0; background: linear-gradient(to right, #e11d48, #be123c); white-space: nowrap;" onclick="openFailedModal(event)">⚠️ View Failed Txns</button>
                <a href="/" class="btn" style="flex: 1; min-width: 100px; padding: 0.75rem 0.5rem; font-size: 0.95rem; text-align: center; margin-top: 0; background: #6b7280; white-space: nowrap;">Return 🏡</a>
            </div>
            <span style="font-style: italic; display: block; margin-top: 1.5rem; font-size: 0.8rem; color: #666; text-align: center; width: 100%;">20260824.1306 ©2025-26 ego/DEV/null</span>
        </div>

        <!-- Mapping Modal -->
        <div id="mappingModal" style="display:none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 2000; justify-content: center; align-items: center;">
            <div class="card" style="display: flex; width: 90%; max-width: 380px; padding: 1.5rem;">
                <h3 style="margin-top: 0; color: #374151;">Edit Mapping</h3>
                
                <div style="width: 100%; text-align: left; margin-bottom: 1rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 4px;">Receipt Merchant (case insensitive)</label>
                    <input type="text" id="mapReceiptMerchant" readonly style="width: 100%; padding: 8px; border: 1px solid #e5e7eb; border-radius: 6px; background: #f3f4f6; color: #6b7280;">
                </div>

                <div style="width: 100%; text-align: left; margin-bottom: 1rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 4px;">Monarch Merchant Name</label>
                    <input type="text" id="mapMonarchMerchant" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px;">
                </div>

                <div style="width: 100%; text-align: left; margin-bottom: 1.5rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 4px;">Category</label>
                    <select id="mapCategory" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; background: white;">
                        <option value="">Loading categories...</option>
                    </select>
                </div>

                <div style="display: flex; gap: 10px; width: 100%;">
                    <button onclick="deleteMapping()" id="deleteMappingBtn" style="padding: 8px 16px; border: 1px solid #fee2e2; background: #fee2e2; border-radius: 6px; cursor: pointer; color: #991b1b; display: none;">Delete</button>
                    <div style="display: flex; gap: 10px; margin-left: auto;">
                        <button onclick="closeMappingModal()" style="padding: 8px 16px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; color: #374151;">Cancel</button>
                        <button onclick="saveMapping()" style="padding: 8px 16px; border: none; background: #4f46e5; color: white; border-radius: 6px; cursor: pointer;">Save</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Delete Confirmation Modal -->
        <div id="deleteConfirmModal" style="display:none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 2001; justify-content: center; align-items: center;">
            <div class="card" style="display: flex; max-width: 350px; padding: 2rem; text-align: center;">
                <div style="font-size: 3rem; margin-bottom: 1rem;">🗑️</div>
                <h3 style="margin-top: 0; color: #1f2937; margin-bottom: 0.5rem;">Delete Mapping?</h3>
                <p style="color: #6b7280; margin-bottom: 1.5rem;">Are you sure you want to delete this mapping rule? Future transactions from this merchant will not be auto-mapped.</p>
                
                <div style="display: flex; gap: 10px; width: 100%; justify-content: center;">
                    <button onclick="closeDeleteConfirm()" style="padding: 8px 16px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; color: #374151;">Cancel</button>
                    <button onclick="confirmDeleteMapping()" style="padding: 8px 16px; border: none; background: #ef4444; color: white; border-radius: 6px; cursor: pointer;">Delete</button>
                </div>
            </div>
        </div>

        <!-- Toast Notification -->
        <div id="toast" class="toast">Mapping saved!</div>

        <!-- History Log Modal -->
        <div id="historyModal"
            style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:2500; justify-content:center; align-items:center;">
            <div style="position:relative; margin: 1rem; max-width: 600px; width: 95%; background: linear-gradient(135deg, #f5d6cd 0%, #e2b4a7 100%); border-radius: 20px; padding: 2rem; box-shadow: 0 10px 25px rgba(0,0,0,0.2); box-sizing: border-box;">
                <span id="closeHistoryModal"
                    style="position:absolute; top: 15px; right: 20px; font-size: 1.5rem; cursor: pointer; color: #aaa;">&times;</span>
                <h2
                    style="color: #4a4a4a; margin-top: 0; display: flex; align-items: center; gap: 10px; font-size: 1.8rem; justify-content: center; font-family: 'Sriracha', cursive;">
                    📜 History Log</h2>

                <div id="historyTableContainer"
                    style="overflow-x: hidden; margin-top: 1rem; max-height: 400px; overflow-y: auto;">
                    <div class="history-header">
                        <div class="history-header-merchant">Merchant</div>
                        <div class="history-header-amount">Amount</div>
                        <div class="history-header-date">Date</div>
                    </div>
                    <div id="historyTableBody">
                        <!-- Loaded dynamically -->
                    </div>
                    <div id="historyLoading" style="text-align: center; padding: 2rem 0; color: #666;">
                        Loading transactions... ⏳
                    </div>
                    <div id="historyNoData" style="display: none; text-align: center; padding: 2rem 0; color: #666;">
                        No transactions processed yet. 📂
                    </div>
                </div>
            </div>
        </div>

        <!-- Failed Transactions Modal -->
        <div id="failedModal"
            style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.5); z-index:2500; justify-content:center; align-items:center;">
            <div style="position:relative; margin: 1rem; max-width: 650px; width: 95%; background: linear-gradient(135deg, #fce4dc 0%, #f7c9bc 100%); border-radius: 20px; padding: 2rem; box-shadow: 0 10px 25px rgba(0,0,0,0.2); box-sizing: border-box;">
                <span id="closeFailedModal"
                    style="position:absolute; top: 15px; right: 20px; font-size: 1.5rem; cursor: pointer; color: #aaa;">&times;</span>
                <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom: 1rem; flex-wrap:wrap; gap:10px;">
                    <h2 style="color: #4a4a4a; margin: 0; display: flex; align-items: center; gap: 8px; font-size: 1.6rem; font-family: 'Sriracha', cursive;">
                        ⚠️ Failed Transactions <span id="failedModalCount" style="font-size: 0.9rem; background: #e53e3e; color: white; border-radius: 999px; padding: 2px 8px; font-family: sans-serif;">0</span>
                    </h2>
                    <div style="display: flex; gap: 8px; margin-right: 28px;">
                        <button id="retryAllFailedBtn" onclick="retryAllFailedTxns()" style="background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); color: white; border: none; padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; font-family: inherit; display: flex; align-items: center; gap: 4px;">
                            <span>🔄 Retry All</span>
                        </button>
                        <button id="clearAllFailedBtn" onclick="clearAllFailedTxns()" style="background: rgba(239, 68, 68, 0.15); color: #b91c1c; border: 1px solid rgba(239, 68, 68, 0.3); padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; font-family: inherit;">
                            <span>🗑️ Clear All</span>
                        </button>
                    </div>
                </div>

                <div id="failedTableContainer"
                    style="overflow-x: hidden; margin-top: 0.5rem; max-height: 420px; overflow-y: auto;">
                    <div id="failedTableBody">
                        <!-- Loaded dynamically -->
                    </div>
                    <div id="failedLoading" style="text-align: center; padding: 2rem 0; color: #666;">
                        Loading failed transactions... ⏳
                    </div>
                    <div id="failedNoData" style="display: none; text-align: center; padding: 2rem 0; color: #666;">
                        No failed transactions! All clear 🎉
                    </div>
                </div>
            </div>
        </div>

        <!-- Edit Failed Transaction Modal -->
        <div id="editFailedModal" style="display:none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 3000; justify-content: center; align-items: center;">
            <div class="card" style="display: flex; width: 90%; max-width: 400px; padding: 1.5rem; background: #fff; border-radius: 16px;">
                <h3 style="margin-top: 0; color: #374151; font-family: 'Sriracha', cursive;">✏️ Edit Failed Transaction</h3>
                <input type="hidden" id="editFailedId">
                
                <div style="width: 100%; text-align: left; margin-bottom: 0.8rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 3px;">Merchant Name</label>
                    <input type="text" id="editFailedMerchant" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box;">
                </div>

                <div style="width: 100%; display: flex; gap: 10px; margin-bottom: 0.8rem;">
                    <div style="flex: 1; text-align: left;">
                        <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 3px;">Amount</label>
                        <input type="number" step="0.01" id="editFailedAmount" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box;">
                    </div>
                    <div style="width: 100px; text-align: left;">
                        <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 3px;">Currency</label>
                        <select id="editFailedCurrency" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; background: white; box-sizing: border-box;">
                            <option value="EUR">EUR</option>
                            <option value="USD">USD</option>
                            <option value="GBP">GBP</option>
                            <option value="JPY">JPY</option>
                            <option value="CZK">CZK</option>
                            <option value="HUF">HUF</option>
                        </select>
                    </div>
                </div>

                <div style="width: 100%; text-align: left; margin-bottom: 0.8rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 3px;">Date</label>
                    <input type="date" id="editFailedDate" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box;">
                </div>

                <div style="width: 100%; text-align: left; margin-bottom: 0.8rem;">
                    <label style="font-size: 0.8rem; color: #6b7280; display: block; margin-bottom: 3px;">Category</label>
                    <select id="editFailedCategory" style="width: 100%; padding: 8px; border: 1px solid #d1d5db; border-radius: 6px; background: white; box-sizing: border-box;">
                        <option value="">Select Category (Optional)</option>
                    </select>
                </div>

                <div style="width: 100%; display: flex; gap: 1rem; margin-bottom: 1.2rem; align-items: center;">
                    <label style="display: flex; align-items: center; gap: 5px; font-size: 0.85rem; color: #4b5563; cursor: pointer;">
                        <input type="checkbox" id="editFailedIsCredit"> Is Credit?
                    </label>
                    <label style="display: flex; align-items: center; gap: 5px; font-size: 0.85rem; color: #4b5563; cursor: pointer;">
                        <input type="checkbox" id="editFailedIsCash"> Is Cash?
                    </label>
                </div>

                <div style="display: flex; gap: 10px; width: 100%; justify-content: flex-end;">
                    <button onclick="closeEditFailedModal()" style="padding: 8px 16px; border: 1px solid #d1d5db; background: white; border-radius: 6px; cursor: pointer; color: #374151;">Cancel</button>
                    <button onclick="saveAndRetryFailedTx()" style="padding: 8px 16px; border: none; background: #4f46e5; color: white; border-radius: 6px; cursor: pointer;">Save & Retry 🚀</button>
                </div>
            </div>
        </div>

        <!-- Manage Starred Merchants Modal -->
        <div id="manageStarredModal" style="display:none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 2500; justify-content: center; align-items: center;">
            <div style="position:relative; margin: 1rem; max-width: 500px; width: 92%; background: linear-gradient(135deg, #ede9fe 0%, #ddd6fe 100%); border-radius: 20px; padding: 1.8rem; box-shadow: 0 10px 25px rgba(0,0,0,0.2); box-sizing: border-box;">
                <span id="closeManageStarredModal" onclick="closeManageStarredModal()" style="position:absolute; top: 15px; right: 20px; font-size: 1.5rem; cursor: pointer; color: #4c1d95; font-weight: bold;">&times;</span>
                <h2 style="color: #4c1d95; margin-top: 0; margin-bottom: 1rem; display: flex; align-items: center; gap: 8px; font-size: 1.5rem; font-family: 'Sriracha', cursive; justify-content: center;">
                    ⭐ Starred Merchants
                </h2>

                <!-- Add Merchant Input -->
                <div style="display: flex; gap: 8px; margin-bottom: 1rem;">
                    <input type="text" id="newMerchantInput" placeholder="New merchant name..." style="flex: 1; height: 42px; padding: 0.5rem 0.8rem; border: 1px solid #8b5cf6; border-radius: 8px; font-size: 0.95rem; box-sizing: border-box; background: white;" onkeydown="if(event.key==='Enter') addNewStarredMerchant()">
                    <button onclick="addNewStarredMerchant()" style="background: linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%); color: white; border: none; padding: 0 16px; border-radius: 8px; cursor: pointer; font-size: 0.95rem; font-weight: bold; height: 42px; display: flex; align-items: center; gap: 4px;">
                        <span>Add ⭐</span>
                    </button>
                </div>

                <!-- Search / Filter Input -->
                <div style="margin-bottom: 0.8rem;">
                    <input type="text" id="searchMerchantsInput" oninput="filterMerchantsList()" placeholder="🔍 Filter merchants..." style="width: 100%; height: 38px; padding: 0.4rem 0.8rem; border: 1px solid rgba(124, 58, 237, 0.4); border-radius: 8px; font-size: 0.9rem; box-sizing: border-box; background: rgba(255,255,255,0.95);">
                </div>

                <!-- Merchants List -->
                <div id="merchantsListContainer" style="max-height: 300px; overflow-y: auto; background: rgba(255,255,255,0.95); border-radius: 12px; padding: 0.5rem; border: 1px solid rgba(124, 58, 237, 0.25);">
                <div id="merchantsListBody">
                    <!-- Loaded dynamically -->
                </div>
                <div id="merchantsLoading" style="text-align: center; padding: 1.5rem 0; color: #4c1d95; font-size: 0.9rem;">
                    Loading merchants... ⏳
                </div>
                <div id="merchantsNoData" style="display: none; text-align: center; padding: 1.5rem 0; color: #4c1d95; font-size: 0.9rem;">
                    No merchants found. Add one above! ⭐
                </div>
            </div>
        </div>
    </div>

        <script>
            const jobId = "__JOB_ID__";
            const pollInterval = 500; // 0.5 seconds
            
            let toastTimeout = null;

            function showToast(message, type = 'success') {
                const toast = document.getElementById("toast");
                if (toastTimeout) clearTimeout(toastTimeout);
                
                toast.textContent = message;
                toast.className = "toast show " + type;
                
                toastTimeout = setTimeout(function(){ 
                    toast.className = toast.className.replace("show", ""); 
                    toastTimeout = null;
                }, 3000);
            }

            function showConfirmToast(message, onConfirm) {
                const toast = document.getElementById("toast");
                if (toastTimeout) clearTimeout(toastTimeout);
                
                toast.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: space-between; gap: 15px;">
                        <span>${message}</span>
                        <div style="display: flex; gap: 8px;">
                            <button id="toastConfirmBtn" style="background: white; color: #be185d; border: none; padding: 4px 10px; border-radius: 4px; font-weight: bold; cursor: pointer; font-size: 0.8rem; font-family: inherit;">Confirm</button>
                            <button id="toastCancelBtn" style="background: rgba(255,255,255,0.2); color: white; border: none; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.8rem; font-family: inherit;">Cancel</button>
                        </div>
                    </div>
                `;
                toast.className = "toast show error";
                
                document.getElementById("toastConfirmBtn").onclick = () => {
                    toast.className = toast.className.replace("show", "");
                    onConfirm();
                };
                
                document.getElementById("toastCancelBtn").onclick = () => {
                    toast.className = toast.className.replace("show", "");
                };
            }

            function forceSubmit() {
                document.getElementById('resultCard').style.display = 'none';
                document.getElementById('loadingOverlay').style.display = 'flex';
                document.getElementById('loadingSubtitle').textContent = "forcing submission...";
                
                fetch(`/job/${jobId}/retry?force=true`, { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        console.log("Retry started");
                        setTimeout(checkStatus, pollInterval);
                    })
                    .catch(err => showError("Retry failed: " + err));
            }
            
            function checkStatus() {
                fetch(`/job/${jobId}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'completed') {
                            showSuccess(data.result);
                        } else if (data.status === 'failed') {
                            showError(data.error);
                        } else {
                            if (data.step) {
                                document.getElementById('loadingSubtitle').textContent = data.step;
                            }
                            if (data.progress !== undefined) {
                                const bar = document.getElementById('progressBar');
                                if (bar) bar.style.width = data.progress + '%';
                            }
                            setTimeout(checkStatus, pollInterval);
                        }
                    })
                    .catch(err => {
                        console.error("Polling error:", err);
                        showError("Communication error: " + err);
                    });
            }
            
            function showSuccess(result) {
                document.getElementById('loadingOverlay').style.display = 'none';
                document.getElementById('resultCard').style.display = 'flex';
                document.getElementById('successActions').style.display = 'flex';
                document.getElementById('errorActions').style.display = 'none';
                document.getElementById('errorContainer').style.display = 'none';
                document.getElementById('detailsContainer').style.display = 'block';
                
                const data = result.status === 'duplicate' ? result.data : result;
                const isDuplicate = result.status === 'duplicate';
                
                if (isDuplicate) {
                    document.getElementById('cardIcon').textContent = '⚠️';
                    document.getElementById('cardTitle').textContent = 'Already Processed';
                    document.getElementById('cardTitle').style.color = '#856404';
                    document.getElementById('forceSubmitBtn').style.display = 'inline-block';
                    document.getElementById('editMappingBtn').style.display = 'none';
                } else {
                    document.getElementById('cardIcon').textContent = '🎉';
                    document.getElementById('cardTitle').textContent = 'Transaction Processed';
                    document.getElementById('cardTitle').style.color = 'green';
                    document.getElementById('forceSubmitBtn').style.display = 'none';
                    document.getElementById('editMappingBtn').style.display = 'inline-block';

                    confetti({
                        particleCount: 150,
                        spread: 70,
                        origin: { y: 0.6 }
                    });
                }
                
                let amountHtml = `${parseFloat(data.amount).toFixed(2)} ${data.currency}`;
                
                if (data.monarch_tx_id) {
                    const deepLink = `intent://transactions/${data.monarch_tx_id}#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%2F${data.monarch_tx_id};end`;
                    const linkColor = data.is_credit ? "#16a34a" : "#2563eb";
                    amountHtml = `<a href="${deepLink}" style="text-decoration:none; color:${linkColor};">${amountHtml}</a>`;
                }
                
                if (data.original_amount && data.original_currency) {
                    let rateInfo = "";
                    if (data.exchange_rate) {
                        rateInfo = ` @ ${parseFloat(data.exchange_rate).toFixed(3)}`;
                    }
                    amountHtml += `<br><span style="font-size: 0.8em; color: #352224;">(${parseFloat(data.original_amount).toFixed(2)} ${data.original_currency}${rateInfo})</span>`;
                }
                
                if (!isDuplicate) {
                    if (data.used_historical_name || data.original_merchant_name) {
                        document.getElementById('editMappingBtn').style.display = 'inline-block';
                        document.getElementById('editMappingBtn').textContent = "Edit Mapping";
                    } else {
                        document.getElementById('editMappingBtn').style.display = 'inline-block';
                        document.getElementById('editMappingBtn').textContent = "Add Mapping";
                    }
                } else {
                    document.getElementById('editMappingBtn').style.display = 'none';
                }

                document.getElementById('amountValue').innerHTML = amountHtml;

                const isHistorical = !!data.used_historical_name;

                document.getElementById('merchantValue').textContent =
                    (isHistorical ? '💜 ' : '') + data.merchant;

                const isStarred = !!data.is_starred;
                window.currentMerchantStarred = isStarred;
                updateStarIcon(isStarred);

                if (data.merchant) {
                    fetch(`/api/merchants/${encodeURIComponent(data.merchant)}/status`)
                        .then(res => res.json())
                        .then(statusData => {
                            if (statusData && typeof statusData.is_starred === 'boolean') {
                                window.currentMerchantStarred = statusData.is_starred;
                                updateStarIcon(statusData.is_starred);
                            }
                        })
                        .catch(err => console.error("Error fetching merchant status:", err));
                }

                document.getElementById('dateValue').innerHTML = data.date;
                document.getElementById('datePicker').value = data.date;

                let catDisplay = data.category_name || "--";
                if (data.category_emoji) {
                    catDisplay = data.category_emoji + " " + catDisplay;
                }
                document.getElementById('categoryValue').textContent =
                    (isHistorical ? '💜 ' : '') + catDisplay;

                document.getElementById('historicalLegend').style.display = isHistorical ? 'block' : 'none';

                const accountValueEl = document.getElementById('accountValue');
                if (accountValueEl) {
                    accountValueEl.textContent = data.is_cash ? "Cash On Hand" : "__MM_ACCOUNT__";
                }
                
                window.currentTransactionData = data;
                fetchCategories();
                updateFailedBadgeCount();
            }

            async function confirmDeleteMapping() {
                closeDeleteConfirm();
                const receiptName = document.getElementById('mapReceiptMerchant').value;
                const btn = document.getElementById('deleteMappingBtn');
                const origText = btn.textContent;
                btn.textContent = "Deleting...";
                btn.disabled = true;
                
                try {
                     const res = await fetch('/api/mapping', {
                        method: 'DELETE',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ receipt_merchant_name: receiptName })
                    });
                    
                    if (res.ok) {
                         showToast("Mapping deleted.", "success");
                         closeMappingModal();
                         document.getElementById('editMappingBtn').textContent = "Add Mapping";
                         if (window.currentTransactionData) {
                             delete window.currentTransactionData.original_merchant_name;
                         }
                    } else {
                        const err = await res.json();
                        showToast("Error deleting: " + err.detail, "error");
                    }
                } catch (e) {
                    showToast("Network error: " + e, "error");
                } finally {
                    btn.textContent = origText;
                    btn.disabled = false;
                }
            }
            
            async function saveMapping() {
                const payload = {
                    receipt_merchant_name: document.getElementById('mapReceiptMerchant').value,
                    monarch_merchant_name: document.getElementById('mapMonarchMerchant').value,
                    category_name: document.getElementById('mapCategory').value,
                    monarch_tx_id: window.currentTransactionData?.monarch_tx_id
                };

                if (!payload.category_name) {
                    showToast("Please select a category", "error");
                    return;
                }

                const btn = document.querySelector('#mappingModal button:last-child');
                const origText = btn.textContent;
                btn.textContent = "Saving...";
                btn.disabled = true;

                try {
                    const res = await fetch('/api/mapping', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload)
                    });

                    if (res.ok) {
                        showToast("Mapping saved! Transaction updated in Monarch.", "success");
                        closeMappingModal();
                        
                        if (!window.currentTransactionData.original_merchant_name) {
                            window.currentTransactionData.original_merchant_name = payload.receipt_merchant_name;
                        }
                        window.currentTransactionData.merchant = payload.monarch_merchant_name;
                        window.currentTransactionData.category_name = payload.category_name;
                        
                        document.getElementById('editMappingBtn').textContent = "Edit Mapping";
                        document.getElementById('merchantValue').textContent = payload.monarch_merchant_name;
                        
                        let emoji = "";
                        if (cachedCategories) {
                             const catObj = cachedCategories.find(c => c.name === payload.category_name);
                             if (catObj) emoji = catObj.emoji;
                        }
                        document.getElementById('categoryValue').textContent = (emoji ? emoji + " " : "") + payload.category_name;
                    } else {
                        const err = await res.json();
                        showToast("Error saving: " + err.detail, "error");
                    }
                } catch (e) {
                    showToast("Network error: " + e, "error");
                } finally {
                    btn.textContent = origText;
                    btn.disabled = false;
                }
            }
            
            // --- Mapping Logic ---
            let cachedCategories = null;

            async function fetchCategories() {
                if (cachedCategories) return;
                try {
                    const res = await fetch('/api/categories');
                    const data = await res.json();
                    cachedCategories = data.categories;
                    populateCategoryDropdown();
                } catch (e) {
                    console.error("Failed to fetch categories", e);
                }
            }

            function populateCategoryDropdown() {
                const select = document.getElementById('mapCategory');
                if (select) {
                    select.innerHTML = '<option value="">Select Category</option>';
                    if (cachedCategories) {
                        cachedCategories.forEach(cat => {
                            const opt = document.createElement('option');
                            opt.value = cat.name;
                            opt.textContent = (cat.emoji ? cat.emoji + " " : "") + cat.name;
                            select.appendChild(opt);
                        });
                    }
                }
                const editSelect = document.getElementById('editFailedCategory');
                if (editSelect && cachedCategories) {
                    editSelect.innerHTML = '<option value="">Select Category (Optional)</option>';
                    cachedCategories.forEach(cat => {
                        const opt = document.createElement('option');
                        opt.value = cat.name;
                        opt.textContent = (cat.emoji ? cat.emoji + " " : "") + cat.name;
                        editSelect.appendChild(opt);
                    });
                }
            }

            function openMappingModal() {
                const data = window.currentTransactionData;
                if (!data) return;

                const receiptName = data.original_merchant_name || data.merchant;
                document.getElementById('mapReceiptMerchant').value = String(receiptName).toLowerCase();
                document.getElementById('mapMonarchMerchant').value = data.merchant;
                
                if (data.original_merchant_name) {
                    document.getElementById('deleteMappingBtn').style.display = 'block';
                } else {
                    document.getElementById('deleteMappingBtn').style.display = 'none';
                }
                
                if (data.category_name) {
                     const select = document.getElementById('mapCategory');
                     for(let i=0; i<select.options.length; i++) {
                         if (select.options[i].value === data.category_name) {
                             select.selectedIndex = i;
                             break;
                         }
                     }
                }

                document.getElementById('mappingModal').style.display = 'flex';
            }

            function deleteMapping() {
                document.getElementById('deleteConfirmModal').style.display = 'flex';
            }
            
            function closeDeleteConfirm() {
                document.getElementById('deleteConfirmModal').style.display = 'none';
            }

            function closeMappingModal() {
                document.getElementById('mappingModal').style.display = 'none';
            }
            
            function showError(msg) {
                console.error("Debug Error:", msg);
                document.getElementById('loadingOverlay').style.display = 'none';
                document.getElementById('resultCard').style.display = 'flex';
                
                document.getElementById('cardIcon').textContent = '⚠️';
                document.getElementById('cardTitle').textContent = 'Oops! Import Failed';
                document.getElementById('cardTitle').style.color = '#e53e3e';
                
                document.getElementById('detailsContainer').style.display = 'none';
                document.getElementById('historicalLegend').style.display = 'none';
                document.getElementById('errorContainer').style.display = 'block';
                document.getElementById('errorMessage').textContent = msg;
                document.getElementById('successActions').style.display = 'none';
                document.getElementById('errorActions').style.display = 'flex';
                
                updateFailedBadgeCount();
            }

            // ── Editable Date ──────────────────────────────────────────────
            function openDatePicker() {
                const picker = document.getElementById('datePicker');
                const currentText = document.getElementById('dateValue').textContent.replace(/[^\\d\\-]/g, '').trim();
                if (currentText) picker.value = currentText;
                picker.showPicker ? picker.showPicker() : picker.click();
            }

            document.getElementById('datePicker').addEventListener('change', async function() {
                const newDate = this.value;
                await onDateChanged(newDate);
            });

            async function onDateChanged(newDate) {
                const data = window.currentTransactionData;
                if (!data || !data.monarch_tx_id) {
                    showToast('Cannot update date: no transaction ID.', 'error');
                    return;
                }

                if (newDate === data.date) return;

                const pill = document.getElementById('dateValue');
                pill.classList.add('updating');
                pill.innerHTML = newDate + '&#160;⏳';

                try {
                    const payload = {
                        monarch_tx_id: data.monarch_tx_id,
                        new_date: newDate,
                        original_currency: data.original_currency || data.currency || 'USD',
                        original_amount: data.original_amount ?? data.amount,
                        is_credit: data.is_credit || false
                    };

                    const res = await fetch('/api/transaction/update-date', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (!res.ok) {
                        const err = await res.json();
                        throw new Error(err.detail || 'Update failed');
                    }

                    const result = await res.json();

                    data.date = result.new_date;
                    data.amount = result.new_amount;
                    if (result.exchange_rate !== null && result.exchange_rate !== undefined) {
                        data.exchange_rate = result.exchange_rate;
                    }

                    pill.innerHTML = result.new_date;
                    document.getElementById('datePicker').value = result.new_date;

                    let amountHtml = `${parseFloat(result.new_amount).toFixed(2)} ${data.currency}`;
                    if (data.monarch_tx_id) {
                        const deepLink = `intent://transactions/${data.monarch_tx_id}#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%2F${data.monarch_tx_id};end`;
                        const linkColor = data.is_credit ? '#16a34a' : '#2563eb';
                        amountHtml = `<a href="${deepLink}" style="text-decoration:none; color:${linkColor};">${amountHtml}</a>`;
                    }
                    if (data.original_amount && data.original_currency) {
                        let rateInfo = '';
                        if (result.exchange_rate) {
                            rateInfo = ` @ ${parseFloat(result.exchange_rate).toFixed(3)}`;
                        }
                        amountHtml += `<br><span style="font-size: 0.8em; color: #352224;">(${parseFloat(data.original_amount).toFixed(2)} ${data.original_currency}${rateInfo})</span>`;
                    }
                    document.getElementById('amountValue').innerHTML = amountHtml;

                    showToast('✅ Date & rate updated!', 'success');

                } catch(e) {
                    const revDate = document.getElementById('datePicker').value || (data.date || '--');
                    pill.innerHTML = revDate;
                    showToast('Error: ' + e.message, 'error');
                } finally {
                    pill.classList.remove('updating');
                }
            }

            // ── Editable Category ──────────────────────────────────────────
            async function openCategorySelector() {
                const select = document.getElementById('inlineCategorySelect');
                const pill = document.getElementById('categoryValue');
                
                await fetchCategories();
                
                select.innerHTML = '<option value="">Select Category</option>';
                if (cachedCategories) {
                    cachedCategories.forEach(cat => {
                        const opt = document.createElement('option');
                        opt.value = cat.name;
                        opt.textContent = (cat.emoji ? cat.emoji + " " : "") + cat.name;
                        select.appendChild(opt);
                    });
                }
                
                const data = window.currentTransactionData;
                if (data && data.category_name) {
                    for(let i=0; i<select.options.length; i++) {
                        if (select.options[i].value === data.category_name) {
                            select.selectedIndex = i;
                            break;
                        }
                    }
                }
                
                pill.style.display = 'none';
                select.style.display = 'inline-block';
                select.focus();
            }

            document.getElementById('inlineCategorySelect').addEventListener('change', async function() {
                const newCategory = this.value;
                if (!newCategory) {
                    closeCategorySelector();
                    return;
                }
                await onCategoryChanged(newCategory);
            });

            document.getElementById('inlineCategorySelect').addEventListener('blur', function() {
                closeCategorySelector();
            });

            function closeCategorySelector() {
                document.getElementById('categoryValue').style.display = 'inline-flex';
                document.getElementById('inlineCategorySelect').style.display = 'none';
            }

            async function onCategoryChanged(newCategory) {
                const data = window.currentTransactionData;
                if (!data || !data.monarch_tx_id) {
                    showToast('Cannot update category: no transaction ID.', 'error');
                    closeCategorySelector();
                    return;
                }

                if (newCategory === data.category_name) {
                    closeCategorySelector();
                    return;
                }

                const pill = document.getElementById('categoryValue');
                pill.classList.add('updating');
                pill.innerHTML = newCategory + '&#160;⏳';
                closeCategorySelector();

                try {
                    const payload = {
                        monarch_tx_id: data.monarch_tx_id,
                        category_name: newCategory
                    };

                    const res = await fetch('/api/transaction/update-category', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (!res.ok) {
                        const err = await res.json();
                        throw new Error(err.detail || 'Update failed');
                    }

                    const result = await res.json();

                    data.category_name = newCategory;
                    if (result.category_emoji !== undefined) {
                        data.category_emoji = result.category_emoji;
                    } else {
                        delete data.category_emoji;
                    }

                    const isHistorical = !!data.used_historical_name;
                    let catDisplay = newCategory;
                    if (data.category_emoji) {
                        catDisplay = data.category_emoji + ' ' + catDisplay;
                    }
                    pill.innerHTML = (isHistorical ? '💜 ' : '') + catDisplay;

                    showToast('✅ Category updated in Monarch!', 'success');

                } catch(e) {
                    const isHistorical = !!data.used_historical_name;
                    let origDisplay = data.category_name || '--';
                    if (data.category_emoji) {
                        origDisplay = data.category_emoji + ' ' + origDisplay;
                    }
                    pill.innerHTML = (isHistorical ? '💜 ' : '') + origDisplay;
                    showToast('Error: ' + e.message, 'error');
                } finally {
                    pill.classList.remove('updating');
                }
            }

            // --- Hamburger Menu and History Modal Logic ---
            const dlTrigger = document.getElementById('deepLinkTrigger');
            const dlDropdown = document.getElementById('deepLinkDropdown');

            dlTrigger.addEventListener('click', (e) => {
                e.stopPropagation();
                const isOpen = dlDropdown.style.display === 'flex';
                if (isOpen) {
                    dlDropdown.style.display = 'none';
                    dlTrigger.classList.remove('open');
                } else {
                    dlDropdown.style.display = 'flex';
                    dlTrigger.classList.add('open');
                }
            });

            document.addEventListener('click', (e) => {
                if (!dlTrigger.contains(e.target) && !dlDropdown.contains(e.target)) {
                    dlDropdown.style.display = 'none';
                    dlTrigger.classList.remove('open');
                }
            });

            const dlLinks = document.querySelectorAll('.deep-link-item');
            dlLinks.forEach(link => {
                link.addEventListener('click', () => {
                    dlDropdown.style.display = 'none';
                    dlTrigger.classList.remove('open');
                });
            });

            // Update App Logic
            const updateAppBtn = document.getElementById('updateAppLink');
            if (updateAppBtn) {
                updateAppBtn.addEventListener('click', async (e) => {
                    e.preventDefault();
                    showToast("Updating app...", "success");
                    try {
                        if ('serviceWorker' in navigator) {
                            const registrations = await navigator.serviceWorker.getRegistrations();
                            for (let registration of registrations) {
                                await registration.unregister();
                            }
                        }
                        if ('caches' in window) {
                            const cacheNames = await caches.keys();
                            await Promise.all(cacheNames.map(name => caches.delete(name)));
                        }
                    } catch (err) {
                        console.error("Failed to clear app cache:", err);
                    }
                    const url = new URL('/', window.location.origin);
                    url.searchParams.set('v', Date.now());
                    url.searchParams.set('updated', '1');
                    window.location.href = url.toString();
                });
            }

            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get('updated') === '1') {
                showToast("✨ Updated to the latest version!", "success");
                urlParams.delete('updated');
                urlParams.delete('v');
                const newSearch = urlParams.toString() ? ('?' + urlParams.toString()) : '';
                const cleanUrl = window.location.pathname + newSearch + window.location.hash;
                window.history.replaceState({}, document.title, cleanUrl);
            }

            // Touch handlers for swipe-to-delete on mobile
            let touchStartX = 0;
            let touchStartY = 0;
            let isSwiping = false;

            function initSwipeToDelete(rowWrapper) {
                const content = rowWrapper.querySelector('.history-row-content');
                let dx = 0;
                
                rowWrapper.addEventListener('touchstart', (e) => {
                    touchStartX = e.touches[0].clientX;
                    touchStartY = e.touches[0].clientY;
                    dx = 0;
                    isSwiping = false;
                    rowWrapper.style.transition = 'none';
                }, { passive: true });

                rowWrapper.addEventListener('touchmove', (e) => {
                    const currentX = e.touches[0].clientX;
                    const currentY = e.touches[0].clientY;
                    dx = touchStartX - currentX;
                    const dy = Math.abs(touchStartY - currentY);
                    
                    if (dy > Math.abs(dx)) return;

                    if (Math.abs(dx) > 10) isSwiping = true;

                    if (isSwiping) {
                        if (e.cancelable) e.preventDefault();
                        const baseTranslate = rowWrapper.classList.contains('swiped') ? -80 : 0;
                        let targetX = baseTranslate - dx;

                        if (targetX > 0) targetX = 0;
                        else if (targetX < -120) targetX = -120;

                        rowWrapper.style.transform = `translateX(${targetX}px)`;
                    }
                }, { passive: false });

                rowWrapper.addEventListener('touchend', (e) => {
                    if (!isSwiping) return;
                    
                    rowWrapper.style.transition = 'transform 0.15s ease-out';
                    const baseTranslate = rowWrapper.classList.contains('swiped') ? -80 : 0;
                    const finalTranslate = baseTranslate - dx;

                    if (finalTranslate < -35) {
                        document.querySelectorAll('.history-row-wrapper.swiped').forEach(el => {
                            if (el !== rowWrapper) {
                                el.style.transition = 'transform 0.15s ease-out';
                                el.style.transform = 'translateX(0)';
                                el.classList.remove('swiped');
                            }
                        });
                        rowWrapper.style.transform = 'translateX(-80px)';
                        rowWrapper.classList.add('swiped');
                    } else {
                        rowWrapper.style.transform = 'translateX(0)';
                        rowWrapper.classList.remove('swiped');
                    }
                }, { passive: true });

                window.addEventListener('touchstart', (e) => {
                    if (!rowWrapper.contains(e.target) && rowWrapper.classList.contains('swiped')) {
                        rowWrapper.style.transition = 'transform 0.15s ease-out';
                        rowWrapper.style.transform = 'translateX(0)';
                        rowWrapper.classList.remove('swiped');
                    }
                }, { passive: true });
            }

            async function deleteLogEntry(logId, rowWrapper) {
                try {
                    const res = await fetch(`/api/logs/${logId}`, {
                        method: 'DELETE'
                    });
                    if (res.ok) {
                        showToast("Log entry deleted", "success");
                        rowWrapper.style.transition = "max-height 0.3s ease-out, opacity 0.3s ease-out, padding 0.3s ease-out";
                        rowWrapper.style.maxHeight = rowWrapper.offsetHeight + "px";
                        rowWrapper.offsetHeight;
                        rowWrapper.style.maxHeight = "0";
                        rowWrapper.style.opacity = "0";
                        rowWrapper.style.paddingTop = "0";
                        rowWrapper.style.paddingBottom = "0";
                        rowWrapper.style.border = "none";
                        setTimeout(() => {
                            rowWrapper.remove();
                            if (historyTableBody.children.length === 0) {
                                historyNoData.style.display = "block";
                            }
                        }, 300);
                    } else {
                        const err = await res.json();
                        showToast("Error deleting: " + err.detail, "error");
                    }
                } catch (e) {
                    showToast("Failed to delete transaction: " + e, "error");
                }
            }

            // History Modal Logic
            const historyModal = document.getElementById('historyModal');
            const openHistoryBtn = document.getElementById('historyLogLink');
            const closeHistoryBtn = document.getElementById('closeHistoryModal');
            const historyTableBody = document.getElementById('historyTableBody');
            const historyLoading = document.getElementById('historyLoading');
            const historyNoData = document.getElementById('historyNoData');

            openHistoryBtn.onclick = function (e) {
                e.preventDefault();
                historyModal.style.display = "flex";
                fetchHistoryLogs();
            }

            function dismissDeleteConfirmation() {
                const toast = document.getElementById("toast");
                if (toast && toast.className.includes("show") && document.getElementById("toastConfirmBtn")) {
                    toast.className = toast.className.replace("show", "");
                }
            }

            closeHistoryBtn.onclick = function () {
                historyModal.style.display = "none";
                dismissDeleteConfirmation();
            }

            async function fetchHistoryLogs() {
                try {
                    historyLoading.style.display = "block";
                    historyNoData.style.display = "none";
                    historyTableBody.innerHTML = "";

                    const res = await fetch("/api/logs");
                    if (!res.ok) throw new Error("Failed to fetch logs");
                    const logs = await res.json();

                    historyLoading.style.display = "none";

                    if (logs.length === 0) {
                        historyNoData.style.display = "block";
                        return;
                    }

                    logs.forEach(log => {
                        const rowWrapper = document.createElement("div");
                        rowWrapper.className = "history-row-wrapper";

                        const contentRow = document.createElement("div");
                        contentRow.className = "history-row-content";

                        const merchantCol = document.createElement("div");
                        merchantCol.style.flex = "2";
                        merchantCol.style.textAlign = "left";
                        
                        const merchantText = log.merchant;
                        const cashEmoji = log.is_cash ? " 💵" : "";

                        if (log.monarch_tx_id) {
                            const deepLink = `intent://transactions/${log.monarch_tx_id}#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%2F${log.monarch_tx_id};end`;
                            merchantCol.innerHTML = `<a href="${deepLink}" target="_blank" style="text-decoration: underline; color: #667eea;" title="View in Monarch">${merchantText}</a>${cashEmoji}`;
                        } else {
                            merchantCol.textContent = merchantText + cashEmoji;
                        }
                        contentRow.appendChild(merchantCol);

                        const amountCol = document.createElement("div");
                        amountCol.style.flex = "1";
                        amountCol.style.textAlign = "right";
                        amountCol.style.marginRight = "12px";
                        
                        const isPositive = log.amount >= 0;
                        amountCol.className = isPositive ? "amount-green" : "amount-red";

                        const getCurrencySymbol = (code) => {
                            if (code === "USD") return "$";
                            if (code === "EUR") return "€";
                            if (code === "GBP") return "£";
                            if (code === "JPY") return "¥";
                            return code + " ";
                        };

                        const symbol = getCurrencySymbol(log.currency);
                        const prefix = isPositive ? "+" : "-";
                        const absAmount = Math.abs(log.amount).toFixed(2);
                        let amountText = `${prefix}${symbol}${absAmount}`;

                        if (log.original_amount && log.original_currency) {
                            const originalText = `(${parseFloat(log.original_amount).toFixed(2)} ${log.original_currency})`;
                            amountCol.innerHTML = `<span>${amountText}</span><br><span style="font-size: 0.75rem; color: #352224; font-style: italic; font-weight: normal;">${originalText}</span>`;
                        } else {
                            amountCol.textContent = amountText;
                        }
                        contentRow.appendChild(amountCol);

                        const dateCol = document.createElement("div");
                        dateCol.style.flex = "1";
                        dateCol.style.textAlign = "center";
                        dateCol.style.maxWidth = "90px";
                        dateCol.textContent = log.date;
                        contentRow.appendChild(dateCol);

                        rowWrapper.appendChild(contentRow);

                        const deleteBtn = document.createElement("div");
                        deleteBtn.className = "history-row-delete-btn";
                        deleteBtn.textContent = "Delete";
                        deleteBtn.onclick = (e) => {
                            e.stopPropagation();
                            showConfirmToast(`Delete "${log.merchant}"?`, async () => {
                                await deleteLogEntry(log.id, rowWrapper);
                            });
                        };
                        rowWrapper.appendChild(deleteBtn);
                        
                        initSwipeToDelete(rowWrapper);

                        historyTableBody.appendChild(rowWrapper);
                    });
                } catch (err) {
                    console.error(err);
                    historyLoading.style.display = "none";
                    historyTableBody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: #dc2626; padding: 2rem 0;">Error loading history logs.</td></tr>`;
                }
            }

            // =========================================================
            // ⚠️ Failed Transactions Logic
            // =========================================================
            const failedModal = document.getElementById('failedModal');
            const closeFailedBtn = document.getElementById('closeFailedModal');
            const failedTableBody = document.getElementById('failedTableBody');
            const failedLoading = document.getElementById('failedLoading');
            const failedNoData = document.getElementById('failedNoData');
            const editFailedModal = document.getElementById('editFailedModal');

            let failedTransactionsCache = [];

            async function updateFailedBadgeCount() {
                try {
                    const res = await fetch('/api/failed-transactions/count');
                    if (res.ok) {
                        const data = await res.json();
                        const count = data.count || 0;
                        const badge = document.getElementById('failedBadge');
                        const modalCount = document.getElementById('failedModalCount');
                        if (badge) {
                            badge.textContent = count;
                            badge.style.display = count > 0 ? 'inline-block' : 'none';
                        }
                        if (modalCount) {
                            modalCount.textContent = count;
                        }
                    }
                } catch (e) {
                    console.error("Failed to update badge count:", e);
                }
            }

            function openFailedModal(e) {
                if (e) e.preventDefault();
                failedModal.style.display = "flex";
                fetchFailedTransactions();
            }

            closeFailedBtn.onclick = function () {
                failedModal.style.display = "none";
            };

            window.addEventListener('click', (event) => {
                if (event.target === historyModal) {
                    historyModal.style.display = "none";
                    dismissDeleteConfirmation();
                }
                if (event.target === failedModal) {
                    failedModal.style.display = "none";
                }
                if (event.target === editFailedModal) {
                    closeEditFailedModal();
                }
            });

            async function fetchFailedTransactions() {
                try {
                    failedLoading.style.display = "block";
                    failedNoData.style.display = "none";
                    failedTableBody.innerHTML = "";

                    const res = await fetch("/api/failed-transactions");
                    if (!res.ok) throw new Error("Failed to fetch failed transactions");
                    failedTransactionsCache = await res.json();

                    failedLoading.style.display = "none";

                    if (failedTransactionsCache.length === 0) {
                        failedNoData.style.display = "block";
                        updateFailedBadgeCount();
                        return;
                    }

                    renderFailedTransactions(failedTransactionsCache);
                    updateFailedBadgeCount();
                } catch (err) {
                    console.error(err);
                    failedLoading.style.display = "none";
                    failedTableBody.innerHTML = `<div style="text-align: center; color: #dc2626; padding: 2rem 0;">Error loading failed transactions: ${err.message}</div>`;
                }
            }

            function renderFailedTransactions(items) {
                failedTableBody.innerHTML = "";
                if (items.length === 0) {
                    failedNoData.style.display = "block";
                    return;
                }
                failedNoData.style.display = "none";

                items.forEach(tx => {
                    const card = document.createElement("div");
                    card.className = "failed-item-card";
                    card.id = `failed-card-${tx.id}`;

                    const sourceIcon = tx.source_type === "manual" ? "✍️ Manual" : "🧾 Receipt";
                    const amountDisplay = tx.amount !== null && tx.amount !== undefined 
                        ? `${parseFloat(tx.amount).toFixed(2)} ${tx.currency}` 
                        : (tx.source_type === "receipt" ? "Amount Pending" : "0.00");
                    
                    const dateDisplay = tx.date || (tx.created_at ? tx.created_at.split("T")[0] : "");
                    const imageBtn = tx.has_image 
                        ? `<a href="/api/failed-transactions/${tx.id}/image" target="_blank" style="text-decoration:none; font-size:1rem;" title="View Receipt Image">🖼️</a>` 
                        : "";

                    const retryCountBadge = tx.retry_count > 0 
                        ? `<span style="font-size:0.75rem; color:#b91c1c; margin-left:6px;">(Retried ${tx.retry_count}x)</span>` 
                        : "";

                    card.innerHTML = `
                        <div class="failed-item-header">
                            <div class="failed-item-merchant">
                                <span style="font-size: 0.8rem; background: rgba(102,126,234,0.15); color: #4f46e5; padding: 2px 6px; border-radius: 4px;">${sourceIcon}</span>
                                <span>${tx.merchant}</span>
                                ${imageBtn}
                                ${retryCountBadge}
                            </div>
                            <div class="failed-item-amount">${amountDisplay}</div>
                        </div>
                        <div class="failed-error-banner">
                            ⚠️ ${tx.error_message}
                        </div>
                        <div class="failed-item-actions">
                            <div class="failed-meta">
                                📅 ${dateDisplay} ${tx.category_name ? `• 🏷️ ${tx.category_name}` : ''}
                            </div>
                            <div class="failed-btn-group">
                                <button class="btn-failed-retry" onclick="retrySingleFailedTx(${tx.id}, this)">🔄 Retry</button>
                                <button class="btn-failed-edit" onclick="openEditFailedModal(${tx.id})">✏️ Edit</button>
                                <button class="btn-failed-del" onclick="deleteSingleFailedTx(${tx.id})">🗑️</button>
                            </div>
                        </div>
                    `;

                    failedTableBody.appendChild(card);
                });
            }

            async function retrySingleFailedTx(id, btn) {
                const origText = btn ? btn.textContent : "Retry";
                if (btn) {
                    btn.textContent = "Retrying... ⏳";
                    btn.disabled = true;
                }

                try {
                    const res = await fetch(`/api/failed-transactions/${id}/retry`, {
                        method: 'POST'
                    });

                    if (res.ok) {
                        showToast("✅ Transaction imported successfully!", "success");
                        const card = document.getElementById(`failed-card-${id}`);
                        if (card) {
                            card.style.transition = "all 0.3s ease";
                            card.style.opacity = "0";
                            card.style.transform = "scale(0.95)";
                            setTimeout(() => {
                                card.remove();
                                failedTransactionsCache = failedTransactionsCache.filter(t => t.id !== id);
                                if (failedTransactionsCache.length === 0) {
                                    failedNoData.style.display = "block";
                                }
                                updateFailedBadgeCount();
                            }, 300);
                        }
                    } else {
                        const err = await res.json();
                        showToast("❌ " + (err.detail || "Retry failed"), "error");
                        await fetchFailedTransactions();
                    }
                } catch (e) {
                    showToast("❌ Network error: " + e.message, "error");
                } finally {
                    if (btn) {
                        btn.textContent = origText;
                        btn.disabled = false;
                    }
                }
            }

            async function retryAllFailedTxns() {
                const btn = document.getElementById('retryAllFailedBtn');
                const origText = btn.innerHTML;
                btn.innerHTML = "<span>Retrying All... ⏳</span>";
                btn.disabled = true;

                try {
                    const res = await fetch('/api/failed-transactions/retry-all', {
                        method: 'POST'
                    });

                    if (res.ok) {
                        const result = await res.json();
                        showToast(`Processed ${result.total}: ${result.succeeded} succeeded, ${result.failed} failed.`, result.failed === 0 ? "success" : "error");
                        await fetchFailedTransactions();
                    } else {
                        const err = await res.json();
                        showToast("Error: " + (err.detail || "Bulk retry failed"), "error");
                    }
                } catch (e) {
                    showToast("Network error: " + e.message, "error");
                } finally {
                    btn.innerHTML = origText;
                    btn.disabled = false;
                }
            }

            async function clearAllFailedTxns() {
                showConfirmToast("Clear ALL failed transactions?", async () => {
                    try {
                        const res = await fetch('/api/failed-transactions', {
                            method: 'DELETE'
                        });
                        if (res.ok) {
                            showToast("All failed transactions cleared", "success");
                            await fetchFailedTransactions();
                        } else {
                            const err = await res.json();
                            showToast("Error: " + err.detail, "error");
                        }
                    } catch (e) {
                        showToast("Network error: " + e.message, "error");
                    }
                });
            }

            async function deleteSingleFailedTx(id) {
                showConfirmToast("Delete this failed transaction?", async () => {
                    try {
                        const res = await fetch(`/api/failed-transactions/${id}`, {
                            method: 'DELETE'
                        });
                        if (res.ok) {
                            showToast("Failed transaction deleted", "success");
                            const card = document.getElementById(`failed-card-${id}`);
                            if (card) {
                                card.remove();
                                failedTransactionsCache = failedTransactionsCache.filter(t => t.id !== id);
                                if (failedTransactionsCache.length === 0) {
                                    failedNoData.style.display = "block";
                                }
                                updateFailedBadgeCount();
                            }
                        } else {
                            const err = await res.json();
                            showToast("Error: " + err.detail, "error");
                        }
                    } catch (e) {
                        showToast("Network error: " + e.message, "error");
                    }
                });
            }

            async function openEditFailedModal(id) {
                const tx = failedTransactionsCache.find(t => t.id === id);
                if (!tx) return;

                await fetchCategories();

                document.getElementById('editFailedId').value = tx.id;
                document.getElementById('editFailedMerchant').value = tx.merchant !== "Receipt (OCR Pending)" ? tx.merchant : "";
                document.getElementById('editFailedAmount').value = tx.amount !== null && tx.amount !== undefined ? tx.amount : "";
                document.getElementById('editFailedCurrency').value = tx.currency || "EUR";
                document.getElementById('editFailedDate').value = tx.date || new Date().toISOString().split('T')[0];
                document.getElementById('editFailedIsCredit').checked = !!tx.is_credit;
                document.getElementById('editFailedIsCash').checked = !!tx.is_cash;

                const catSelect = document.getElementById('editFailedCategory');
                if (catSelect && tx.category_name) {
                    for (let i = 0; i < catSelect.options.length; i++) {
                        if (catSelect.options[i].value === tx.category_name) {
                            catSelect.selectedIndex = i;
                            break;
                        }
                    }
                }

                editFailedModal.style.display = "flex";
            }

            function closeEditFailedModal() {
                editFailedModal.style.display = "none";
            }

            async function saveAndRetryFailedTx() {
                const id = document.getElementById('editFailedId').value;
                const merchant = document.getElementById('editFailedMerchant').value.trim();
                const amountVal = document.getElementById('editFailedAmount').value;
                const amount = amountVal ? parseFloat(amountVal) : 0.0;
                const currency = document.getElementById('editFailedCurrency').value;
                const date = document.getElementById('editFailedDate').value;
                const category_name = document.getElementById('editFailedCategory').value;
                const is_credit = document.getElementById('editFailedIsCredit').checked;
                const is_cash = document.getElementById('editFailedIsCash').checked;

                if (!merchant) {
                    showToast("Merchant name is required", "error");
                    return;
                }

                try {
                    const updateRes = await fetch(`/api/failed-transactions/${id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            merchant,
                            amount,
                            currency,
                            date,
                            category_name,
                            is_credit,
                            is_cash
                        })
                    });

                    if (!updateRes.ok) {
                        const err = await updateRes.json();
                        throw new Error(err.detail || "Failed to update transaction");
                    }

                    closeEditFailedModal();
                    showToast("Changes saved. Retrying transaction...", "success");
                    await retrySingleFailedTx(id, null);
                } catch (e) {
                    showToast("Error: " + e.message, "error");
                }
            }

            // --- Starred Merchants Support ---
            let allMerchantsCache = [];

            function updateStarIcon(isStarred) {
                const btn = document.getElementById('starMerchantBtn');
                if (!btn) return;
                if (isStarred) {
                    btn.textContent = '⭐';
                    btn.title = 'Starred Merchant (Click to unstar)';
                } else {
                    btn.textContent = '☆';
                    btn.title = 'Star this merchant';
                }
            }

            async function toggleProcessedMerchantStar() {
                if (!window.currentTransactionData || !window.currentTransactionData.merchant) return;
                const merchantName = window.currentTransactionData.merchant;
                const newStarState = !window.currentMerchantStarred;
                
                try {
                    const res = await fetch(`/api/merchants/${encodeURIComponent(merchantName)}/star`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ is_starred: newStarState })
                    });
                    if (res.ok) {
                        const data = await res.json();
                        window.currentMerchantStarred = !!data.is_starred;
                        updateStarIcon(window.currentMerchantStarred);
                        showToast(window.currentMerchantStarred ? `"${merchantName}" starred! ⭐` : `"${merchantName}" unstarred`, 'success');
                    } else {
                        showToast('Failed to update star', 'error');
                    }
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }

            async function openManageStarredModal(e) {
                if (e) e.preventDefault();
                const deepLinkDropdown = document.getElementById('deepLinkDropdown');
                if (deepLinkDropdown) deepLinkDropdown.classList.remove('show');
                
                document.getElementById('manageStarredModal').style.display = 'flex';
                document.getElementById('newMerchantInput').value = '';
                document.getElementById('searchMerchantsInput').value = '';
                await fetchManageMerchants();
            }

            function closeManageStarredModal() {
                document.getElementById('manageStarredModal').style.display = 'none';
            }

            async function fetchManageMerchants() {
                const loadingEl = document.getElementById('merchantsLoading');
                const noDataEl = document.getElementById('merchantsNoData');
                const bodyEl = document.getElementById('merchantsListBody');
                
                if (loadingEl) loadingEl.style.display = 'block';
                if (noDataEl) noDataEl.style.display = 'none';
                if (bodyEl) bodyEl.innerHTML = '';
                
                try {
                    const res = await fetch('/api/merchants');
                    if (res.ok) {
                        const data = await res.json();
                        allMerchantsCache = data.merchants || [];
                        renderMerchantsList(allMerchantsCache);
                    } else {
                        showToast('Failed to load merchants', 'error');
                    }
                } catch (e) {
                    showToast('Network error: ' + e.message, 'error');
                } finally {
                    if (loadingEl) loadingEl.style.display = 'none';
                }
            }

            function renderMerchantsList(merchants) {
                const bodyEl = document.getElementById('merchantsListBody');
                const noDataEl = document.getElementById('merchantsNoData');
                if (!bodyEl) return;
                
                bodyEl.innerHTML = '';
                if (!merchants || merchants.length === 0) {
                    if (noDataEl) noDataEl.style.display = 'block';
                    return;
                }
                if (noDataEl) noDataEl.style.display = 'none';
                
                merchants.forEach(m => {
                    const item = document.createElement('div');
                    item.className = 'merchant-list-item';
                    item.style.cssText = 'display:flex; align-items:center; justify-content:space-between; padding:9px 12px; margin-bottom:6px; background:linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%); border:1px solid rgba(221, 214, 254, 0.3); border-radius:8px; transition: background 0.15s ease;';
                    item.onmouseover = () => { item.style.background = 'linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)'; };
                    item.onmouseout = () => { item.style.background = 'linear-gradient(135deg, #7c3aed 0%, #6d28d9 100%)'; };
                    
                    const isStarred = !!m.is_starred;
                    const starIcon = isStarred ? '⭐' : '☆';
                    const starTitle = isStarred ? 'Starred (click to unstar)' : 'Unstarred (click to star)';
                    const starColor = isStarred ? '#fbbf24' : '#e0e7ff';
                    
                    item.innerHTML = `
                        <div style="display:flex; align-items:center; gap:8px; flex:1; min-width:0;">
                            <button onclick="toggleMerchantItemStar('${encodeURIComponent(m.name)}', ${isStarred})" title="${starTitle}" style="background:none; border:none; font-size:1.25rem; cursor:pointer; padding:0; line-height:1; color:${starColor};">
                                ${starIcon}
                            </button>
                            <span style="font-weight:600; color:#ffffff; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${m.name}">${m.name}</span>
                        </div>
                        <button onclick="deleteMerchantItem('${encodeURIComponent(m.name)}')" title="Delete merchant" style="background:rgba(254, 202, 202, 0.15); border:1px solid rgba(252, 165, 165, 0.45); border-radius:6px; padding:4px 6px; cursor:pointer; display:flex; align-items:center; justify-content:center; transition: all 0.15s ease;" onmouseover="this.style.background='rgba(252, 165, 165, 0.3)'; this.style.borderColor='#fca5a5';" onmouseout="this.style.background='rgba(254, 202, 202, 0.15)'; this.style.borderColor='rgba(252, 165, 165, 0.45)';">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fca5a5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="display:block;"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
                        </button>
                    `;
                    bodyEl.appendChild(item);
                });
            }

            function filterMerchantsList() {
                const q = (document.getElementById('searchMerchantsInput').value || '').trim().toLowerCase();
                if (!q) {
                    renderMerchantsList(allMerchantsCache);
                    return;
                }
                const filtered = allMerchantsCache.filter(m => m.name.toLowerCase().includes(q));
                renderMerchantsList(filtered);
            }

            async function addNewStarredMerchant() {
                const input = document.getElementById('newMerchantInput');
                const name = (input.value || '').trim();
                if (!name) {
                    showToast('Please enter a merchant name', 'error');
                    return;
                }
                
                try {
                    const res = await fetch('/api/merchants', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ name: name, is_starred: true })
                    });
                    if (res.ok) {
                        input.value = '';
                        showToast(`Added "${name}" to Starred Merchants! ⭐`, 'success');
                        await fetchManageMerchants();
                    } else {
                        const err = await res.json();
                        showToast('Error: ' + (err.detail || 'Could not add merchant'), 'error');
                    }
                } catch (e) {
                    showToast('Network error: ' + e.message, 'error');
                }
            }

            async function toggleMerchantItemStar(encodedName, currentStarred) {
                const merchantName = decodeURIComponent(encodedName);
                const newStarState = !currentStarred;
                try {
                    const res = await fetch(`/api/merchants/${encodedName}/star`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ is_starred: newStarState })
                    });
                    if (res.ok) {
                        showToast(newStarState ? `"${merchantName}" starred! ⭐` : `"${merchantName}" unstarred`, 'success');
                        await fetchManageMerchants();
                    } else {
                        showToast('Failed to update star', 'error');
                    }
                } catch (e) {
                    showToast('Network error: ' + e.message, 'error');
                }
            }

            async function deleteMerchantItem(encodedName) {
                const merchantName = decodeURIComponent(encodedName);
                showConfirmToast(`Delete "${merchantName}"?`, async () => {
                    try {
                        const res = await fetch(`/api/merchants/${encodedName}`, {
                            method: 'DELETE'
                        });
                        if (res.ok) {
                            showToast(`"${merchantName}" deleted`, 'success');
                            await fetchManageMerchants();
                        } else {
                            showToast('Failed to delete merchant', 'error');
                        }
                    } catch (e) {
                        showToast('Network error: ' + e.message, 'error');
                    }
                });
            }

            // Start polling and load initial badge count
            updateFailedBadgeCount();
            setTimeout(checkStatus, 100);
        </script>

    </body>
</html>
"""

@app.post("/manual")
async def handle_manual_entry(
    background_tasks: BackgroundTasks,
    amount: float = Form(...),
    currency: str = Form(...),
    date: str = Form(...),
    merchant: str = Form(...),
    is_cash: Optional[bool] = Form(False),
    is_credit: Optional[bool] = Form(False),
    notes: Optional[str] = Form(None)
):
    """
    Handle Manual Entry POST request.
    """
    try:
        job_id = str(uuid.uuid4())
        mm_account = os.environ.get("MM_ACCOUNT", "Default Account")
        
        manual_data = {
            "amount": amount,
            "currency": currency,
            "date": date,
            "merchant": merchant,
            "is_cash": is_cash,
            "is_credit": is_credit,
            "notes": notes
        }
        
        # Start background task
        background_tasks.add_task(process_background_job, job_id, None, None, manual_data)
        
        # Return Loading HTML
        return HTMLResponse(content=LOADING_HTML.replace("__JOB_ID__", job_id).replace("__MM_ACCOUNT__", mm_account))

    except Exception as e:
        print(f"Error starting job: {e}")
        return HTMLResponse(content="Error starting job", status_code=500)

@app.post("/share")
async def handle_share(
    background_tasks: BackgroundTasks,
    currency: str = Form(None),
    file: UploadFile = File(...)
):
    """
    Handle Share Target POST request. 
    Starts processing in background and returns a loading page that polls for status.
    """
    try:
        # Read file immediately before response closes
        content = await file.read()
        job_id = str(uuid.uuid4())
        mm_account = os.environ.get("MM_ACCOUNT", "Default Account")
        
        # Start background task
        background_tasks.add_task(process_background_job, job_id, content, currency)
        
        # Return Loading HTML
        return HTMLResponse(content=LOADING_HTML.replace("__JOB_ID__", job_id).replace("__MM_ACCOUNT__", mm_account))

    except Exception as e:
        print(f"Error starting job: {e}")
        return HTMLResponse(content="Error starting job", status_code=500)

class MerchantCreate(BaseModel):
    name: str
    is_starred: bool = True

class MerchantStarRequest(BaseModel):
    is_starred: Optional[bool] = True

@app.get("/api/merchants")
async def get_merchants(starred: Optional[bool] = None, q: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """
    List merchants, optionally filtered by starred status and query string.
    """
    try:
        stmt = select(Merchant)
        if starred is not None:
            stmt = stmt.where(Merchant.is_starred == starred)
        if q:
            stmt = stmt.where(Merchant.name.ilike(f"%{q.strip()}%"))
        
        stmt = stmt.order_by(Merchant.is_starred.desc(), func.lower(Merchant.name).asc())
        result = await db.execute(stmt)
        merchants = result.scalars().all()
        return {
            "merchants": [
                {
                    "id": m.id,
                    "name": m.name,
                    "is_starred": m.is_starred,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                }
                for m in merchants
            ]
        }
    except Exception as e:
        print(f"Error fetching merchants: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/merchants/starred")
async def get_starred_merchants(db: AsyncSession = Depends(get_db)):
    """
    Get all starred merchants, along with their mapped category if configured.
    """
    try:
        stmt = select(Merchant).where(Merchant.is_starred == True).order_by(func.lower(Merchant.name).asc())
        result = await db.execute(stmt)
        starred = result.scalars().all()

        # Fetch mappings to auto-populate category if present
        mappings_result = await db.execute(select(MerchantMapping))
        mappings = mappings_result.scalars().all()
        mapping_map = {}
        for m in mappings:
            if m.receipt_merchant_name:
                mapping_map[m.receipt_merchant_name.strip().lower()] = m.category_name
            if m.monarch_merchant_name:
                mapping_map[m.monarch_merchant_name.strip().lower()] = m.category_name

        data = []
        for s in starred:
            name_clean = s.name.strip()
            cat = mapping_map.get(name_clean.lower())
            data.append({
                "id": s.id,
                "name": name_clean,
                "is_starred": True,
                "mapped_category": cat
            })
        return {"merchants": data}
    except Exception as e:
        print(f"Error fetching starred merchants: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/merchants/{name}/status")
async def get_merchant_status(name: str, db: AsyncSession = Depends(get_db)):
    try:
        clean_name = name.strip()
        stmt = select(Merchant).where(func.lower(Merchant.name) == clean_name.lower())
        result = await db.execute(stmt)
        merchant = result.scalar_one_or_none()
        return {
            "name": clean_name,
            "exists": merchant is not None,
            "is_starred": merchant.is_starred if merchant else False
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/merchants")
async def create_or_star_merchant(payload: MerchantCreate, db: AsyncSession = Depends(get_db)):
    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Merchant name cannot be empty")
    try:
        stmt = select(Merchant).where(func.lower(Merchant.name) == clean_name.lower())
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.is_starred = payload.is_starred
            await db.commit()
            await db.refresh(existing)
            return {"id": existing.id, "name": existing.name, "is_starred": existing.is_starred, "created": False}
        
        new_m = Merchant(name=clean_name, is_starred=payload.is_starred)
        db.add(new_m)
        await db.commit()
        await db.refresh(new_m)
        return {"id": new_m.id, "name": new_m.name, "is_starred": new_m.is_starred, "created": True}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/merchants/{name}/star")
async def toggle_merchant_star(name: str, payload: Optional[MerchantStarRequest] = None, db: AsyncSession = Depends(get_db)):
    clean_name = name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Merchant name cannot be empty")
    try:
        stmt = select(Merchant).where(func.lower(Merchant.name) == clean_name.lower())
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            if payload and payload.is_starred is not None:
                existing.is_starred = payload.is_starred
            else:
                existing.is_starred = not existing.is_starred
            await db.commit()
            await db.refresh(existing)
            return {"id": existing.id, "name": existing.name, "is_starred": existing.is_starred}
        else:
            is_starred = payload.is_starred if (payload and payload.is_starred is not None) else True
            new_m = Merchant(name=clean_name, is_starred=is_starred)
            db.add(new_m)
            await db.commit()
            await db.refresh(new_m)
            return {"id": new_m.id, "name": new_m.name, "is_starred": new_m.is_starred}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/merchants/{name}")
async def delete_merchant(name: str, db: AsyncSession = Depends(get_db)):
    clean_name = name.strip()
    try:
        stmt = select(Merchant).where(func.lower(Merchant.name) == clean_name.lower())
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if not existing:
            if clean_name.isdigit():
                stmt_id = select(Merchant).where(Merchant.id == int(clean_name))
                res_id = await db.execute(stmt_id)
                existing = res_id.scalar_one_or_none()
        
        if existing:
            await db.delete(existing)
            await db.commit()
            return {"success": True, "message": f"Merchant '{existing.name}' deleted"}
        return {"success": True, "message": "Merchant not found or already deleted"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

class MappingRequest(BaseModel):
    receipt_merchant_name: str
    monarch_merchant_name: str
    category_name: str
    monarch_tx_id: Optional[str] = None

@app.get("/api/merchant-names")
async def get_merchant_names(db: AsyncSession = Depends(get_db)):
    """
    Return a sorted, distinct list of Monarch merchant names from the merchant_mappings table.
    Used by the frontend to build the HISTORICAL_MERCHANT_NAMES localStorage cache, and by
    the backend to hint the Gemini AI during OCR inference.
    """
    try:
        result = await db.execute(
            select(MerchantMapping.monarch_merchant_name).distinct()
        )
        names = sorted([row[0] for row in result.fetchall() if row[0]])
        return {"merchant_names": names}
    except Exception as e:
        print(f"Error fetching merchant names: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/categories")
async def get_categories(db: AsyncSession = Depends(get_db)):
    """
    Fetch categories from Monarch (using cached session) and local DB.
    """
    try:
        # 1. Try to get from local DB first (faster)
        local_cats_result = await db.execute(select(Category).where(Category.is_hidden == False))
        local_cats = local_cats_result.scalars().all()
        
        if local_cats:
             # Sort by name
             local_cats.sort(key=lambda x: x.category_name.lower())
             return {"categories": [{"name": c.category_name, "emoji": c.category_emoji} for c in local_cats]}

        # 2. Fallback to Monarch API (if local empty)
        creds = await get_latest_credentials(db)
        if not creds:
             raise HTTPException(status_code=400, detail="No credentials found")
             
        mm = await get_monarch_client(db, creds.id)
        cat_data = await mm.get_transaction_categories()
        
        # Transform for frontend
        categories = []
        for c in cat_data.get('categories', []):
             categories.append({"name": c['name'], "emoji": ""}) # API doesn't give emoji easily here?
        
        # Sort by name
        categories.sort(key=lambda x: x['name'].lower())
             
        return {"categories": categories}
    except Exception as e:
        print(f"Error fetching categories: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/mapping")
async def save_mapping(request: Request, mapping: MappingRequest, db: AsyncSession = Depends(get_db)):
    """
    Create or update a merchant mapping.
    """
    if not request.state.is_authenticated:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        # Enforce lowercase for the key
        lower_receipt_name = mapping.receipt_merchant_name.lower()
        
        # Check if exists
        stmt = select(MerchantMapping).where(MerchantMapping.receipt_merchant_name == lower_receipt_name)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.monarch_merchant_name = mapping.monarch_merchant_name
            existing.category_name = mapping.category_name
        else:
            new_mapping = MerchantMapping(
                receipt_merchant_name=lower_receipt_name,
                monarch_merchant_name=mapping.monarch_merchant_name,
                category_name=mapping.category_name
            )
            db.add(new_mapping)
            
        await db.commit()

        if mapping.monarch_tx_id:
            # Try to get the monarch_category_id from local DB
            cat_stmt = select(Category).where(Category.category_name == mapping.category_name)
            cat_result = await db.execute(cat_stmt)
            category = cat_result.scalar_one_or_none()
            
            category_id = category.monarch_category_id if category else None

            creds = await get_latest_credentials(db)
            if creds:
                mm = await get_monarch_client(db, creds.id)
                await mm.update_transaction(
                    transaction_id=mapping.monarch_tx_id,
                    merchant_name=mapping.monarch_merchant_name,
                    category_id=category_id
                )

        return {"status": "success"}
    except Exception as e:
        await db.rollback()
        print(f"Error saving mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))

class DeleteMappingRequest(BaseModel):
    receipt_merchant_name: str

@app.delete("/api/mapping")
async def delete_mapping(req: DeleteMappingRequest, db: AsyncSession = Depends(get_db)):
    """
    Delete a merchant mapping.
    """
    try:
        # Enforce lowercase for the key
        lower_receipt_name = req.receipt_merchant_name.lower()
        
        stmt = select(MerchantMapping).where(MerchantMapping.receipt_merchant_name == lower_receipt_name)
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            await db.delete(existing)
            await db.commit()
            return {"status": "success", "message": "Mapping deleted"}
        else:
             raise HTTPException(status_code=404, detail="Mapping not found")
             
    except Exception as e:
        await db.rollback()
        print(f"Error deleting mapping: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

async def _update_history_log(db: AsyncSession, monarch_tx_id: str, new_date: str, new_amount: Optional[float] = None):
    """Helper to update the date and optionally the amount in the history log."""
    try:
        from .models import Log
        log_stmt = select(Log).where(Log.monarch_tx_id == monarch_tx_id)
        log_res = await db.execute(log_stmt)
        log_entry = log_res.scalar_one_or_none()
        if log_entry:
            log_entry.date = new_date
            if new_amount is not None:
                log_entry.amount = new_amount
                print(f"Updated history log entry for transaction {monarch_tx_id}: new date={new_date}, amount={new_amount}")
            else:
                print(f"Updated history log entry for transaction {monarch_tx_id}: new date={new_date} (USD)")
            await db.commit()
    except Exception as e:
        print(f"Failed to update history log entry: {e}")

class UpdateDateRequest(BaseModel):
    monarch_tx_id: str
    new_date: str            # YYYY-MM-DD
    original_currency: str   # e.g. "EUR" — the foreign currency before conversion
    original_amount: float   # The original foreign-currency amount
    is_credit: Optional[bool] = False

@app.post("/api/transaction/update-date")
async def update_transaction_date(req: UpdateDateRequest, db: AsyncSession = Depends(get_db)):
    """
    Update a transaction's date in Monarch, recalculating the USD amount and notes
    using the exchange rate for the new date.

    If original_currency is USD, only the date is updated (no conversion needed).
    """
    try:
        from .services.currency import get_exchange_rate

        creds = await get_latest_credentials(db)
        if not creds:
            raise HTTPException(status_code=400, detail="No Monarch credentials configured")

        mm = await get_monarch_client(db, creds.id)
        from .services.monarch import update_transaction_fields

        new_date = req.new_date
        original_currency = req.original_currency.upper().strip()
        original_amount = req.original_amount
        is_credit = req.is_credit or False

        if original_currency in ("USD", ""):
            # No conversion needed — just update the date
            await update_transaction_fields(
                mm,
                transaction_id=req.monarch_tx_id,
                date=new_date,
            )

            # Update logs table record if it exists
            await _update_history_log(db, req.monarch_tx_id, new_date)

            print(f"Updated transaction {req.monarch_tx_id}: new date={new_date} (USD, no conversion)")
            return {
                "status": "success",
                "new_date": new_date,
                "new_amount": original_amount,
                "exchange_rate": None,
                "original_currency": original_currency,
            }
        else:
            # Re-fetch exchange rate for the new date
            rate = await get_exchange_rate(original_currency, "USD", new_date)
            new_usd = round(original_amount * rate, 2)
            # Sign: credits are positive, debits are negative
            signed_amount = abs(new_usd) if is_credit else -abs(new_usd)

            # Build notes in the same format as push_transaction
            notes = (
                f"Original Price: {original_currency} {original_amount:.2f}\n"
                f"Exchange Rate: {rate} USD/{original_currency}"
            )

            update_result = await update_transaction_fields(
                mm,
                transaction_id=req.monarch_tx_id,
                date=new_date,
                amount=signed_amount,
                notes=notes,
            )

            # Update logs table record if it exists
            await _update_history_log(db, req.monarch_tx_id, new_date, signed_amount)

            print(
                f"Updated transaction {req.monarch_tx_id}: new date={new_date}, "
                f"{original_currency} {original_amount:.2f} -> USD {new_usd:.2f} @ {rate} "
                f"(amount_updated={update_result['amount_updated']})"
            )
            return {
                "status": "success",
                "new_date": new_date,
                "new_amount": new_usd,
                "exchange_rate": rate,
                "original_currency": original_currency,
                "amount_updated": update_result["amount_updated"],
            }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

class UpdateCategoryRequest(BaseModel):
    monarch_tx_id: str
    category_name: str

@app.post("/api/transaction/update-category")
async def update_transaction_category(req: UpdateCategoryRequest, db: AsyncSession = Depends(get_db)):
    """
    Update a transaction's category in Monarch Money and locally.
    Does not edit mapping rules.
    """
    try:
        creds = await get_latest_credentials(db)
        if not creds:
            raise HTTPException(status_code=400, detail="No Monarch credentials configured")

        mm = await get_monarch_client(db, creds.id)

        # Try to get the monarch_category_id from local DB
        cat_stmt = select(Category).where(Category.category_name == req.category_name)
        cat_result = await db.execute(cat_stmt)
        category = cat_result.scalar_one_or_none()
        
        category_id = category.monarch_category_id if category else None

        # Fallback: if category_id not in local DB, fetch from Monarch API directly
        if not category_id:
            try:
                cat_data = await mm.get_transaction_categories()
                for c in cat_data.get("categories", []):
                    if c.get("name", "").lower() == req.category_name.lower():
                        category_id = c.get("id")
                        break
            except Exception as e:
                print(f"Failed to fetch categories from Monarch API: {e}")

        if not category_id:
            raise HTTPException(status_code=400, detail=f"Category '{req.category_name}' not configured or missing monarch ID")

        # Update in Monarch Money
        await mm.update_transaction(
            transaction_id=req.monarch_tx_id,
            category_id=category_id
        )

        # Update local Transaction table's parsed_data JSON if it exists
        tx_stmt = select(Transaction).where(Transaction.parsed_data["monarch_tx_id"].as_string() == req.monarch_tx_id)
        tx_result = await db.execute(tx_stmt)
        tx = tx_result.scalar_one_or_none()
        if tx:
            parsed = dict(tx.parsed_data) if tx.parsed_data else {}
            parsed["category_name"] = req.category_name
            if category.category_emoji:
                parsed["category_emoji"] = category.category_emoji
            else:
                parsed.pop("category_emoji", None)
            tx.parsed_data = parsed
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(tx, "parsed_data")
            await db.commit()

        return {"status": "success", "category_emoji": category.category_emoji}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def get_logs(db: AsyncSession = Depends(get_db)):
    """
    Get the last 20 processed transactions from the log.
    """
    try:
        from .models import Log
        stmt = select(Log).order_by(Log.created_at.desc()).limit(20)
        result = await db.execute(stmt)
        logs = result.scalars().all()
        
        return [
            {
                "id": log.id,
                "merchant": log.merchant,
                "amount": log.amount,
                "currency": log.currency,
                "date": log.date,
                "original_amount": log.original_amount,
                "original_currency": log.original_currency,
                "is_cash": log.is_cash,
                "monarch_tx_id": log.monarch_tx_id,
                "created_at": log.created_at.isoformat() if log.created_at else None
            }
            for log in logs
        ]
    except Exception as e:
        print(f"Error fetching logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/logs/{log_id}")
async def delete_log_entry(log_id: int, db: AsyncSession = Depends(get_db)):
    """
    Delete a history log entry from the local logs database table only.
    Does NOT delete from Monarch Money or local Transaction tables.
    """
    try:
        from .models import Log
        log_stmt = select(Log).where(Log.id == log_id)
        log_result = await db.execute(log_stmt)
        log_entry = log_result.scalar_one_or_none()
        
        if not log_entry:
            raise HTTPException(status_code=404, detail="Log entry not found")

        # Delete Log entry only
        await db.delete(log_entry)
        await db.commit()
        
        return {"status": "success", "message": "Log entry deleted"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# ⚠️ Failed Transactions Routes
# =============================================================================

class FailedTransactionUpdateRequest(BaseModel):
    merchant: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    date: Optional[str] = None
    category_name: Optional[str] = None
    is_cash: Optional[bool] = None
    is_credit: Optional[bool] = None
    notes: Optional[str] = None

@app.get("/api/failed-transactions")
async def get_failed_transactions(db: AsyncSession = Depends(get_db)):
    """
    Get all failed transactions ordered by creation date descending.
    Excludes large raw_content binary bytes for performance.
    """
    try:
        stmt = select(FailedTransaction).order_by(FailedTransaction.created_at.desc())
        result = await db.execute(stmt)
        failed_list = result.scalars().all()
        
        items = []
        for tx in failed_list:
            display_data = tx.parsed_data or tx.manual_data or {}
            items.append({
                "id": tx.id,
                "source_type": tx.source_type,
                "error_message": tx.error_message,
                "retry_count": tx.retry_count or 0,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
                "updated_at": tx.updated_at.isoformat() if tx.updated_at else None,
                "has_image": tx.raw_content is not None,
                "user_currency": tx.user_currency,
                "merchant": display_data.get("merchant") or "Receipt (OCR Pending)",
                "amount": display_data.get("amount"),
                "currency": display_data.get("currency") or tx.user_currency or "EUR",
                "date": display_data.get("date") or "",
                "is_cash": bool(display_data.get("is_cash", False)),
                "is_credit": bool(display_data.get("is_credit", False)),
                "notes": display_data.get("notes") or "",
                "category_name": display_data.get("category_name") or "",
                "category_emoji": display_data.get("category_emoji") or "",
                "original_amount": display_data.get("original_amount"),
                "original_currency": display_data.get("original_currency"),
                "parsed_data": tx.parsed_data,
                "manual_data": tx.manual_data
            })
        return items
    except Exception as e:
        print(f"Error fetching failed transactions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/failed-transactions/count")
async def get_failed_transactions_count(db: AsyncSession = Depends(get_db)):
    """
    Get the total count of failed transactions for the UI badge.
    """
    try:
        from sqlalchemy import func as sql_func
        stmt = select(sql_func.count()).select_from(FailedTransaction)
        result = await db.execute(stmt)
        count = result.scalar_one() or 0
        return {"count": count}
    except Exception as e:
        print(f"Error counting failed transactions: {e}")
        return {"count": 0}

@app.get("/api/failed-transactions/{failed_id}/image")
async def get_failed_transaction_image(failed_id: int, db: AsyncSession = Depends(get_db)):
    """
    Serve the stored receipt image if available.
    """
    tx = await db.get(FailedTransaction, failed_id)
    if not tx or not tx.raw_content:
        raise HTTPException(status_code=404, detail="Image not found")
    
    media_type = "image/jpeg"
    if tx.raw_content.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif tx.raw_content.startswith(b"GIF87a") or tx.raw_content.startswith(b"GIF89a"):
        media_type = "image/gif"
    elif tx.raw_content.startswith(b"RIFF") and b"WEBP" in tx.raw_content[:12]:
        media_type = "image/webp"
        
    return Response(content=tx.raw_content, media_type=media_type)

@app.put("/api/failed-transactions/{failed_id}")
async def update_failed_transaction(
    failed_id: int, 
    update_req: FailedTransactionUpdateRequest, 
    db: AsyncSession = Depends(get_db)
):
    """
    Update editable fields of a failed transaction before retry.
    """
    tx = await db.get(FailedTransaction, failed_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Failed transaction not found")
        
    data = dict(tx.parsed_data or tx.manual_data or {})
    if update_req.merchant is not None: data["merchant"] = update_req.merchant
    if update_req.amount is not None: data["amount"] = update_req.amount
    if update_req.currency is not None: data["currency"] = update_req.currency
    if update_req.date is not None: data["date"] = update_req.date
    if update_req.category_name is not None: data["category_name"] = update_req.category_name
    if update_req.is_cash is not None: data["is_cash"] = update_req.is_cash
    if update_req.is_credit is not None: data["is_credit"] = update_req.is_credit
    if update_req.notes is not None: data["notes"] = update_req.notes
    
    from sqlalchemy.orm.attributes import flag_modified
    if tx.source_type == "manual":
        tx.manual_data = data
        flag_modified(tx, "manual_data")
    else:
        tx.parsed_data = data
        flag_modified(tx, "parsed_data")
        
    await db.commit()
    return {"status": "ok", "message": "Transaction updated successfully"}

@app.post("/api/failed-transactions/{failed_id}/retry")
async def retry_failed_transaction(
    failed_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Retry a specific failed transaction.
    On success: pushes to Monarch, adds to Transaction & Log tables, and deletes from FailedTransaction table.
    """
    tx = await db.get(FailedTransaction, failed_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Failed transaction not found")
        
    try:
        from .services.orchestrator import process_transaction, process_manual_transaction, process_parsed_transaction
        
        result = None
        if tx.parsed_data:
            # We already have parsed/edited fields, retry from parsed data
            img_hash = tx.image_hash or f"retry_{tx.id}_{uuid.uuid4().hex[:6]}"
            result = await process_parsed_transaction(
                data=dict(tx.parsed_data),
                image_hash=img_hash,
                db=db,
                user_currency_override=tx.user_currency,
                force_override=True
            )
        elif tx.source_type == "manual" and tx.manual_data:
            result = await process_manual_transaction(
                manual_data=dict(tx.manual_data),
                db=db,
                force_override=True
            )
        elif tx.raw_content:
            # Re-run full OCR extraction and processing
            result = await process_transaction(
                content=tx.raw_content,
                db=db,
                user_currency=tx.user_currency,
                force_override=True
            )
        else:
            raise ValueError("No transaction data or image content found to retry.")
            
        # If successful, delete from failed_transactions
        await db.delete(tx)
        await db.commit()
        return {"status": "success", "result": result}
        
    except Exception as e:
        tx.retry_count = (tx.retry_count or 0) + 1
        tx.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Retry failed: {str(e)}")

@app.post("/api/failed-transactions/retry-all")
async def retry_all_failed_transactions(db: AsyncSession = Depends(get_db)):
    """
    Retry all failed transactions sequentially.
    Returns summary of successes and failures.
    """
    stmt = select(FailedTransaction).order_by(FailedTransaction.created_at.asc())
    result = await db.execute(stmt)
    failed_list = result.scalars().all()
    
    if not failed_list:
        return {"status": "ok", "total": 0, "succeeded": 0, "failed": 0, "results": []}
        
    from .services.orchestrator import process_transaction, process_manual_transaction, process_parsed_transaction
    
    succeeded = 0
    failed = 0
    results = []
    
    for tx in failed_list:
        tx_id = tx.id
        merchant = (tx.parsed_data or tx.manual_data or {}).get("merchant", f"Transaction #{tx_id}")
        try:
            if tx.parsed_data:
                img_hash = tx.image_hash or f"retry_{tx.id}_{uuid.uuid4().hex[:6]}"
                res = await process_parsed_transaction(
                    data=dict(tx.parsed_data),
                    image_hash=img_hash,
                    db=db,
                    user_currency_override=tx.user_currency,
                    force_override=True
                )
            elif tx.source_type == "manual" and tx.manual_data:
                res = await process_manual_transaction(
                    manual_data=dict(tx.manual_data),
                    db=db,
                    force_override=True
                )
            elif tx.raw_content:
                res = await process_transaction(
                    content=tx.raw_content,
                    db=db,
                    user_currency=tx.user_currency,
                    force_override=True
                )
            else:
                raise ValueError("No data or content available to retry")
                
            await db.delete(tx)
            await db.commit()
            succeeded += 1
            results.append({"id": tx_id, "merchant": merchant, "status": "success"})
        except Exception as e:
            tx.retry_count = (tx.retry_count or 0) + 1
            tx.error_message = str(e)
            await db.commit()
            failed += 1
            results.append({"id": tx_id, "merchant": merchant, "status": "failed", "error": str(e)})
            
    return {
        "status": "ok",
        "total": len(failed_list),
        "succeeded": succeeded,
        "failed": failed,
        "results": results
    }

@app.delete("/api/failed-transactions/{failed_id}")
async def delete_failed_transaction(failed_id: int, db: AsyncSession = Depends(get_db)):
    tx = await db.get(FailedTransaction, failed_id)
    if not tx:
        raise HTTPException(status_code=404, detail="Failed transaction not found")
    await db.delete(tx)
    await db.commit()
    return {"status": "ok", "message": "Failed transaction deleted"}

@app.delete("/api/failed-transactions")
async def clear_all_failed_transactions(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(FailedTransaction))
    await db.commit()
    return {"status": "ok", "message": "All failed transactions cleared"}


# =============================================================================
# 🔥 IGNITE — FIRE Simulation Routes
# =============================================================================

@app.get("/fire", response_class=HTMLResponse)
async def fire_page(request: Request):
    """Serve the Ignite FIRE dashboard page."""
    import pathlib
    fire_html = pathlib.Path("bridge_app/static/fire.html").read_text()
    
    # Inject authentication state
    is_auth_str = "true" if request.state.is_authenticated else "false"
    fire_html = fire_html.replace(
        "/* INIT_AUTH_STATE */", 
        f"</style><script>window.IS_AUTHENTICATED = {is_auth_str};</script><style>"
    )
    
    return HTMLResponse(content=fire_html)


class FireSettingsUpdate(BaseModel):
    current_age: Optional[int] = None
    retirement_age: Optional[int] = None
    annual_contribution: Optional[int] = None
    annual_retirement_spending: Optional[int] = None
    risk_tolerance: Optional[str] = None
    inflation_rate: Optional[float] = None
    final_age: Optional[int] = None
    social_security_enabled: Optional[bool] = None
    social_security_pia: Optional[int] = None
    social_security_fra: Optional[int] = None
    social_security_birth_month: Optional[int] = None
    social_security_birth_year: Optional[int] = None
    social_security_withdrawal_month: Optional[int] = None
    social_security_withdrawal_year: Optional[int] = None


@app.get("/api/fire/settings")
async def get_fire_settings(request: Request, db: AsyncSession = Depends(get_db)):
    """Get current FIRE simulation settings."""
    if not request.state.is_authenticated:
        # Return default values for unauthenticated users instead of reading DB
        return DEMO_DEFAULTS.copy()

    result = await db.execute(select(FireSettings).where(FireSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings:
        # Create default settings
        settings = FireSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return {
        "current_age": settings.current_age,
        "retirement_age": settings.retirement_age,
        "annual_contribution": settings.annual_contribution,
        "annual_retirement_spending": settings.annual_retirement_spending,
        "risk_tolerance": settings.risk_tolerance,
        "inflation_rate": settings.inflation_rate,
        "final_age": settings.final_age,
        "social_security_enabled": settings.social_security_enabled,
        "social_security_pia": settings.social_security_pia,
        "social_security_fra": settings.social_security_fra,
        "social_security_birth_month": settings.social_security_birth_month,
        "social_security_birth_year": settings.social_security_birth_year,
        "social_security_withdrawal_month": settings.social_security_withdrawal_month,
        "social_security_withdrawal_year": settings.social_security_withdrawal_year,
    }


@app.put("/api/fire/settings")
async def update_fire_settings(
    request: Request,
    updates: FireSettingsUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update FIRE simulation settings."""
    if not request.state.is_authenticated:
        # Pretend it updated for unauthorized users
        return {
            "status": "success",
            **updates.model_dump(exclude_none=True)
        }

    result = await db.execute(select(FireSettings).where(FireSettings.id == 1))
    settings = result.scalar_one_or_none()

    if not settings:
        settings = FireSettings(id=1)
        db.add(settings)

    # Apply updates
    update_data = updates.model_dump(exclude_none=True)
    for key, value in update_data.items():
        setattr(settings, key, value)

    await db.commit()
    await db.refresh(settings)

    return {
        "status": "success",
        "current_age": settings.current_age,
        "retirement_age": settings.retirement_age,
        "annual_contribution": settings.annual_contribution,
        "annual_retirement_spending": settings.annual_retirement_spending,
        "risk_tolerance": settings.risk_tolerance,
        "inflation_rate": settings.inflation_rate,
        "final_age": settings.final_age,
        "social_security_enabled": settings.social_security_enabled,
        "social_security_pia": settings.social_security_pia,
        "social_security_fra": settings.social_security_fra,
        "social_security_birth_month": settings.social_security_birth_month,
        "social_security_birth_year": settings.social_security_birth_year,
        "social_security_withdrawal_month": settings.social_security_withdrawal_month,
        "social_security_withdrawal_year": settings.social_security_withdrawal_year,
    }


class SimulateRequest(BaseModel):
    settings: Optional[FireSettingsUpdate] = None
    current_portfolio: Optional[float] = None
    is_demo: bool = False


def _build_simulation_response(result, current_portfolio: float, account_breakdown: list, monthly_spend_avg: float, settings_obj) -> dict:
    return {
        "years": result.years,
        "percentile_5": result.percentile_5,
        "percentile_25": result.percentile_25,
        "percentile_50": result.percentile_50,
        "percentile_75": result.percentile_75,
        "percentile_95": result.percentile_95,
        "retirement_probability": result.retirement_probability,
        "fire_date_age": result.fire_date_age,
        "fire_date_year": result.fire_date_year,
        "swr": result.swr,
        "required_spend_for_target": result.required_spend_for_target,
        "current_portfolio": current_portfolio,
        "risk_profile_label": result.risk_profile_label,
        "account_breakdown": account_breakdown,
        "monthly_spend_avg": monthly_spend_avg,
        "settings": {
            "current_age": settings_obj.current_age,
            "retirement_age": settings_obj.retirement_age,
            "annual_contribution": settings_obj.annual_contribution,
            "annual_retirement_spending": settings_obj.annual_retirement_spending,
            "risk_tolerance": settings_obj.risk_tolerance,
            "inflation_rate": settings_obj.inflation_rate,
            "final_age": settings_obj.final_age,
            "social_security_enabled": settings_obj.social_security_enabled,
            "social_security_pia": settings_obj.social_security_pia,
            "social_security_fra": settings_obj.social_security_fra,
            "social_security_birth_month": settings_obj.social_security_birth_month,
            "social_security_birth_year": settings_obj.social_security_birth_year,
            "social_security_withdrawal_month": settings_obj.social_security_withdrawal_month,
            "social_security_withdrawal_year": settings_obj.social_security_withdrawal_year,
        }
    }


def _get_demo_settings(req: Optional[SimulateRequest]) -> Any:
    if req and req.settings:
        return req.settings
    return FireSettings(**DEMO_DEFAULTS)


def _build_demo_sim_input(settings_obj: Any, demo_portfolio: float) -> "SimulationInput":
    from .services.fire_engine import SimulationInput

    def get_val(attr):
        v = getattr(settings_obj, attr, None)
        return v if v is not None else DEMO_DEFAULTS[attr]

    return SimulationInput(
        current_portfolio=demo_portfolio,
        current_age=get_val("current_age"),
        retirement_age=get_val("retirement_age"),
        annual_contribution=get_val("annual_contribution"),
        annual_retirement_spending=get_val("annual_retirement_spending"),
        risk_tolerance=get_val("risk_tolerance"),
        inflation_rate=get_val("inflation_rate"),
        final_age=get_val("final_age"),
        social_security_enabled=get_val("social_security_enabled"),
        social_security_pia=get_val("social_security_pia"),
        social_security_fra=get_val("social_security_fra"),
        social_security_birth_month=get_val("social_security_birth_month"),
        social_security_birth_year=get_val("social_security_birth_year"),
        social_security_withdrawal_month=get_val("social_security_withdrawal_month"),
        social_security_withdrawal_year=get_val("social_security_withdrawal_year"),
    )


def _handle_demo_simulation(req: Optional[SimulateRequest]) -> dict:
    from .services.fire_engine import simulate

    settings_obj = _get_demo_settings(req)

    demo_portfolio = 500_000
    if req and req.current_portfolio is not None:
        demo_portfolio = req.current_portfolio

    sim_input = _build_demo_sim_input(settings_obj, demo_portfolio)
    result = simulate(sim_input)

    return _build_simulation_response(
        result=result,
        current_portfolio=demo_portfolio,
        account_breakdown=[],
        monthly_spend_avg=6015,
        settings_obj=sim_input,
    )


@app.post("/api/fire/simulate")
async def run_fire_simulation(request: Request, req: Optional[SimulateRequest] = None, db: AsyncSession = Depends(get_db)):
    """
    Run a full FIRE Monte Carlo simulation using live Monarch data.
    Set DEMO_MODE=1 in env to return static fictional data for recording/testing.
    """
    from .services.fire_engine import (
        SimulationInput, simulate, filter_accounts, calc_monthly_spend
    )

    # ── Demo Mode Enforcement ──────────────────────────────────────────────
    is_demo = (req and req.is_demo) or not request.state.is_authenticated

    if is_demo:
        return _handle_demo_simulation(req)
    # ── End Demo Mode ──────────────────────────────────────────────────────

    # 1. Get Monarch client
    creds = await get_latest_credentials(db)
    if not creds:
        raise HTTPException(status_code=503, detail="No Monarch credentials configured.")

    try:
        mm = await get_monarch_client(db, creds.id)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Monarch connection failed: {e}")

    # 2. Fetch accounts
    try:
        accounts_data = await mm.get_accounts()
        total_portfolio, account_breakdown = filter_accounts(accounts_data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch accounts: {e}")

    # 3. Fetch cashflow (last 12 months)
    monthly_spend = 0
    try:
        now = datetime.now()
        end_date = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        cashflow_data = await mm.get_cashflow_summary(
            start_date=start_date, end_date=end_date
        )
        monthly_spend = calc_monthly_spend(cashflow_data)
    except Exception as e:
        print(f"⚠️ Could not fetch cashflow: {e}")

    # 4. Load settings
    settings_result = await db.execute(select(FireSettings).where(FireSettings.id == 1))
    settings = settings_result.scalar_one_or_none()
    if not settings:
        settings = FireSettings(id=1)

    # 5. Build simulation input
    sim_input = SimulationInput(
        current_portfolio=total_portfolio,
        current_age=settings.current_age,
        retirement_age=settings.retirement_age,
        annual_contribution=settings.annual_contribution,
        annual_retirement_spending=settings.annual_retirement_spending,
        risk_tolerance=settings.risk_tolerance,
        inflation_rate=settings.inflation_rate,
        final_age=settings.final_age,
        social_security_enabled=settings.social_security_enabled,
        social_security_pia=settings.social_security_pia,
        social_security_fra=settings.social_security_fra,
        social_security_birth_month=settings.social_security_birth_month,
        social_security_birth_year=settings.social_security_birth_year,
        social_security_withdrawal_month=settings.social_security_withdrawal_month,
        social_security_withdrawal_year=settings.social_security_withdrawal_year,
    )

    # 6. Run simulation
    try:
        result = simulate(sim_input)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    # 7. Return results
    return _build_simulation_response(
        result=result,
        current_portfolio=result.current_portfolio,
        account_breakdown=account_breakdown,
        monthly_spend_avg=monthly_spend,
        settings_obj=settings,
    )


# =============================================================================
# 📊 SPENDING REPORT Routes
# =============================================================================

@app.get("/spending", response_class=HTMLResponse)
async def spending_page(request: Request):
    """Serve the Spending Report dashboard page."""
    import pathlib
    spending_html = pathlib.Path("bridge_app/static/spending.html").read_text()
    
    # Inject authentication state
    is_auth_str = "true" if request.state.is_authenticated else "false"
    spending_html = spending_html.replace(
        "/* INIT_AUTH_STATE */", 
        f"</style><script>window.IS_AUTHENTICATED = {is_auth_str};</script><style>"
    )
    
    return HTMLResponse(content=spending_html)


@app.get("/api/spending")
async def get_spending_report_endpoint(
    request: Request,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Fetch stored spending report for a given year."""
    target_year = year or datetime.now().year
    
    stmt = (
        select(SpendingReport)
        .where(SpendingReport.year == target_year)
        .order_by(SpendingReport.updated_at.desc())
    )
    res = await db.execute(stmt)
    report = res.scalars().first()

    if not report:
        return {
            "status": "not_found",
            "year": target_year,
            "message": f"No spending report found for {target_year}. Click Recalculate to generate one.",
        }

    return {
        "status": "ok",
        "year": report.year,
        "start_date": report.start_date,
        "end_date": report.end_date,
        "include_hidden": report.include_hidden,
        "summary": report.summary,
        "category_groups": report.category_groups,
        "categories": report.categories,
        "monthly_spending": report.monthly_spending,
        "sync_status": report.sync_status,
        "error_message": report.error_message,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


@app.post("/api/spending/recalculate")
async def recalculate_spending_report_endpoint(
    background_tasks: BackgroundTasks,
    request: Request,
    year: Optional[int] = None,
    include_hidden: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Trigger background recalculation of the spending report from Monarch Money."""
    from .services.spending_service import calculate_and_save_spending_report
    target_year = year or datetime.now().year

    stmt = select(SpendingReport).where(SpendingReport.year == target_year)
    res = await db.execute(stmt)
    report = res.scalars().first()

    if not report:
        report = SpendingReport(
            year=target_year,
            start_date=f"{target_year}-01-01",
            end_date=f"{target_year}-12-31",
            include_hidden=include_hidden,
            sync_status="syncing",
        )
        db.add(report)
    else:
        report.sync_status = "syncing"
        report.error_message = None

    await db.commit()

    background_tasks.add_task(
        calculate_and_save_spending_report,
        year=target_year,
        include_hidden=include_hidden,
    )

    return {
        "status": "syncing",
        "year": target_year,
        "message": f"Recalculation started for {target_year}.",
    }


@app.get("/api/spending/status")
async def get_spending_status_endpoint(
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Check sync/recalculation status for a given year."""
    target_year = year or datetime.now().year
    stmt = select(SpendingReport).where(SpendingReport.year == target_year).order_by(SpendingReport.updated_at.desc())
    res = await db.execute(stmt)
    report = res.scalars().first()

    if not report:
        return {"status": "not_found", "year": target_year, "sync_status": "none"}

    return {
        "status": "ok",
        "year": report.year,
        "sync_status": report.sync_status,
        "error_message": report.error_message,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }


@app.get("/api/spending/years")
async def get_spending_years_endpoint(db: AsyncSession = Depends(get_db)):
    """List all years that have reports stored in the database."""
    stmt = select(SpendingReport.year).distinct().order_by(SpendingReport.year.desc())
    res = await db.execute(stmt)
    years = [r[0] for r in res.all()]
    current_year = datetime.now().year
    if current_year not in years:
        years.insert(0, current_year)
    return {"years": sorted(list(set(years)), reverse=True)}


app.mount("/", StaticFiles(directory="bridge_app/static", html=True), name="static")

