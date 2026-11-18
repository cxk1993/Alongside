/** 皮肤库单测：存取/去重/导入导出/删除不动当前外观。 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Skin } from "./skin";
import {
  SKIN_LIBRARY_KEY,
  applyFromLibrary,
  exportFromLibrary,
  importToLibrary,
  loadLibrary,
  removeFromLibrary,
  renameInLibrary,
  saveToLibrary,
} from "./skin-library";
import type { AlongsideSkinApi } from "./skin-runtime";

const skinA: Skin = {
  version: 2,
  base: "dark",
  tokens: { theme: { "--bg": "#12121a", "--txt": "#e8e8f0", "--accent": "#ff7a59" } },
  meta: { name: "橙夜" },
};

const skinB: Skin = {
  version: 2,
  base: "dark",
  tokens: { theme: { "--bg": "#0d1117", "--txt": "#e6edf3", "--accent": "#58a6ff" } },
  meta: { name: "深海" },
};

function memStorage(): void {
  const mem = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return mem.size;
    },
    clear: () => mem.clear(),
    getItem: (k) => mem.get(k) ?? null,
    key: (i) => Array.from(mem.keys())[i] ?? null,
    removeItem: (k) => void mem.delete(k),
    setItem: (k, v) => void mem.set(k, String(v)),
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: shim });
}

/** 记录 apply 调用的小替身（库层只依赖 api.apply / api.get） */
function fakeApi(): { api: AlongsideSkinApi; applied: Skin[] } {
  const applied: Skin[] = [];
  const api = {
    version: 1,
    get: () => skinA,
    apply: (s: Skin) => {
      applied.push(s);
      return { ok: true, errors: [], warnings: [] };
    },
    reset: () => {},
    export: () => "{}",
    import: () => ({ ok: true, errors: [], warnings: [] }),
    subscribe: () => () => {},
  } as unknown as AlongsideSkinApi;
  return { api, applied };
}

beforeEach(() => {
  memStorage();
  window.localStorage.clear();
});

afterEach(() => window.localStorage.clear());

describe("皮肤库", () => {
  it("存入后能读回，同指纹覆盖不重复", () => {
    const a = saveToLibrary(skinA);
    const b = saveToLibrary(skinA, "改名了");
    const items = loadLibrary();
    expect(items.length).toBe(1);
    expect(a.id).toBe(b.id);
    expect(items[0].name).toBe("改名了");
    expect(items[0].accent).toBe("#ff7a59");
  });

  it("多份皮肤按最近在前；删除只影响库，不碰当前外观", () => {
    saveToLibrary(skinA);
    saveToLibrary(skinB);
    expect(loadLibrary().map((i) => i.name)).toEqual(["深海", "橙夜"]);
    removeFromLibrary(loadLibrary()[0].id);
    expect(loadLibrary().map((i) => i.name)).toEqual(["橙夜"]);
    // 库键变化，但「当前皮肤」键未被触碰
    expect(window.localStorage.getItem(SKIN_LIBRARY_KEY)).toBeTruthy();
    expect(window.localStorage.getItem("alongside.skin.v1")).toBeNull();
  });

  it("重命名保留条目与时间", () => {
    const a = saveToLibrary(skinA);
    renameInLibrary(a.id, "  改过名的  ");
    const items = loadLibrary();
    expect(items[0].name).toBe("改过名的");
    expect(items[0].savedAt).toBe(a.savedAt);
  });

  it("导入走同一校验器：坏包被拒不入库，好包入库并保留名字", () => {
    const bad = importToLibrary(
      JSON.stringify({ version: 2, base: "dark", tokens: { theme: { "--bg": "url(x)" } } }),
    );
    expect(bad.ok).toBe(false);
    expect(loadLibrary().length).toBe(0);

    const good = importToLibrary(
      JSON.stringify({
        alongsideSkin: 1,
        meta: { name: "导入的" },
        skin: { ...skinB, meta: { name: "导入的" } },
      }),
    );
    expect(good.ok).toBe(true);
    expect(loadLibrary()[0].name).toBe("导入的");
  });

  it("导出可再导入（往返一致）", () => {
    const a = saveToLibrary(skinA);
    const pack = exportFromLibrary(a.id)!;
    const parsed = JSON.parse(pack);
    expect(parsed.alongsideSkin).toBe(1);
    removeFromLibrary(a.id);
    expect(loadLibrary().length).toBe(0);
    expect(importToLibrary(pack).ok).toBe(true);
    expect(loadLibrary()[0].accent).toBe("#ff7a59");
  });

  it("应用库里的皮肤会调用 api.apply，并把皮肤原样传过去", () => {
    const { api, applied } = fakeApi();
    const a = saveToLibrary(skinA);
    const r = applyFromLibrary(a.id, api);
    expect(r.ok).toBe(true);
    expect(applied.length).toBe(1);
    expect(applied[0].meta?.name).toBe("橙夜");
  });

  it("应用不存在的 id 不报错崩溃，返回可读错误", () => {
    const { api } = fakeApi();
    const r = applyFromLibrary("nope", api);
    expect(r.ok).toBe(false);
    expect(r.errors[0]).toContain("皮肤不在库里");
  });

  it("超大包被拒（>64KB）", () => {
    const huge = `{"alongsideSkin":1,"skin":${'{"a":"b"},'.repeat(8000)}"x"}`;
    const r = importToLibrary(huge);
    expect(r.ok).toBe(false);
    expect(r.errors[0]).toContain("过大");
  });
});
