import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "@/shared/styles/tokens.css";
import "@/shared/styles/components.css";
import "@/kernel/desktop.css";
import "./index.css";
import { initTheme } from "@/shared/styles/theme";

// 在首次渲染前确定主题并写到 <html>，避免闪烁。
initTheme();

// 服务端状态统一由 TanStack Query 管；窗口/UI 状态由 Zustand 管（内核 store）。
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("找不到 #root 挂载点，检查 index.html");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

// PWA：生产环境注册 service worker（dev 下不注册，避免 HMR 被缓存层干扰）。
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        // ★ H1（2026-09-23）：新版本可用 → 明确告诉用户，而不是让他一直跑旧代码。
        //   起因：本站一天可能部署多次；而"应用被钉着开着"时用户可长时间停留在旧代码上
        //   （业界原文：a user with the app pinned open can stay on old code for days），
        //   屏幕上却毫无提示 —— 这是"静默失败"的 UX 形态。
        reg.addEventListener("updatefound", () => {
          const sw = reg.installing;
          if (!sw) return;
          sw.addEventListener("statechange", () => {
            // 已有 controller = 这是"更新"而非"首次安装" → 才需要提示
            if (sw.state === "installed" && navigator.serviceWorker.controller) {
              showUpdateBar(() => sw.postMessage({ type: "SKIP_WAITING" }));
            }
          });
        });
      })
      .catch(() => {
        // 注册失败不影响使用，静默即可（例如通过 IP 直连的非安全上下文）
      });

    // 新 SW 接管后刷新一次，让页面真正跑上新代码（仅当确有更新时）
    let refreshed = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshed) return;
      refreshed = true;
      window.location.reload();
    });

    // ★ H2（2026-09-23）：请求存储豁免驱逐。
    //   离线壳（SW 缓存）是"每天要用"的东西，而浏览器在磁盘压力下可能**静默清掉**它 ——
    //   那会让"断网能开壳"这项能力无声失效（又一例静默失败：
    //   the absence of an error reads as success）。
    //   Chrome 对已安装应用会自动授予，Firefox 会询问用户；不支持该 API 的浏览器直接跳过。
    if (navigator.storage && typeof navigator.storage.persist === "function") {
      void navigator.storage.persist();
    }
  });
}

/** H1：底部「新版本已就绪」提示条（原生 DOM，不引 React 复杂度）。 */
function showUpdateBar(onRefresh: () => void): void {
  if (document.getElementById("alongside-update-bar")) return; // 幂等：只挂一条
  const bar = document.createElement("div");
  bar.id = "alongside-update-bar";
  bar.setAttribute("role", "status");
  bar.style.cssText = [
    "position:fixed",
    "left:50%",
    "bottom:18px",
    "transform:translateX(-50%)",
    "z-index:2147483647",
    "display:flex",
    "align-items:center",
    "gap:12px",
    "padding:10px 16px",
    "border-radius:10px",
    "background:#1f2430",
    "color:#f2f4f8",
    "font:13px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif",
    "box-shadow:0 6px 24px rgba(0,0,0,.35)",
  ].join(";");
  const text = document.createElement("span");
  text.textContent = "新版本已就绪";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "刷新";
  btn.style.cssText =
    "cursor:pointer;border:0;border-radius:6px;padding:4px 12px;font:inherit;background:#4c8dff;color:#fff";
  btn.addEventListener("click", () => {
    bar.remove();
    onRefresh(); // → sw.js 收 SKIP_WAITING → controllerchange → reload
  });
  bar.append(text, btn);
  document.body.appendChild(bar);
}
