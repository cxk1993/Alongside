/**
 * 杂物库 · V1-EXT2 二期 MVP（主人③：「链接其他文件夹里图片或文件」· 纯浏览器本机形态）。
 *
 * 能力：个性化面板内「挂载本地文件夹」→ showDirectoryPicker → handle 持久化
 * IndexedDB（alongside-misc/handles/current）→ 列顶层条目 → 图片文件一键「设为壁纸」/
 * 点击预览。Chromium 渐进增强（无 API 环境 isMiscSupported()=false，面板隐藏入口）。
 *
 * 安全边界：只读（query/requestPermission 均 'read'）；handle 只进本机 IndexedDB；
 * 服务端杂物库（跨设备形态）挂 V7 通道管线，不在本件。
 */
import { createElement, useEffect, useState } from "react";

import { resolveDbName } from "@/shared/legacyStorage";

const HANDLE_KEY = "current";
const DB_NAME = "alongside-misc";
//: ★ 改名兼容：旧品牌期的库名（IndexedDB 没有改名 API，探测后再决定用哪个）。
const LEGACY_DB_NAME = "lifeos-misc";
const STORE = "handles";

export function isMiscSupported(): boolean {
  return typeof (window as { showDirectoryPicker?: unknown }).showDirectoryPicker === "function";
}

interface DirHandleExt extends FileSystemDirectoryHandle {
  queryPermission?: (d: { mode: string }) => Promise<PermissionState>;
  requestPermission?: (d: { mode: string }) => Promise<PermissionState>;
}

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

