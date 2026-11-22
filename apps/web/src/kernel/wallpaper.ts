/**
 * 桌面壁纸 · V1-EXT2「桌面个性化」（主人③后半：像 Win11 一样自定义背景；
 * 单）。四态：grid（默认淡线网格）/ image（自定义图片）/
 * color（自定义纯色）/ off（纯底无网格）。
 *
 * 设计约束（领地零侵入）：
 * - 状态走 localStorage（`alongside.desktop.wallpaper`），图片 Blob 走 IndexedDB
 *（alongside-wallpaper.images/current）——不碰 workbuddy 的 store.ts/Deskop.tsx；
 * - 挂载入口 = App.tsx 一行 useEffect 调 initWallpaper()；
 * - 桌面空白右键弹「个性化」菜单（window 级监听，落点在 .desktop 内且不在交互容器里时接管）；
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

/**
 * 打开「桌面个性化」面板。
 *
 * ★ 右键菜单与「外观与皮肤」设置窗共用这一个入口 —— 两处各写一份 onPick/onChange
 *   必然会分叉（图片 objectURL 的回收最容易被漏），所以只留一份。
 */
export function openDesktopPersonalization(): void {
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
}

/**
 * 桌面「空白」右键的判据：正向全收 + 反向排除。
 *
 * 旧判据是白名单式的 `target.classList.contains("desktop")` —— 只有右键**正好落在**
 * `.desktop` 本体上才接管。可 `.desktop` 中间的大块区域被 `.desktop__mid` / `.windows`
 * 盖着，真空白处命中的是这些子容器，于是「桌面右键」本身就大面积失效；
 * 共处工作台只要把浮层的 pointer-events 打开到 auto，入口立刻全灭（点不到）。
 *
 * 所以改成反向排除：落点必须在 `.desktop` 之内、且不在任何交互容器里，才算桌面空白。
 * 新增交互层（抽屉 / 弹层 / 悬浮操作条）时把它追加进这份名单即可，
 * 而不是反过来要求每个背景层都长得像 `.desktop` 本体。
 */
export const DESKTOP_INTERACTIVE_SELECTOR = [
  ".win", // 窗口（WindowFrame）
  ".dock", // 底部模块坞
  ".topbar", // 顶栏
  ".desktop-sidebar", // 桌面级左右侧栏
  ".desktop__compass", // 罗盘导航
  ".desktop__compass-btn", // 罗盘触发按钮（独立于弹出的 .desktop__compass）
  ".desktop__clock-pill", // 顶栏收起后的时钟胶囊
  ".collab-surface", // 共处工作台背景面（其内可交互层由 desktop.css 白名单打开）
  ".search", // 全局搜索面板
  ".search-backdrop", // 搜索遮罩
  ".wp-settings-host", // 个性化面板自身
].join(", ");

/** 右键落点是不是「桌面空白」（反向排除法）。 */
export function isDesktopBlankTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  // 必须在桌面之内：挂在 body 下的弹窗 / 门户不误触
  if (!target.closest(".desktop")) return false;
  // 落在任何交互容器里都不接管，交给那个容器自己处理
  return target.closest(DESKTOP_INTERACTIVE_SELECTOR) === null;
}

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
    // 反向排除：窗口 / 坞 / 顶栏 / 侧栏 / 协作台浮层之内一律放行默认，
    // 其余落在 .desktop 内的位置才算桌面空白（判据见 isDesktopBlankTarget）。
    if (!isDesktopBlankTarget(e.target)) return;
    e.preventDefault();
    openDesktopPersonalization();
  };

  window.addEventListener("contextmenu", onContextMenu);
  return () => window.removeEventListener("contextmenu", onContextMenu);
}
