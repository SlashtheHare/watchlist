const CACHE = 'covers-v3';

const COVER_HOSTS = [
  'images.igdb.com',
  'covers.openlibrary.org',
  'books.google.com',
  'lh3.googleusercontent.com',
  'cdn.akamai.steamstatic.com',
  'cdn.cloudflare.steamstatic.com',
  'media.rawg.io',
  'upload.wikimedia.org',
  'image.tmdb.org',
];

self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  let hostname;
  try { hostname = new URL(e.request.url).hostname; } catch { return; }
  if (!COVER_HOSTS.includes(hostname)) return;

  e.respondWith(
    caches.open(CACHE).then(async cache => {
      const cached = await cache.match(e.request);
      if (cached) return cached;

      try {
        const fresh = await fetch(e.request);
        if (fresh.ok) cache.put(e.request, fresh.clone());
        return fresh;
      } catch {
        return new Response('', { status: 503 });
      }
    })
  );
});