export async function saveDirHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(handle, HANDLE_KEY);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function loadDirHandle(): Promise<DirHandleExt | null> {
  try {
    const db = await openDb();
    const handle = await new Promise<DirHandleExt | null>((resolve, reject) => {
      const req = db.transaction(STORE, "readonly").objectStore(STORE).get(HANDLE_KEY);
      req.onsuccess = () => resolve((req.result as DirHandleExt) ?? null);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return handle;
  } catch {
    return null;
  }
}

export async function clearDirHandle(): Promise<void> {
  try {
    const db = await openDb();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(HANDLE_KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch {
    /* 忽略 */
  }
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|bmp|svg)$/i;

export interface MiscEntry {
  name: string;
  kind: "file" | "directory";
  isImage: boolean;
}

/** ★ 固定中文排序器：不随宿主环境 locale 漂移（测试 flake 曾因裸 localeCompare 在 en-US 下退化码点序）。 */
const NAME_COLLATOR = new Intl.Collator("zh-Hans-CN");

export async function listTopLevel(dir: DirHandleExt): Promise<MiscEntry[]> {
  const out: MiscEntry[] = [];
  // directory 值迭代在 TS lib 里以 async iterator 存在，这里窄化使用
  const iterable = dir as unknown as AsyncIterable<[string, FileSystemHandle]>;
  for await (const [name, handle] of iterable) {
    out.push({ name, kind: handle.kind === "directory" ? "directory" : "file", isImage: IMAGE_EXT.test(name) });
  }
  // ★ 排序必须显式指定 locale：裸 localeCompare 跟随运行环境（CI/服务器多为 en-US），
  //   中文目录名会退化到码点序，导致同一份代码在不同环境排序不同（测试 flake 根因）。
  //   固定 zh-Hans-CN → 中文按拼音序，且判定稳定可复现。
  return out.sort((a, b) =>
    a.kind === b.kind ? NAME_COLLATOR.compare(a.name, b.name) : a.kind === "directory" ? -1 : 1,
  );
}

export async function pickDirectory(): Promise<FileSystemDirectoryHandle | null> {
  const picker = (window as { showDirectoryPicker?: (o?: { mode?: string }) => Promise<FileSystemDirectoryHandle> })
    .showDirectoryPicker;
  if (!picker) return null;
  try {
    return await picker({ mode: "read" });
  } catch {
    return null; // 用户取消
  }
}

/* ───────── 面板区块组件（挂在 WallpaperSettings 内） ───────── */

export interface MiscSectionProps {
  onSetWallpaperFromFile: (file: File) => void | Promise<void>;
}

export function MiscFolderSection({ onSetWallpaperFromFile }: MiscSectionProps) {
  const [dir, setDir] = useState<DirHandleExt | null>(null);
  const [entries, setEntries] = useState<MiscEntry[] | null>(null);
  const [granted, setGranted] = useState(false);
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);

  useEffect(() => {
    void (async () => {
      const h = await loadDirHandle();
      if (!h) return;
      setDir(h);
      const perm = (await h.queryPermission?.({ mode: "read" })) ?? "granted";
      if (perm === "granted") {
        setGranted(true);
        setEntries(await listTopLevel(h));
      }
    })();
  }, []);

  const mount = async () => {
    const h = await pickDirectory();
    if (!h) return;
    await saveDirHandle(h);
    setDir(h);
    setGranted(true);
    setEntries(await listTopLevel(h));
  };

  const regrant = async () => {
    if (!dir) return;
    const perm = (await dir.requestPermission?.({ mode: "read" })) ?? "denied";
    if (perm === "granted") {
      setGranted(true);
      setEntries(await listTopLevel(dir));
    }
  };

  const openAsFile = async (entry: MiscEntry) => {
    if (!dir || !granted) return;
    try {
      const fh = await dir.getFileHandle(entry.name);
      const file = await fh.getFile();
      if (entry.isImage) {
        setPreview({ url: URL.createObjectURL(file), name: entry.name });
      }
    } catch {
      /* 文件消失等 */
    }
  };

  if (!isMiscSupported()) return null;

  return createElement(
    "div",
    { className: "wp-settings__row wp-misc", "aria-label": "本地文件夹" },
    !dir
      ? createElement(
          "button",
          { type: "button", className: "wp-settings__reset", onClick: () => void mount() },
          "挂载本地文件夹（只读）",
        )
      : createElement(
          "div",
          { className: "wp-misc__body" },
          createElement(
            "div",
            { className: "wp-misc__head" },
            `已挂载：${dir.name}`,
            createElement(
              "button",
              {
                type: "button",
                className: "wp-settings__close",
                onClick: () => {
                  void clearDirHandle();
                  setDir(null);
                  setEntries(null);
                  setGranted(false);
                },
              },
              "卸载",
            ),
          ),
          !granted
            ? createElement(
                "button",
                { type: "button", className: "wp-settings__reset", onClick: () => void regrant() },
                "重新授权访问",
              )
            : null,
          entries
            ? createElement(
                "ul",
                { className: "wp-misc__list" },
                entries.map((e) =>
                  createElement(
                    "li",
                    { key: `${e.kind}:${e.name}`, className: "wp-misc__item" },
                    createElement("span", null, `${e.kind === "directory" ? "📁" : "📄"} ${e.name}`),
                    e.isImage && e.kind === "file"
                      ? createElement(
                          "span",
                          { className: "wp-misc__actions" },
                          createElement(
                            "button",
                            { type: "button", onClick: () => void openAsFile(e) },
                            "预览",
                          ),
                          createElement(
                            "button",
                            {
                              type: "button",
                              onClick: async () => {
                                const fh = await dir.getFileHandle(e.name);
                                const file = await fh.getFile();
                                await onSetWallpaperFromFile(file);
                              },
                            },
                            "设为壁纸",
                          ),
                        )
                      : null,
                  ),
                ),
              )
            : createElement("div", { className: "wp-settings__hint" }, "读取中…"),
        ),
    preview
      ? createElement(
          "div",
          { className: "wp-misc__preview" },
          createElement("img", { src: preview.url, alt: preview.name }),
          createElement(
            "button",
            {
              type: "button",
              className: "wp-settings__close",
              onClick: () => {
                URL.revokeObjectURL(preview.url);
                setPreview(null);
              },
            },
            "关闭预览",
          ),
        )
      : null,
  );
}
