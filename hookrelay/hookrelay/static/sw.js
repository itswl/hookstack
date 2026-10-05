/* hookrelay's service worker: the board as an app on a phone.
 *
 * It keeps the SHELL — the page, the manifest, the icons — and never the data.
 * Every read on this service needs the token, and a worker that answered
 * /status or /live from a cache would show a board that looks current and is
 * not; the page already says "cannot reach hookrelay" when a fetch fails, and
 * that sentence is the truth offline. So: the page itself is network-first
 * with the cached copy as the fallback, the static files are cache-first, and
 * everything else is not touched here at all.
 *
 * Paths are relative to this script's own URL, so a board served under a path
 * prefix keeps its scope under that prefix without being told.
 */
const VERSION = "hookrelay-shell-1";
const SHELL = ["./", "./static/manifest.webmanifest", "./static/icon.svg", "./static/icon-192.png", "./static/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const base = new URL("./", self.location.href).pathname;
  const path = new URL(request.url).pathname;
  const isShell = request.mode === "navigate" || path === base;
  const isStatic = path.startsWith(base + "static/");
  if (!isShell && !isStatic) return;  // the data: the network, with the token, or nothing
  if (isShell) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put("./", copy));
          return response;
        })
        .catch(() => caches.match("./"))
    );
    return;
  }
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((response) => {
      const copy = response.clone();
      caches.open(VERSION).then((cache) => cache.put(request, copy));
      return response;
    }))
  );
});
