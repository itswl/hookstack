/* The boards' service worker: a board as an app on a phone. The same file in
 * all three services — scripts/assert_design.py keeps the copies identical.
 *
 * It keeps the SHELL — the page and the icons — and never the data. Every
 * read on these services needs a token, and a worker that answered a status
 * or a live feed from a cache would show a board that looks current and is
 * not; each page already says when it cannot reach its service, and that
 * sentence is the truth offline. So: the page itself is network-first with
 * the cached copy as the fallback, the static files are cache-first, and
 * everything else — the data, and any other page — is not touched here at all.
 *
 * Paths are relative to this script's own URL, so a board served under a path
 * prefix keeps its scope under that prefix without being told. Which path is
 * the page arrives in this script's own query (`sw.js?page=ui` for a console
 * that is not at its root): the page registers the worker, so the page is the
 * one that knows.
 */
const PAGE = "./" + (new URL(self.location.href).searchParams.get("page") || "");
const VERSION = "hookstack-shell-2";
const SHELL = [PAGE, "./static/icon.svg", "./static/icon-192.png", "./static/icon-512.png"];

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
  const isPage = request.mode === "navigate" && path === new URL(PAGE, self.location.href).pathname;
  const isStatic = path.startsWith(base + "static/");
  if (!isPage && !isStatic) return;  // the data, and any other page: the network, with the token, or nothing
  if (isPage) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put(PAGE, copy));
          }
          return response;
        })
        .catch(() => caches.match(PAGE))
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
