/**
 * 桌面壁纸 · V1-EXT2「桌面个性化」（主人③后半：像 Win11 一样自定义背景；
 * 单）。四态：grid（默认淡线网格）/ image（自定义图片）/
 * color（自定义纯色）/ off（纯底无网格）。
 *
 * 设计约束（领地零侵入）：
 * - 状态走 localStorage（`alongside.desktop.wallpaper`），图片 Blob 走 IndexedDB
 *（alongside-wallpaper.images/current）——不碰 workbuddy 的 store.ts/Deskop.tsx；
 * - 挂载入口 = App.tsx 一行 useEffect 调 initWallpaper()；
 * - 桌面空白右键弹「个性化」菜单（window 级监听，仅 .desktop 本体命中时接管）；
 * - 图片文件只进 IndexedDB，**不进 git**（红线）。
 */
import { resolveDbName } from "@/shared/legacyStorage";

import { applyWallpaper, openWallpaperSettings, type WallpaperConfig } from "./wallpaperApply";

export type { WallpaperConfig };

const CFG_KEY = "alongside.desktop.wallpaper";
export const DEFAULT_CFG: WallpaperConfig = { mode: "grid", color: "" };

export function readWallpaperConfig(): WallpaperConfig {
  try {
    const raw = localStorage.getItem(CFG_KEY);
    if (raw) return { ...DEFAULT_CFG, ...(JSON.parse(raw) as WallpaperConfig) };
  } catch {
    /* 脏值/隐私模式 → 默认 */
  }
  return { ...DEFAULT_CFG };
}

export function saveWallpaperConfig(cfg: WallpaperConfig): void {
  try {
    localStorage.setItem(CFG_KEY, JSON.stringify(cfg));
  } catch {
    /* 同上 */
  }
}

/* ───────── IndexedDB（图片 Blob，库外不入 git） ───────── */

const DB_NAME = "alongside-wallpaper";
//: ★ 改名兼容：旧品牌期的库名（IndexedDB 没有改名 API，探测后再决定用哪个）。
const LEGACY_DB_NAME = "lifeos-wallpaper";
const STORE = "images";
const KEY = "current";

async function openDb(): Promise<IDBDatabase> {
  const dbName = await resolveDbName(DB_NAME, LEGACY_DB_NAME);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(dbName, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function saveWallpaperImage(blob: Blob): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(blob, KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function loadWallpaperImage(): Promise<Blob | null> {
  try {
    const db = await openDb();
    const blob = await new Promise<Blob | null>((resolve, reject) => {
      const req = db.transaction(STORE, "readonly").objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve((req.result as Blob) ?? null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return blob;
  } catch {
    return null;
  }
}

/* ───────── 启动初始化 + 右键菜单 ───────── */

let objectUrl: string | null = null;

/** 启动时调用一次：应用持久化壁纸 + 注册桌面空白右键菜单。返回清理函数。 */
export function initWallpaper(): () => void {
  void (async () => {
    const cfg = readWallpaperConfig();
    if (cfg.mode === "image") {
      const blob = await loadWallpaperImage();
      if (blob) {
        objectUrl = URL.createObjectURL(blob);
        applyWallpaper({ ...cfg, resolvedUrl: objectUrl });
        return;
      }
      // 图片丢失（IndexedDB 清过）→ 退回网格
      applyWallpaper(DEFAULT_CFG);
      return;
    }
    applyWallpaper(cfg);
  })();

  const onContextMenu = (e: MouseEvent) => {
    const target = e.target as HTMLElement | null;
    // 仅桌面空白（.desktop 本体）接管；窗口/坞/顶栏内放行默认
    if (!target || !target.classList.contains("desktop")) return;
    e.preventDefault();
    openWallpaperSettings({
      current: readWallpaperConfig(),
      onPick: async (file: File) => {
        await saveWallpaperImage(file);
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = URL.createObjectURL(file);
        const cfg: WallpaperConfig = { mode: "image", color: "", resolvedUrl: objectUrl };
        saveWallpaperConfig(cfg);
        applyWallpaper(cfg);
      },
      onChange: (cfg: WallpaperConfig) => {
        saveWallpaperConfig(cfg);
        if (cfg.mode === "image") {
          void loadWallpaperImage().then((blob) => {
            if (blob) {
              if (objectUrl) URL.revokeObjectURL(objectUrl);
              objectUrl = URL.createObjectURL(blob);
              applyWallpaper({ ...cfg, resolvedUrl: objectUrl });
            } else {
              applyWallpaper(DEFAULT_CFG);
            }
          });
        } else {
          applyWallpaper(cfg);
        }
      },
    });
  };

  window.addEventListener("contextmenu", onContextMenu);
  return () => window.removeEventListener("contextmenu", onContextMenu);
}
