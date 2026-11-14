/* 小九足球 service worker —— 构建于 202611141045（内容一变浏览器就自动换版） */
var CACHE = "xj9-202611141045";
var PRE = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/favicon.ico"];
self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(PRE); })
    .then(function () { return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var r = e.request;
  if (r.method !== "GET") return;
  try { if (new URL(r.url).origin !== location.origin) return; } catch (err) { return; }  // 第三方计数器不插手
  if (r.mode === "navigate") {          // 页面：先联网（每小时重建的数据要立刻见效），断网才回缓存
    e.respondWith(fetch(r).then(function (res) {
      if (res && res.ok) { var cp = res.clone(); caches.open(CACHE).then(function (c) { c.put("/", cp); }); }
      return res;
    }).catch(function () { return caches.match("/"); }));
    return;
  }
  e.respondWith(caches.match(r).then(function (hit) {   // 图标等静态件：先缓存，缺了再联网回填
    return hit || fetch(r).then(function (res) {
      if (res && res.ok) { var cp = res.clone(); caches.open(CACHE).then(function (c) { c.put(r, cp); }); }
      return res;
    });
  }));
});
