// Service worker : coquille en cache (réseau d'abord), notifications Web Push.
const CACHE = "herdr-coquille-v39";
const COQUILLE = ["/", "/static/style.css", "/static/i18n/en.js", "/static/i18n/fr.js", "/static/i18n/i18n.js", "/static/md.js", "/static/commandes.js", "/static/palette.js", "/static/app.js",
                  "/static/vendor/qrcode/qrcode.js",
                  "/static/herdr.svg", "/static/os-logo.png", "/static/prog-claude.svg", "/static/prog-terminal.svg",
                  "/manifest.webmanifest",
                  "/static/icon-192.png", "/static/icon-512.png",
                  "/static/apple-touch-icon.png",
                  "/static/os/almalinux.svg", "/static/os/alpine.svg", "/static/os/arch.svg", "/static/os/bazzite.svg", "/static/os/cachyos.svg", "/static/os/centos.svg", "/static/os/debian.svg", "/static/os/elementary.svg", "/static/os/endeavouros.svg", "/static/os/fedora.svg", "/static/os/generic.svg", "/static/os/gentoo.svg", "/static/os/kali.svg", "/static/os/linux.svg", "/static/os/macos.svg", "/static/os/manjaro.svg", "/static/os/mint.svg", "/static/os/nixos.svg", "/static/os/opensuse.svg", "/static/os/pop.svg", "/static/os/quantum.svg", "/static/os/raspios.svg", "/static/os/rhel.svg", "/static/os/rocky.svg", "/static/os/steamos.svg", "/static/os/ubuntu.svg", "/static/os/void.svg", "/static/os/windows.svg", "/static/os/zorin.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  // /appairer/<code> : jamais mis en cache (le code ne s'écrit nulle part), toujours au réseau
  if (u.pathname.startsWith("/api/") || u.pathname === "/login" || u.pathname.startsWith("/appairer/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok) { const copie = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copie)); }
    return r;
  }).catch(() => caches.match(e.request)));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {}; }
  // l'agent n'attend plus (répondu ici, au PC, ou fermé) : sa notification « X attend » part
  if (d.clear) {
    e.waitUntil(self.registration.getNotifications({ tag: d.clear })
      .then((ns) => ns.forEach((n) => n.close())));
    return;
  }
  e.waitUntil(self.registration.showNotification(d.title || "herdr", {
    body: d.body || "", icon: "/static/icon-192.png", tag: d.addr || "herdr",
    data: { addr: d.addr || "" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const addr = (e.notification.data && e.notification.data.addr) || "";
  const url = addr ? "/#/a/" + encodeURIComponent(addr) : "/";
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((ws) => {
    for (const w of ws) {
      if ("focus" in w) { w.navigate(url).catch(() => {}); return w.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
