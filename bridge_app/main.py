import uuid
import asyncio
import os
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, BackgroundTasks, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from .database import engine, Base, get_db, AsyncSessionLocal
from contextlib import asynccontextmanager
from .services.orchestrator import process_transaction
from .services.monarch import get_monarch_client
from .models import Credentials, MerchantMapping, Category, FireSettings
from sqlalchemy.future import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 LIFESPAN: Starting application startup...")
    print("📦 LIFESPAN: Initializing database tables (this might take a moment if connecting remotely)...")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ LIFESPAN: Database tables created/verified.")
    except Exception as e:
        print(f"❌ LIFESPAN: Database initialization failed: {e}")
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
        # If no secret is configured, bypass security (or you could choose to block)
        if not UNLOCK_SECRET:
            return await call_next(request)

        # Allow activation endpoint
        if request.url.path == "/s":
            return await call_next(request)
            
        # Allow static assets (manifest, Service Worker, icons) to support PWA installation.
        # Browsers often fetch these without credentials or in a separate context.
        # This exposes the *existence* of the app (if you guess the URL), but protects the functionality.
        if request.url.path in ["/manifest.json", "/sw.js", "/favicon.ico"]:
            return await call_next(request)
            
        if request.url.path.endswith((".png", ".jpg", ".css", ".js", ".gif")):
             return await call_next(request)
        
        # Check for cookie
        token = request.cookies.get(DEVICE_TOKEN_COOKIE)
        if token == COOKIE_VALUE:
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
            
            /* Mobile Optimizations */
            @media (max-width: 480px) {
                body {
                    padding: 1rem;
                    justify-content: flex-start;
                    padding-top: 15vh;
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

                <div class="detail-row">
                    <span class="label">Date</span>
                    <span id="dateValue" class="value">--</span>
                </div>
                <div class="detail-row">
                    <span class="label">Category</span>
                    <span id="categoryValue" class="value">--</span>
                </div>
                <div class="detail-row">
                    <span class="label">Added to</span>
                    <span id="accountValue" class="value">__MM_ACCOUNT__</span>
                </div>
            </div>
            
            <div id="errorContainer" style="display:none; text-align: center;">
                <p id="errorMessage" style="color: #666; margin: 1rem 0;"></p>
            </div>
            
            <div style="display: flex; gap: 10px; width: 100%; justify-content: center; margin-top: 1.5rem;">
                <button id="editMappingBtn" class="btn" style="margin-top: 0; background: linear-gradient(to right, #fcad03, #f76b1c);" onclick="openMappingModal()">Edit Mapping</button>
                <a href="/" class="btn" style="margin-top: 0;">Process Another</a>
                <button id="forceSubmitBtn" class="btn" style="display:none; background: linear-gradient(to right, #ef4444, #b91c1c); margin-top: 0;" onclick="forceSubmit()">Force Submit</button>
            </div>
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
                } else {
                    // Reset to Success State
                    document.getElementById('cardIcon').textContent = '🎉';
                    document.getElementById('cardTitle').textContent = 'Transaction Processed';
                    document.getElementById('cardTitle').style.color = 'green';
                    document.getElementById('forceSubmitBtn').style.display = 'none';

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
                    amountHtml = `<a href="${deepLink}" style="text-decoration:none; color:#2563eb;">${amountHtml}</a>`;
                }
                
                if (data.original_amount && data.original_currency) {
                    let rateInfo = "";
                    if (data.exchange_rate) {
                        rateInfo = ` @ ${parseFloat(data.exchange_rate).toFixed(3)}`;
                    }
                    amountHtml += `<br><span style="font-size: 0.8em; color: #777;">(${parseFloat(data.original_amount).toFixed(2)} ${data.original_currency}${rateInfo})</span>`;
                }
                
                if (data.original_merchant_name) {
                    document.getElementById('editMappingBtn').textContent = "Edit Mapping";
                } else {
                    document.getElementById('editMappingBtn').textContent = "Add Mapping";
                }

                document.getElementById('amountValue').innerHTML = amountHtml;
                document.getElementById('merchantValue').textContent = data.merchant;
                document.getElementById('dateValue').textContent = data.date;
                
                // Emoji + Category Name
                let catDisplay = data.category_name || "--";
                if (data.category_emoji) {
                    catDisplay = data.category_emoji + " " + catDisplay;
                }
                document.getElementById('categoryValue').textContent = catDisplay;
                
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
                document.getElementById('loadingOverlay').style.display = 'none';
                document.getElementById('resultCard').style.display = 'flex';
                
                document.getElementById('cardIcon').textContent = '🐳';
                document.getElementById('cardTitle').textContent = 'Oops! Failed';
                document.getElementById('cardTitle').style.color = '#e53e3e';
                
                document.getElementById('detailsContainer').style.display = 'none';
                document.getElementById('errorContainer').style.display = 'block';
                document.getElementById('errorMessage').textContent = msg;
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
    merchant: str = Form(...)
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
            "merchant": merchant
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

# =============================================================================
# 🔥 IGNITE — FIRE Simulation Routes
# =============================================================================

@app.get("/fire", response_class=HTMLResponse)
async def fire_page():
    """Serve the Ignite FIRE dashboard page."""
    import pathlib
    fire_html = pathlib.Path("bridge_app/static/fire.html").read_text()
    return HTMLResponse(content=fire_html)


class FireSettingsUpdate(BaseModel):
    current_age: Optional[int] = None
    retirement_age: Optional[int] = None
    annual_contribution: Optional[int] = None
    annual_retirement_spending: Optional[int] = None
    risk_tolerance: Optional[str] = None
    inflation_rate: Optional[float] = None


@app.get("/api/fire/settings")
async def get_fire_settings(db: AsyncSession = Depends(get_db)):
    """Get current FIRE simulation settings."""
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
    }


@app.put("/api/fire/settings")
async def update_fire_settings(
    updates: FireSettingsUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update FIRE simulation settings."""
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
    }


@app.post("/api/fire/simulate")
async def run_fire_simulation(db: AsyncSession = Depends(get_db)):
    """
    Run a full FIRE Monte Carlo simulation using live Monarch data.
    """
    from .services.fire_engine import (
        SimulationInput, simulate, filter_accounts, calc_monthly_spend
    )

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
    )

    # 6. Run simulation
    try:
        result = simulate(sim_input)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")

    # 7. Return results
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
        "current_portfolio": result.current_portfolio,
        "risk_profile_label": result.risk_profile_label,
        "account_breakdown": account_breakdown,
        "monthly_spend_avg": monthly_spend,
        "settings": {
            "current_age": settings.current_age,
            "retirement_age": settings.retirement_age,
            "annual_contribution": settings.annual_contribution,
            "annual_retirement_spending": settings.annual_retirement_spending,
            "risk_tolerance": settings.risk_tolerance,
            "inflation_rate": settings.inflation_rate,
        }
    }


app.mount("/", StaticFiles(directory="bridge_app/static", html=True), name="static")

