/**
 * 皮肤运行时契约测试（window.AlongsideSkin / 事件 / 导入导出）
 * 依据：demo重构/开源调研-皮肤与图标-20261118/05-吸收方案/01-吸收方案.md
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SKIN_KEY, defaultSkin, type Skin } from "./skin";
import {
  SKIN_EVENT,
  _resetSkinRuntime,
  commitSkin,
  exportSkinPack,
  getCurrentSkin,
  importSkinPack,
  installSkinApi,
  patchSkin,
  resetSkin,
  subscribeSkin,
} from "./skin-runtime";

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

// 合法的「皮肤包」：必须自带 --bg 与 --txt（validateSkinPack 的 requireBase 判据）
const sampleSkin: Skin = {
  version: 2,
  base: "dark",
  tokens: {
    theme: {
      "--bg": "#101014",
      "--txt": "#e8e8f0",
      "--accent": "#a78bfa",
      "--radius": "12px",
    },
    light: { "--bg": "#f7f7fb", "--txt": "#141418" },
  },
  meta: { name: "夜紫", author: "test", license: "MIT" },
};

beforeEach(() => {
  memStorage();
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-skin");
  _resetSkinRuntime();
});

afterEach(() => {
  _resetSkinRuntime();
  vi.restoreAllMocks();
});

describe("commitSkin / patchSkin", () => {
  it("提交合法皮肤：写变量 + 落盘 + 广播", () => {
    const seen: Array<Skin | null> = [];
    subscribeSkin((s) => seen.push(s));
    const r = commitSkin(sampleSkin);
    expect(r.ok).toBe(true);
    expect(getCurrentSkin()?.meta?.name).toBe("夜紫");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#a78bfa");
    expect(window.localStorage.getItem(SKIN_KEY)).toContain("夜紫");
    expect(seen.at(-1)?.version).toBe(2);
  });

  it("提交非法皮肤：一个字节都不改（不写变量、不落盘、不广播）", () => {
    const spy = vi.fn();
    subscribeSkin(spy);
    const bad = { version: 2, base: "dark", tokens: { theme: { "--bg": "url(evil)" } } };
    const r = commitSkin(bad);
    expect(r.ok).toBe(false);
    expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(SKIN_KEY)).toBeNull();
  });

  it("patchSkin 深合并三分区，未提到的键保留", () => {
    commitSkin(sampleSkin);
    const r = patchSkin({ tokens: { light: { "--bg": "#fafafa" } } });
    expect(r.ok).toBe(true);
    const cur = getCurrentSkin()!;
    expect(cur.tokens.theme?.["--accent"]).toBe("#a78bfa"); // 保留
    expect(cur.tokens.theme?.["--bg"]).toBe("#101014"); // 保留
    expect(cur.tokens.light?.["--bg"]).toBe("#fafafa"); // 新增
  });
});

describe("resetSkin", () => {
  it("清空变量与持久化，并广播 null", () => {
    commitSkin(sampleSkin);
    const seen: Array<Skin | null> = [];
    subscribeSkin((s) => seen.push(s));
    resetSkin();
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
    expect(window.localStorage.getItem(SKIN_KEY)).toBeNull();
    expect(seen.at(-1)).toBeNull();
    expect(getCurrentSkin()).toBeNull();
  });
});

describe("导入导出", () => {
  it("导出带外壳，导入回来等价", () => {
    commitSkin(sampleSkin);
    const pack = exportSkinPack();
    const parsed = JSON.parse(pack);
    expect(parsed.alongsideSkin).toBe(1);
    expect(parsed.skin.version).toBe(2);

    resetSkin();
    const r = importSkinPack(pack);
    expect(r.ok).toBe(true);
    expect(getCurrentSkin()?.meta?.name).toBe("夜紫");
  });

  it("也接受「裸 Skin」（社区里常见）", () => {
    const r = importSkinPack(JSON.stringify(sampleSkin));
    expect(r.ok).toBe(true);
    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("12px");
  });

  it("坏 JSON 与非法值都被拒，且不动当前皮肤", () => {
    commitSkin(sampleSkin);
    expect(importSkinPack("{broken").ok).toBe(false);
    const r = importSkinPack(
      JSON.stringify({ version: 2, base: "dark", tokens: { theme: { "--bg": "var(--x)" } } }),
    );
    expect(r.ok).toBe(false);
    expect(getCurrentSkin()?.meta?.name).toBe("夜紫");
  });
});

describe("window.AlongsideSkin", () => {
  it("挂载后具备六个入口，apply/get/reset 闭环", () => {
    const off = installSkinApi();
    const api = (window as unknown as { AlongsideSkin: any }).AlongsideSkin;
    expect(api.version).toBe(1);
    for (const k of ["get", "apply", "reset", "export", "import", "subscribe"]) {
      expect(typeof api[k]).toBe("function");
    }
    // 标量补丁（面板语义）：传 options 风格也只写 tokens
    const r = api.apply({ tokens: { theme: { "--accent": "#22c55e" } } });
    expect(r.ok).toBe(true);
    expect(api.get().tokens.theme["--accent"]).toBe("#22c55e");
    api.reset();
    expect(api.get()).toEqual(defaultSkin());
    off();
  });

  it("广播 alongside:skin-changed 事件，detail 为皮肤或 null", async () => {
    const hits: unknown[] = [];
    const onEvent = (e: Event) => hits.push((e as CustomEvent).detail);
    window.addEventListener(SKIN_EVENT, onEvent);
    commitSkin(sampleSkin);
    resetSkin();
    window.removeEventListener(SKIN_EVENT, onEvent);
    expect(hits.length).toBe(2);
    expect((hits[0] as Skin).version).toBe(2);
    expect(hits[1]).toBeNull();
  });
});
