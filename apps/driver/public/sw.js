/* The driver app's service worker: keeps the app shell on the phone so the
 * app opens in a black spot. API calls (/v1) always go to the network; the
 * app's own queue looks after confirmations, and the run is kept in the
 * app's storage. The page is fetched fresh whenever there is signal, so a
 * new build reaches the phone the next time the app is opened in range. */
const CACHE = "tms-driver-v1";
const BASE = "/driver/";
const SHELL = [BASE, `${BASE}manifest.webmanifest`, `${BASE}favicon.svg`, `${BASE}icon-192.png`, `${BASE}icon-512.png`];
const ASSET = /\/driver\/assets\/[^"'()\s]+/g;
const PAGE_WAIT_MS = 4000;

async function assetsOf(response) {
  if (!response) return [];
  return [...new Set((await response.clone().text()).match(ASSET) ?? [])];
}

/** The page's scripts and styles, and the fonts its styles name; and none of
 * an older build's. */
async function keepAssets(page) {
  const cache = await caches.open(CACHE);
  const wanted = new Set(await assetsOf(page));
  for (const css of [...wanted].filter((a) => a.endsWith(".css"))) {
    let res = await cache.match(css);
    if (!res) {
      res = await fetch(css);
      if (res.ok) await cache.put(css, res.clone());
    }
    for (const a of await assetsOf(res)) wanted.add(a);
  }
  for (const a of wanted) {
    if (!(await cache.match(a))) {
      const res = await fetch(a);
      if (res.ok) await cache.put(a, res);
    }
  }
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith(`${BASE}assets/`) && !wanted.has(path)) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(SHELL);
    await keepAssets(await cache.match(BASE));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error("slow")), ms));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return;

  if (req.mode === "navigate") {
    // the page: fresh when there is signal (and quickly), the kept one when not
    event.respondWith((async () => {
      try {
        const res = await Promise.race([fetch(req), timeout(PAGE_WAIT_MS)]);
        if (res.ok) {
          const cache = await caches.open(CACHE);
          await cache.put(BASE, res.clone());
          event.waitUntil(keepAssets(res.clone()).catch(() => undefined));
        }
        return res;
      } catch {
        return (await caches.match(BASE)) ?? Response.error();
      }
    })());
    return;
  }

  // a build's files never change under their name: the kept copy, else fetch and keep it
  event.respondWith((async () => {
    const hit = await caches.match(req);
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok && url.pathname.startsWith(`${BASE}assets/`)) {
      const cache = await caches.open(CACHE);
      await cache.put(req, res.clone());
    }
    return res;
  })());
});
