import uuid
import asyncio
import os
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from .database import engine, Base, get_db, AsyncSessionLocal
from contextlib import asynccontextmanager
from .services.orchestrator import process_transaction

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(lifespan=lifespan)

# Simple in-memory job store
# Structure: { job_id: { "status": "processing" | "completed" | "failed", "result": dict, "error": str } }
jobs = {}

async def process_background_job(job_id: str, content: bytes):
    """
    Background task to process the transaction using a fresh DB session.
    """
    print(f"Starting background job {job_id}")
    try:
        jobs[job_id] = {"status": "processing"}
        async with AsyncSessionLocal() as db:
            result = await process_transaction(content, db)
        
        jobs[job_id] = {"status": "completed", "result": result}
        print(f"Job {job_id} completed successfully")
    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        jobs[job_id] = {"status": "failed", "error": str(e)}

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
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Monarch Money Bridge</title>
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
                    #loadingOverlay h3 {{ color: #fff; margin-top: 20px; }}
                    #loadingOverlay p {{ color: #ddd; }}
                    
                    @keyframes bounce {{
                        from {{ transform: translateY(0); }}
                        to {{ transform: translateY(-20px) rotate(5deg); }}
                    }}
                    
                    /* Error State */
                    .error-text {{ color: #e53e3e; font-weight: bold; font-size: 1.5rem; }}
                </style>
            </head>
            <body>
                <!-- Loading State (Visible initially) -->
                <div id="loadingOverlay">
                    <div class="bouncer">🧾</div>
                    <h3 id="loadingTitle">Crunching the numbers...</h3>
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
                    const pollInterval = 2000; // 2 seconds
                    
                    function checkStatus() {{
                        fetch(`/job/${{jobId}}`)
                            .then(response => response.json())
                            .then(data => {{
                                if (data.status === 'completed') {{
                                    showSuccess(data.result);
                                }} else if (data.status === 'failed') {{
                                    showError(data.error);
                                }} else {{
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
                    setTimeout(checkStatus, 1000);
                </script>
            </body>
        </html>
        """)
    except Exception as e:
        print(f"Error starting job: {e}")
        return HTMLResponse(content="Error starting job", status_code=500)

app.mount("/", StaticFiles(directory="bridge_app/static", html=True), name="static")
