/**
 * 桌面外壳（Electron）注入的桥：只有外壳里存在，浏览器里为 undefined。
 *
 * 由 alongside-desktop/preload.js 通过 contextBridge 暴露。
 * 页面只能拿到这两个字段 —— 拿不到 Node，也拿不到任何 cookie。
 */
export interface AlongsideDesktopBridge {
  isDesktop: true;
  /** 打开官方 CAS 登录窗；成功返回 { ok: true, logged_in, token_preview } */
  isduLogin: () => Promise<{ ok: boolean; logged_in?: boolean; token_preview?: string; error?: string }>;
}

declare global {
  interface Window {
    alongsideDesktop?: AlongsideDesktopBridge;
  }
}

export {};
