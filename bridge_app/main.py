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
# Structure: { job_id: { "status": "processing" | "completed" | "failed", "result": dict, "error": str } }
jobs = {}

async def process_background_job(job_id: str, content: bytes):
    """
    Background task to process the transaction using a fresh DB session.
    """
    print(f"Starting background job {job_id}")
    try:
        jobs[job_id] = {"status": "processing", "step": "Initializing...", "progress": 0}
        
        async def progress_callback(step_msg, percent=None):
            jobs[job_id]["step"] = step_msg
            if percent is not None:
                jobs[job_id]["progress"] = percent
            
        async with AsyncSessionLocal() as db:
            result = await process_transaction(content, db, progress_callback=progress_callback)
        
        jobs[job_id] = {"status": "completed", "result": result, "progress": 100}
        print(f"Job {job_id} completed successfully")
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
    db: AsyncSession = Depends(get_db)
):
    try:
        content = await file.read()
        result = await process_transaction(content, db)
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
    return job

@app.post("/share")
async def handle_share(
    background_tasks: BackgroundTasks,
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
        
        # Start background task
        background_tasks.add_task(process_background_job, job_id, content)
        
        # Return Loading HTML
        return HTMLResponse(content=f"""
        <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <link rel="manifest" href="/manifest.json">
                <link rel="icon" type="image/png" href="/icon.png">
                <title>💶 Monarch Money Bridge</title>
                <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
                <style>
                    body {{ 
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
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
                    }}
                    .card {{ 
                        background: rgba(230, 255, 240, 0.98);
                        padding: 2.5rem;
                        border-radius: 20px;
                        box-shadow: 0 10px 25px rgba(0,0,0,0.2);
                        max-width: 400px;
                        width: 100%;
                        backdrop-filter: blur(10px);
                        display: none; /* Hidden by default */
                        flex-direction: column;
                        align-items: center;
                    }}
                    .title {{ font-weight: bold; font-size: 1.5rem; margin-bottom: 1rem; color: green; }}
                    .btn {{ 
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
                    }}
                    .btn:hover {{ transform: translateY(-2px); }}
                    .detail-row {{ display: flex; justify-content: space-between; align-items: center; margin: 0.5rem auto; border-bottom: 1px solid #eee; padding-bottom: 0.5rem; max-width: 320px; width: 100%; gap: 1rem; }}
                    .label {{ color: #666; }}
                    .value {{ font-weight: 600; }}
                    
                    /* Mobile Optimizations */
                    @media (max-width: 480px) {{
                        body {{
                            padding: 1rem;
                            justify-content: flex-start;
                            padding-top: 15vh;
                        }}
                    }}
                    
                    /* Loading Animation */
                    #loadingOverlay {{
                        display: flex; 
                        position: fixed; 
                        top: 0; left: 0; 
                        width: 100%; height: 100%; 
                        z-index: 1000; 
                        justify-content: center; 
                        align-items: center; 
                        flex-direction: column;
                    }}
                    .bouncer {{ font-size: 4rem; animation: bounce 1s infinite alternate; }}
                    #loadingTitle {{ color: #fff; margin-top: 20px; font-size: 1rem; }}
                    #loadingOverlay p {{ color: #ddd; }}
                    
                    @keyframes bounce {{
                        from {{ transform: translateY(0); }}
                        to {{ transform: translateY(-20px) rotate(5deg); }}
                    }}
                    
                    /* Error State */
                    .error-text {{ color: #e53e3e; font-weight: bold; font-size: 1.5rem; }}
                    /* Progress Bar */
                    .progress-container {{
                        width: 100%;
                        background-color: #e0e7ff;
                        border-radius: 9999px;
                        height: 8px;
                        margin-bottom: 20px;
                        overflow: hidden;
                        max-width: 300px;
                    }}
                    .progress-bar {{
                        height: 100%;
                        background-color: #4f46e5;
                        width: 0%;
                        transition: width 0.5s ease-out;
                        border-radius: 9999px;
                    }}
                    
                    @keyframes spin {{
                        from {{ transform: rotate(0deg); }}
                        to {{ transform: rotate(360deg); }}
                    }}

                    .spinning-emoji {{
                        display: inline-block;
                        animation: spin 2s linear infinite;
                        font-size: 30px; /* Size of emoji */
                        margin-left: 10px;
                        vertical-align: middle;
                    }}
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
                    <div id="cardIcon" style="font-size: 3rem; margin-bottom: 1rem;">🎉</div>
                    <p id="cardTitle" class="title">Transaction Processed</p>
                    
                    <div id="detailsContainer">
                        <div class="detail-row">
                            <span class="label">Amount</span>
                            <span id="amountValue" class="value">--</span>
                        </div>
                        <div class="detail-row">
                            <span class="label">Merchant</span>
                            <span id="merchantValue" class="value">--</span>
                        </div>
                    </div>
                    
                    <div id="errorContainer" style="display:none; text-align: center;">
                        <p id="errorMessage" style="color: #666; margin: 1rem 0;"></p>
                    </div>
                    
                    <a href="/" class="btn">Process Another</a>
                </div>

                <script>
                    const jobId = "{job_id}";
                    const pollInterval = 500; // 0.5 seconds
                    
                    function checkStatus() {{
                        fetch(`/job/${{jobId}}`)
                            .then(response => response.json())
                            .then(data => {{
                                if (data.status === 'completed') {{
                                    showSuccess(data.result);
                                }} else if (data.status === 'failed') {{
                                    showError(data.error);
                                }} else {{
                                    // Update progress text
                                    if (data.step) {{
                                        document.getElementById('loadingSubtitle').textContent = data.step;
                                    }}
                                    // Update progress bar
                                    if (data.progress !== undefined) {{
                                        const bar = document.getElementById('progressBar');
                                        if (bar) bar.style.width = data.progress + '%';
                                    }}
                                    
                                    // Still processing
                                    setTimeout(checkStatus, pollInterval);
                                }}
                            }})
                            .catch(err => {{
                                console.error("Polling error:", err);
                                showError("Communication error: " + err);
                            }});
                    }}
                    
                    function showSuccess(result) {{
                        document.getElementById('loadingOverlay').style.display = 'none';
                        document.getElementById('resultCard').style.display = 'flex';
                        
                        const data = result.status === 'duplicate' ? result.data : result;
                        const isDuplicate = result.status === 'duplicate';
                        
                        if (isDuplicate) {{
                             document.getElementById('cardIcon').textContent = '⚠️';
                             document.getElementById('cardTitle').textContent = 'Already Processed';
                             document.getElementById('cardTitle').style.color = '#856404';
                        }} else {{
                            // Confetti!
                            confetti({{
                                particleCount: 150,
                                spread: 70,
                                origin: {{ y: 0.6 }}
                            }});
                        }}
                        
                        document.getElementById('amountValue').textContent = `${{parseFloat(data.amount).toFixed(2)}} ${{data.currency}}`;
                        document.getElementById('merchantValue').textContent = data.merchant;
                    }}
                    
                    function showError(msg) {{
                        document.getElementById('loadingOverlay').style.display = 'none';
                        document.getElementById('resultCard').style.display = 'flex';
                        
                        document.getElementById('cardIcon').textContent = '🐳';
                        document.getElementById('cardTitle').textContent = 'Oops! Failed';
                        document.getElementById('cardTitle').style.color = '#e53e3e';
                        
                        document.getElementById('detailsContainer').style.display = 'none';
                        document.getElementById('errorContainer').style.display = 'block';
                        document.getElementById('errorMessage').textContent = msg;
                    }}

                    // Start polling
                    setTimeout(checkStatus, 100);
                </script>
            </body>
        </html>
        """)


    except Exception as e:
        print(f"Error starting job: {e}")
        return HTMLResponse(content="Error starting job", status_code=500)

app.mount("/", StaticFiles(directory="bridge_app/static", html=True), name="static")
