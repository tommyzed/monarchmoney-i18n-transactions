self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    
    // Intercept Web Share Target POST request
    if (event.request.method === 'POST' && url.pathname === '/share') {
        event.respondWith((async () => {
            try {
                // Read formData in the Service Worker.
                // On Android Chrome, this extracts the file from the native Android share intent / content URI.
                const formData = await event.request.formData();
                const entries = Array.from(formData.entries());
                const files = entries.filter(([k, v]) => v instanceof File || (v && v.size !== undefined && v.name !== undefined));

                // Send telemetry to server so we see what was inside the intent on the device
                event.waitUntil(
                    fetch('/api/sw-log', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            event: "share_intercepted",
                            keys: Array.from(formData.keys()),
                            files: files.map(([k, v]) => ({ key: k, name: v.name, size: v.size, type: v.type }))
                        })
                    }).catch(() => {})
                );

                if (files.length > 0) {
                    // Re-POST the extracted FormData freshly.
                    // This avoids Chromium's bug where passing event.request directly to fetch() drops the body stream.
                    return await fetch(event.request.url, {
                        method: 'POST',
                        body: formData
                    });
                } else {
                    return await fetch(event.request);
                }
            } catch (err) {
                event.waitUntil(
                    fetch('/api/sw-log', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            event: "share_error",
                            error: String(err)
                        })
                    }).catch(() => {})
                );
                return await fetch(event.request);
            }
        })());
        return;
    }

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
