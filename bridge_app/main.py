import uuid
import asyncio
import os
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from .database import engine, Base, get_db, AsyncSessionLocal
from contextlib import asynccontextmanager
from .services.orchestrator import process_transaction
from .services.monarch import get_monarch_client
from .models import Credentials, MerchantMapping, Category, FireSettings, Transaction
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("📦 LIFESPAN: Checking database connection (this might take a moment if connecting remotely)...")
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("✅ LIFESPAN: Database connected.")
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

        # If no secret is configured, bypass security (or you could choose to block)
        if not UNLOCK_SECRET:
            request.state.is_authenticated = True
            return await call_next(request)

        # Check for cookie
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
            return await call_next(request)
            
        if request.url.path.endswith((".png", ".jpg", ".css", ".js", ".gif")):
             return await call_next(request)

        # Allow FIRE demo mode access
        if request.url.path == "/fire" or request.url.path.startswith("/api/fire/"):
            return await call_next(request)
        
        # GHOST MODE: Return 404 Not Found if unauthorized
        return Response(status_code=404, content="Not Found")

app.add_middleware(GhostSecurityMiddleware)

@app.get("/s")
async def activate(s: str):
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
    
    response = HTMLResponse(content=html_content)
    response.set_cookie(
        key=DEVICE_TOKEN_COOKIE,
        value=COOKIE_VALUE,
        max_age=60*60*24*365*10, # 10 years
        httponly=True,
        samesite="lax",
        secure=False  # Set to True if running behind HTTPS
    )
    return response

# Simple in-memory job store
# Structure: { job_id: { "status": "processing" | "completed" | "failed", "result": dict, "error": str, "inputs": dict } }
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
        else:
            display_error = f"I hit a snag: {err_msg}"

        jobs[job_id] = {"status": "failed", "error": display_error, "progress": 0}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload")
