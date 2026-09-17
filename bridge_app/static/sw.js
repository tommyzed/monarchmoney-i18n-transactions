self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    
    // Intercept share target POST request
    if (event.request.method === 'POST' && url.pathname === '/share') {
        event.respondWith(
            fetch(event.request).catch((err) => {
                console.warn("[Service Worker] Share fetch failed:", err);
                return new Response("Network error: MM Bridge requires an active internet connection to process transactions.", {
                    status: 503,
                    statusText: "Service Unavailable"
                });
            })
        );
        return;
    }

    // Only attempt fetch for http/https protocols
    if (!url.protocol.startsWith('http')) {
        return; // Let the browser handle it natively (e.g. intent://)
    }

    event.respondWith(
        fetch(event.request).catch((err) => {
            console.warn("[Service Worker] Fetch failed:", url.href, err);
            return new Response("Failed to fetch asset", {
                status: 503,
                statusText: "Service Unavailable"
            });
        })
    );
});
