const CACHE_NAME = 'mm-bridge-static-v1';

const STATIC_ASSET_PATTERN = /\.(?:png|gif|ico|svg|webp|woff2?)$/i;
const CDN_HOSTS = new Set([
    'cdn.jsdelivr.net',
    'fonts.gstatic.com',
    'fonts.googleapis.com'
]);

function isApiOrDynamicPath(pathname) {
    return (
        pathname.startsWith('/api/') ||
        pathname.startsWith('/job/') ||
        pathname.startsWith('/batch/') ||
        pathname.startsWith('/status/') ||
        pathname === '/share' ||
        pathname === '/history'
    );
}

function isStaticAsset(url) {
    if (CDN_HOSTS.has(url.hostname)) return true;
    if (url.pathname === '/manifest.json') return true;
    return STATIC_ASSET_PATTERN.test(url.pathname);
}

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys
                    .filter((key) => key !== CACHE_NAME)
                    .map((key) => caches.delete(key))
            )
        ).then(() => self.clients.claim())
    );
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

    // Never cache non-GET requests or dynamic API/job/batch endpoints
    if (event.request.method !== 'GET' || isApiOrDynamicPath(url.pathname)) {
        event.respondWith(
            fetch(event.request).catch((err) => {
                console.warn("[Service Worker] Fetch failed:", url.href, err);
                return new Response("Failed to fetch asset", {
                    status: 503,
                    statusText: "Service Unavailable"
                });
            })
        );
        return;
    }

    // Stale-While-Revalidate for static images, manifest, fonts, and CDN libraries
    if (isStaticAsset(url)) {
        event.respondWith(
            caches.open(CACHE_NAME).then((cache) =>
                cache.match(event.request).then((cachedResponse) => {
                    const networkFetch = fetch(event.request)
                        .then((networkResponse) => {
                            if (networkResponse && (networkResponse.ok || networkResponse.type === 'opaque')) {
                                cache.put(event.request, networkResponse.clone());
                            }
                            return networkResponse;
                        })
                        .catch((err) => {
                            if (!cachedResponse) {
                                console.warn("[Service Worker] Static asset fetch failed:", url.href, err);
                                return new Response("Failed to fetch asset", {
                                    status: 503,
                                    statusText: "Service Unavailable"
                                });
                            }
                        });

                    return cachedResponse || networkFetch;
                })
            )
        );
        return;
    }

    // Network-First for HTML navigation and all other GET requests so pages are never stale
    event.respondWith(
        fetch(event.request)
            .then((networkResponse) => {
                if (event.request.mode === 'navigate' && networkResponse && networkResponse.ok) {
                    const copy = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                }
                return networkResponse;
            })
            .catch((err) =>
                caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) return cachedResponse;
                    console.warn("[Service Worker] Fetch failed:", url.href, err);
                    return new Response("Failed to fetch asset", {
                        status: 503,
                        statusText: "Service Unavailable"
                    });
                })
            )
    );
});