async def upload_receipt(
    file: UploadFile = File(...),
    currency: str = Form(None),
    db: AsyncSession = Depends(get_db)
):
    try:
        content = await file.read()
        result = await process_transaction(content, db, user_currency=currency)
        return {"status": "success", "data": result}
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"Error processing transaction: {e}") # Log internal error
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
                border-top: 1px solid #e2e8f0;
                margin: 0;
            }

            .deep-link-item:hover {
                background: #f8faff;
                color: #667eea;
                padding-left: 24px;
            }

            /* History Table Styles */
            .history-table {
                width: 100%;
                border-collapse: collapse;
                text-align: left;
                font-size: 0.9rem;
                margin-top: 1rem;
            }

            .history-table th {
                font-weight: bold;
                color: #d35400;
                border-bottom: 2px solid #fcc5a7;
                padding: 12px 8px;
            }

            .history-table td {
                padding: 12px 8px;
                border-bottom: 1px solid #f0f0f0;
                color: #444;
                vertical-align: middle;
            }

            .history-table tr:last-child td {
                border-bottom: none;
            }

            .amount-green, .history-table td.amount-green {
                color: #16a34a;
                font-weight: bold;
            }

            .amount-red, .history-table td.amount-red {
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
                font-size: 30px; /* Size of emoji */
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
                z-index: 3000;
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
            
            .toast.success { background-color: #be185d; } /* Dark Pink */
            .toast.error { background-color: #9f1239; } /* Darker Pink/Red for error */
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
                    <div class="menu-divider"></div>
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
                </div>
            </div>
            <div id="cardIcon" style="font-size: 3rem; margin-bottom: 0.2rem;">🎉</div>
            <p id="cardTitle" class="title">Transaction Processed</p>
            
            <div id="detailsContainer">
                <div class="detail-row">
                    <span class="label">Merchant</span>
                    <span id="merchantValue" class="value">--</span>
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
                <p id="errorMessage" style="color: #666; margin: 1rem 0;"></p>
            </div>
            
            <div style="display: flex; gap: 10px; width: 100%; justify-content: center; margin-top: 1.5rem;">
                <button id="editMappingBtn" class="btn" style="margin-top: 0; background: linear-gradient(to right, #fcad03, #f76b1c);" onclick="openMappingModal()">Edit Mapping</button>
                <a href="/" class="btn" style="margin-top: 0;">Process Another</a>
                <button id="forceSubmitBtn" class="btn" style="display:none; background: linear-gradient(to right, #ef4444, #b91c1c); margin-top: 0;" onclick="forceSubmit()">Force Submit</button>
            </div>
            <span style="font-style: italic; display: block; margin-top: 1.5rem; font-size: 0.8rem; color: #666; text-align: center; width: 100%;">20260702.1716 ©2025-26 ego/DEV/null</span>
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
                    style="overflow-x: auto; margin-top: 1rem; max-height: 400px; overflow-y: auto;">
                    <table class="history-table">
                        <thead>
                            <tr style="border-bottom: 2px solid #eee; color: #666;">
                                <th style="padding: 10px 5px;">Merchant</th>
                                <th style="padding: 10px 5px; text-align: right;">Amount</th>
                                <th style="padding: 10px 5px; text-align: center;">Date</th>
                            </tr>
                        </thead>
                        <tbody id="historyTableBody">
                            <!-- Loaded dynamically -->
                        </tbody>
                    </table>
                    <div id="historyLoading" style="text-align: center; padding: 2rem 0; color: #666;">
                        Loading transactions... ⏳
                    </div>
                    <div id="historyNoData" style="display: none; text-align: center; padding: 2rem 0; color: #666;">
                        No transactions processed yet. 📂
                    </div>
                </div>
            </div>
        </div>

        <script>
            const jobId = "__JOB_ID__";
            const pollInterval = 500; // 0.5 seconds
            
            function showToast(message, type = 'success') {
                const toast = document.getElementById("toast");
                toast.textContent = message;
                toast.className = "toast show " + type; // Reset class
                setTimeout(function(){ toast.className = toast.className.replace("show", ""); }, 3000);
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
                            // Update progress text
                            if (data.step) {
                                document.getElementById('loadingSubtitle').textContent = data.step;
                            }
                            // Update progress bar
                            if (data.progress !== undefined) {
                                const bar = document.getElementById('progressBar');
                                if (bar) bar.style.width = data.progress + '%';
                            }
                            
                            // Still processing
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
                
                const data = result.status === 'duplicate' ? result.data : result;
                const isDuplicate = result.status === 'duplicate';
                
                if (isDuplicate) {
                        document.getElementById('cardIcon').textContent = '⚠️';
                        document.getElementById('cardTitle').textContent = 'Already Processed';
                        document.getElementById('cardTitle').style.color = '#856404';
                        document.getElementById('forceSubmitBtn').style.display = 'inline-block';
                        document.getElementById('editMappingBtn').style.display = 'none';
                } else {
                    // Reset to Success State
                    document.getElementById('cardIcon').textContent = '🎉';
                    document.getElementById('cardTitle').textContent = 'Transaction Processed';
                    document.getElementById('cardTitle').style.color = 'green';
                    document.getElementById('forceSubmitBtn').style.display = 'none';
                    document.getElementById('editMappingBtn').style.display = 'inline-block';

                    // Confetti!
                    confetti({
                        particleCount: 150,
                        spread: 70,
                        origin: { y: 0.6 }
                    });
                }
                
                let amountHtml = `${parseFloat(data.amount).toFixed(2)} ${data.currency}`;
                
                // Add Deep Link if ID exists
                if (data.monarch_tx_id) {
                    const deepLink = `intent://transactions/${data.monarch_tx_id}#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%2F${data.monarch_tx_id};end`;
                    const linkColor = data.is_credit ? "#16a34a" : "#2563eb"; // Green for credits, blue for debits
                    amountHtml = `<a href="${deepLink}" style="text-decoration:none; color:${linkColor};">${amountHtml}</a>`;
                }
                
                if (data.original_amount && data.original_currency) {
                    let rateInfo = "";
                    if (data.exchange_rate) {
                        rateInfo = ` @ ${parseFloat(data.exchange_rate).toFixed(3)}`;
                    }
                    amountHtml += `<br><span style="font-size: 0.8em; color: #352224;">(${parseFloat(data.original_amount).toFixed(2)} ${data.original_currency}${rateInfo})</span>`;
                }
                
                if (data.used_historical_name || data.original_merchant_name) {
                    document.getElementById('editMappingBtn').style.display = 'inline-block';
                    document.getElementById('editMappingBtn').textContent = "Edit Mapping";
                } else {
                    document.getElementById('editMappingBtn').style.display = 'inline-block';
                    document.getElementById('editMappingBtn').textContent = "Add Mapping";
                }

                document.getElementById('amountValue').innerHTML = amountHtml;

                const isHistorical = !!data.used_historical_name;

                // Merchant name — prefix with 💜 when matched from history
                document.getElementById('merchantValue').textContent =
                    (isHistorical ? '💜 ' : '') + data.merchant;

                document.getElementById('dateValue').innerHTML = data.date;
                document.getElementById('datePicker').value = data.date;

                // Emoji + Category Name — prefix with 💜 when matched from history
                let catDisplay = data.category_name || "--";
                if (data.category_emoji) {
                    catDisplay = data.category_emoji + " " + catDisplay;
                }
                document.getElementById('categoryValue').textContent =
                    (isHistorical ? '💜 ' : '') + catDisplay;

                // Show/hide legend
                document.getElementById('historicalLegend').style.display = isHistorical ? 'block' : 'none';

                // Update "Added to" account value based on cash status
                const accountValueEl = document.getElementById('accountValue');
                if (accountValueEl) {
                    accountValueEl.textContent = data.is_cash ? "Cash On Hand" : "__MM_ACCOUNT__";
                }
                
                window.currentTransactionData = data;
                // Prefetch categories
                fetchCategories();
            }



            async function confirmDeleteMapping() {
                closeDeleteConfirm(); // Close confirmation
                
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
                         closeMappingModal(); // Close main modal
                         
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
                        
                        // Update UI Data State
                        if (!window.currentTransactionData.original_merchant_name) {
                            window.currentTransactionData.original_merchant_name = payload.receipt_merchant_name;
                        }
                        window.currentTransactionData.merchant = payload.monarch_merchant_name;
                        window.currentTransactionData.category_name = payload.category_name;
                        
                        // Update Edit/Add button text immediately
                        document.getElementById('editMappingBtn').textContent = "Edit Mapping";

                        // Update current UI text
                        document.getElementById('merchantValue').textContent = payload.monarch_merchant_name;
                        
                        // Safe category lookup
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
                select.innerHTML = '<option value="">Select Category</option>';
                if (!cachedCategories) return;

                cachedCategories.forEach(cat => {
                    const opt = document.createElement('option');
                    opt.value = cat.name;
                    opt.textContent = (cat.emoji ? cat.emoji + " " : "") + cat.name;
                    select.appendChild(opt);
                });
            }

            function openMappingModal() {
                const data = window.currentTransactionData;
                if (!data) return;

                // Use the original merchant name if it exists (meaning it was auto-mapped), 
                // otherwise use the current merchant name (OCR/Manual)
                const receiptName = data.original_merchant_name || data.merchant;

                document.getElementById('mapReceiptMerchant').value = String(receiptName).toLowerCase();
                document.getElementById('mapMonarchMerchant').value = data.merchant; // Default to current display name
                
                // Show Delete button mostly if we think a mapping exists (e.g. original name is present)
                // Or we could check if mapping endpoint returns existence?
                // Simpler: If original_merchant_name is present, it means it WAS mapped, so we can delete it.
                // If it wasn't mapped, there's nothing to delete.
                if (data.original_merchant_name) {
                    document.getElementById('deleteMappingBtn').style.display = 'block';
                } else {
                    document.getElementById('deleteMappingBtn').style.display = 'none';
                }
                
                // Select current category
                if (data.category_name) {
                     const select = document.getElementById('mapCategory');
                     // Try to match exact
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
                // Open Custom Confirmation Modal
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
                
                document.getElementById('cardIcon').textContent = '🐳';
                document.getElementById('cardTitle').textContent = 'Oops! Failed';
                document.getElementById('cardTitle').style.color = '#e53e3e';
                
                document.getElementById('detailsContainer').style.display = 'none';
                document.getElementById('errorContainer').style.display = 'block';
                document.getElementById('errorMessage').textContent = msg;
            }

            // ── Editable Date ──────────────────────────────────────────────
            function openDatePicker() {
                const picker = document.getElementById('datePicker');
                // Ensure value reflects the current displayed date
                // Strip any emoji from the pill text to get the raw date
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

                // No-op if the user picked the same date that's already stored
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

                    // Update stored data
                    data.date = result.new_date;
                    data.amount = result.new_amount;
                    if (result.exchange_rate !== null && result.exchange_rate !== undefined) {
                        data.exchange_rate = result.exchange_rate;
                    }

                    // Update date pill
                    pill.innerHTML = result.new_date;
                    document.getElementById('datePicker').value = result.new_date;

                    // Rebuild amount display
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
                    // Revert pill
                    const revDate = document.getElementById('datePicker').value || (data.date || '--');
                    pill.innerHTML = revDate;
                    showToast('Error: ' + e.message, 'error');
                } finally {
                    pill.classList.remove('updating');
                }
            }
            // ── End Editable Date ──────────────────────────────────────────

            // ── Editable Category ──────────────────────────────────────────
            async function openCategorySelector() {
                const select = document.getElementById('inlineCategorySelect');
                const pill = document.getElementById('categoryValue');
                
                // Fetch categories if we haven't yet
                await fetchCategories();
                
                // Populate inline dropdown from cachedCategories
                select.innerHTML = '<option value="">Select Category</option>';
                if (cachedCategories) {
                    cachedCategories.forEach(cat => {
                        const opt = document.createElement('option');
                        opt.value = cat.name;
                        opt.textContent = (cat.emoji ? cat.emoji + " " : "") + cat.name;
                        select.appendChild(opt);
                    });
                }
                
                // Pre-select current category
                const data = window.currentTransactionData;
                if (data && data.category_name) {
                    for(let i=0; i<select.options.length; i++) {
                        if (select.options[i].value === data.category_name) {
                            select.selectedIndex = i;
                            break;
                        }
                    }
                }
                
                // Toggle display
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

                    // Update stored data
                    data.category_name = newCategory;
                    if (result.category_emoji !== undefined) {
                        data.category_emoji = result.category_emoji;
                    } else {
                        delete data.category_emoji;
                    }

                    // Update UI text (preserving historical prefix 💜 if it existed)
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
            // ── End Editable Category ──────────────────────────────────────

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

            // Close dropdown when a link is clicked
            const dlLinks = document.querySelectorAll('.deep-link-item');
            dlLinks.forEach(link => {
                link.addEventListener('click', () => {
                    dlDropdown.style.display = 'none';
                    dlTrigger.classList.remove('open');
                });
            });

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

            closeHistoryBtn.onclick = function () {
                historyModal.style.display = "none";
            }

            window.addEventListener('click', (event) => {
                if (event.target === historyModal) {
                    historyModal.style.display = "none";
                }
            });

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
                        const row = document.createElement("tr");

                        // Merchant
                        const merchantCell = document.createElement("td");
                        const merchantText = log.merchant;
                        const cashEmoji = log.is_cash ? " 💵" : "";

                        if (log.monarch_tx_id) {
                            const deepLink = `intent://transactions/${log.monarch_tx_id}#Intent;scheme=monarchmoney;package=com.monarchmoney.mobile;S.browser_fallback_url=https%3A%2F%2Fapp.monarch.com%2Ftransactions%2F${log.monarch_tx_id};end`;
                            merchantCell.innerHTML = `<a href="${deepLink}" target="_blank" style="text-decoration: underline; color: #667eea;" title="View in Monarch">${merchantText}</a>${cashEmoji}`;
                        } else {
                            merchantCell.textContent = merchantText + cashEmoji;
                        }
                        row.appendChild(merchantCell);

                        // Amount (signed, green/red)
                        const amountCell = document.createElement("td");
                        amountCell.style.textAlign = "right";
                        const isPositive = log.amount >= 0;
                        amountCell.className = isPositive ? "amount-green" : "amount-red";

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
                            amountCell.innerHTML = `<span>${amountText}</span><br><span style="font-size: 0.75rem; color: #352224; font-style: italic; font-weight: normal;">${originalText}</span>`;
                        } else {
                            amountCell.textContent = amountText;
                        }
                        row.appendChild(amountCell);

                        // Date
                        const dateCell = document.createElement("td");
                        dateCell.style.textAlign = "center";
                        dateCell.textContent = log.date;
                        row.appendChild(dateCell);

                        historyTableBody.appendChild(row);
                    });
                } catch (err) {
                    console.error(err);
                    historyLoading.style.display = "none";
                    historyTableBody.innerHTML = `<tr><td colspan="3" style="text-align: center; color: #dc2626; padding: 2rem 0;">Error loading history logs.</td></tr>`;
                }
            }

            // Start polling
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
        creds_result = await db.execute(select(Credentials))
        creds = creds_result.scalars().first()
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
async def save_mapping(mapping: MappingRequest, db: AsyncSession = Depends(get_db)):
    """
    Create or update a merchant mapping.
    """
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

            creds_result = await db.execute(select(Credentials))
            creds = creds_result.scalars().first()
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

        creds_result = await db.execute(select(Credentials))
        creds = creds_result.scalars().first()
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
        creds_result = await db.execute(select(Credentials))
        creds = creds_result.scalars().first()
        if not creds:
            raise HTTPException(status_code=400, detail="No Monarch credentials configured")

        mm = await get_monarch_client(db, creds.id)

        # Try to get the monarch_category_id from local DB
        cat_stmt = select(Category).where(Category.category_name == req.category_name)
        cat_result = await db.execute(cat_stmt)
        category = cat_result.scalar_one_or_none()
        
        category_id = category.monarch_category_id if category else None
        if not category_id:
            raise HTTPException(status_code=400, detail=f"Category '{req.category_name}' not configured or missing monarch ID")

        # Update in Monarch Money
        await mm.update_transaction(
            transaction_id=req.monarch_tx_id,
            category_id=category_id
        )

        # Update local Transaction table's parsed_data JSON if it exists
        tx_stmt = select(Transaction)
        tx_result = await db.execute(tx_stmt)
        transactions = tx_result.scalars().all()
        for tx in transactions:
            if tx.parsed_data and tx.parsed_data.get("monarch_tx_id") == req.monarch_tx_id:
                parsed = dict(tx.parsed_data)
                parsed["category_name"] = req.category_name
                if category.category_emoji:
                    parsed["category_emoji"] = category.category_emoji
                else:
                    parsed.pop("category_emoji", None)
                tx.parsed_data = parsed
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(tx, "parsed_data")
                await db.commit()
                break

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
    Get the last 10 processed transactions from the log.
    """
    try:
        from .models import Log
        stmt = select(Log).order_by(Log.created_at.desc()).limit(10)
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
        return {
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


def _handle_demo_simulation(req: Optional[SimulateRequest]) -> dict:
    from .services.fire_engine import SimulationInput, simulate

    if req and req.settings:
        settings_obj = req.settings
    else:
        settings_obj = FireSettings(
            current_age=45,
            retirement_age=65,
            annual_contribution=100000,
            annual_retirement_spending=80000,
            risk_tolerance="moderate",
            inflation_rate=0.03,
            final_age=85,
            social_security_enabled=False,
            social_security_pia=0,
            social_security_fra=67,
            social_security_birth_month=1,
            social_security_birth_year=1980,
            social_security_withdrawal_month=1,
            social_security_withdrawal_year=2047,
        )

    demo_portfolio = 500_000
    if req and req.current_portfolio is not None:
        demo_portfolio = req.current_portfolio

    def get_val(obj, attr, default):
        v = getattr(obj, attr, None)
        return v if v is not None else default

    sim_input = SimulationInput(
        current_portfolio=demo_portfolio,
        current_age=get_val(settings_obj, "current_age", 45),
        retirement_age=get_val(settings_obj, "retirement_age", 65),
        annual_contribution=get_val(settings_obj, "annual_contribution", 100000),
        annual_retirement_spending=get_val(settings_obj, "annual_retirement_spending", 80000),
        risk_tolerance=get_val(settings_obj, "risk_tolerance", "moderate"),
        inflation_rate=get_val(settings_obj, "inflation_rate", 0.03),
        final_age=get_val(settings_obj, "final_age", 85),
        social_security_enabled=get_val(settings_obj, "social_security_enabled", False),
        social_security_pia=get_val(settings_obj, "social_security_pia", 0),
        social_security_fra=get_val(settings_obj, "social_security_fra", 67),
        social_security_birth_month=get_val(settings_obj, "social_security_birth_month", 1),
        social_security_birth_year=get_val(settings_obj, "social_security_birth_year", 1980),
        social_security_withdrawal_month=get_val(settings_obj, "social_security_withdrawal_month", 1),
        social_security_withdrawal_year=get_val(settings_obj, "social_security_withdrawal_year", 2047),
    )
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
    creds_result = await db.execute(select(Credentials))
    creds = creds_result.scalars().first()
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


app.mount("/", StaticFiles(directory="bridge_app/static", html=True), name="static")
