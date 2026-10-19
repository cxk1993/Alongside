/* Alongside service worker
 * 职责一：离线壳（ADR-0003 §2.5 的 PWA 落地，红线：/api/ 绝不缓存——单用户系统数据必须实时）：
 *  - /api/*、/healthz        → 直连网络（bypass）
 *  - /assets/*（hash 文件名） → cache-first（hash 变即新资源，天然安全）
 *  - 页面导航 / 壳资源        → network-first，离线回退缓存（装得上、断网能开壳）
 * 职责二：Web Push 接收端（push 插件）——push 事件 → showNotification，
 *         notificationclick → 聚焦/新开窗口。详见文件末两段。
 */
// ★★ 改本文件时**必须 bump 下面这个版本号**（H1 · 2026-09-23）：
//   ① 不 bump → 旧 hash 的 /assets/* 会永远留在同一个 cache 里（cache-first 只增不减）
//   ② 浏览器对 sw.js 只做**字节比较** → 不 bump 等于"没有新版本"，用户永远收不到更新
//   参考：bump the cache name every deploy and delete old caches on activate。
const CACHE = "lifeos-shell-v2";
const SHELL = ["/", "/login.html", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    (async () => {
      // ★ H3（2026-09-23）：开启 navigation preload —— 让浏览器在 SW 启动期间**并行**
      //   发起导航请求，省掉 SW 冷启动给首屏带来的那段延迟。
      //   不支持该 API 的浏览器（self.registration.navigationPreload 为 undefined）自动跳过。
      if (self.registration.navigationPreload) {
        await self.registration.navigationPreload.enable();
      }
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

// H1（2026-09-23）：响应前端"立即切换"指令。
// 背景：install 里本来就有无条件 skipWaiting（我们的 /assets/* 是 hash 命名、旧页面不会
// 404，业界明确说这种场景可无条件 skipWaiting，故保留不改）。本监听是给"用户点了刷新条"
// 这条路径用的，同时为将来若改成"温和更新（等待用户确认才激活）"留好口子。
self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return; // 数据永远走网络

  if (url.pathname.startsWith("/assets/")) {
    e.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const clone = res.clone();
              caches.open(CACHE).then((c) => c.put(req, clone));
            }
            return res;
          })
      )
    );
    return;
  }

  // 导航与其他静态：network-first，失败回退缓存，再失败回退壳
  // ★ H3（2026-09-23）：导航请求优先用 navigation preload 的响应（浏览器在 SW 启动期间
  //   就已并行发起），省掉冷启动延迟；非导航请求 e.preloadResponse 为 undefined → 正常 fetch。
  const net = e.preloadResponse
    ? e.preloadResponse.then((r) => r || fetch(req))
    : fetch(req);
  e.respondWith(
    net
      .then((res) => {
        if (res.ok && (req.mode === "navigate" || url.pathname.startsWith("/icons/"))) {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(req, clone));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then(
          (hit) => hit || (req.mode === "navigate" ? caches.match("/") : hit)
        )
      )
  );
});

/* ── Web Push（push 插件）：浏览器 / PWA 推送通道 ──────────────────────────
 * ★ 没有下面这段，后端发得出去、浏览器也不会弹 —— 这就是 Web Push 的最后一公里。
 * payload 形状与后端 modules/push/sender.py 对齐：{title, body, url, tag}。
 */
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Alongside", {
      body: data.body || "",
      tag: data.tag || "lifeos",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          if ("navigate" in client) client.navigate(url).catch(() => {});
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
