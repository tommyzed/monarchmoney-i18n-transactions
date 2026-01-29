const JOBS = new Map();

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    // Intercept Share Target POST to show UI immediately
    if (url.pathname === '/share' && event.request.method === 'POST') {
        event.respondWith(handleShare(event));
    } else {
        // Network-only for everything else
        event.respondWith(fetch(event.request));
    }
});

async function handleShare(event) {
    const jobId = crypto.randomUUID();
    const formData = await event.request.formData();

    // Set initial status
    JOBS.set(jobId, { status: 'processing' });

    // Start background upload to the /upload API
    // This allows us to respond with HTML immediately while this continues
    fetch('/upload', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(res => {
            if (res.status === 'success') {
                JOBS.set(jobId, { status: 'completed', result: res.data });
            } else {
                JOBS.set(jobId, { status: 'failed', error: 'Upload failed' });
            }
        })
        .catch(e => {
            JOBS.set(jobId, { status: 'failed', error: e.toString() });
        });

    // Clean up memory after 10 minutes
    setTimeout(() => JOBS.delete(jobId), 600000);

    return new Response(getSharePageHTML(jobId), {
        headers: { 'Content-Type': 'text/html' }
    });
}

// Handle polling from the client page
self.addEventListener('message', event => {
    if (event.data && event.data.type === 'POLL_JOB') {
        const job = JOBS.get(event.data.jobId);
        event.source.postMessage({
            type: 'JOB_STATUS',
            jobId: event.data.jobId,
            status: job ? job.status : 'not_found',
            result: job ? (job.result || null) : null,
            error: job ? (job.error || null) : null
        });
    }
});

function getSharePageHTML(jobId) {
    return `
    <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <link rel="manifest" href="/manifest.json">
            <link rel="icon" type="image/png" href="/icon.png">
            <title>Monarch Money 💶 Bridge</title>
            <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
            <style>
                body { 
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
                }
                .card { 
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
                }
                .title { font-weight: bold; font-size: 1.5rem; margin-bottom: 1rem; color: green; }
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
                }
                .btn:hover { transform: translateY(-2px); }
                .detail-row { display: flex; justify-content: space-between; align-items: center; margin: 0.5rem auto; border-bottom: 1px solid #eee; padding-bottom: 0.5rem; max-width: 320px; width: 100%; gap: 1rem; }
                .label { color: #666; }
                .value { font-weight: 600; }
                
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
                #loadingOverlay h3 { color: #fff; margin-top: 20px; }
                #loadingOverlay p { color: #ddd; }
                
                @keyframes bounce {
                    from { transform: translateY(0); }
                    to { transform: translateY(-20px) rotate(5deg); }
                }
                
                /* Error State */
                .error-text { color: #e53e3e; font-weight: bold; font-size: 1.5rem; }
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
                const jobId = "${jobId}";
                const pollInterval = 1000;
                
                function checkStatus() {
                    if (navigator.serviceWorker.controller) {
                        navigator.serviceWorker.controller.postMessage({ type: 'POLL_JOB', jobId: jobId });
                    } else {
                        // Fallback if no controller (shouldn't happen in share flow usually)
                        console.log("No SW controller found");
                    }
                }
                
                navigator.serviceWorker.addEventListener('message', event => {
                    const data = event.data;
                    if (data.type === 'JOB_STATUS' && data.jobId === jobId) {
                        if (data.status === 'completed') {
                                showSuccess(data.result);
                        } else if (data.status === 'failed') {
                                showError(data.error);
                        } else {
                                // Still processing
                                setTimeout(checkStatus, pollInterval);
                        }
                    }
                });
                
                function showSuccess(result) {
                    document.getElementById('loadingOverlay').style.display = 'none';
                    document.getElementById('resultCard').style.display = 'flex';
                    
                    const data = result.status === 'duplicate' ? result.data : result;
                    const isDuplicate = result.status === 'duplicate';
                    
                    if (isDuplicate) {
                            document.getElementById('cardIcon').textContent = '⚠️';
                            document.getElementById('cardTitle').textContent = 'Already Processed';
                            document.getElementById('cardTitle').style.color = '#856404';
                    } else {
                        // Confetti!
                        confetti({
                            particleCount: 150,
                            spread: 70,
                            origin: { y: 0.6 }
                        });
                    }
                    
                    document.getElementById('amountValue').textContent = parseFloat(data.amount).toFixed(2) + ' ' + data.currency;
                    document.getElementById('merchantValue').textContent = data.merchant;
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
                setTimeout(checkStatus, 500);
            </script>
        </body>
    </html>
    `;
}
