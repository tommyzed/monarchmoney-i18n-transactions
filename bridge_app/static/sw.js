self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    
    // Allow POST requests (such as /share Web Share Target) to pass directly to the network
    // without Service Worker stream interception, avoiding Chromium body-dropping bugs on Android.
    if (event.request.method === 'POST') {
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
