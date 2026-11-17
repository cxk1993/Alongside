/** 皮肤引擎单测：规范化、对比色、应用与撤销、旧配置兼容。 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  _resetBaseCache,
  applySkin,
  clearSkin,
  contrastFg,
  defaultSkin,
  initSkin,
  isColor,
  loadSkin,
  normalizeSkin,
  saveSkin,
  SKIN_KEY,
  softColor,
  toHex6,
} from "./skin";

/**
 * 本地 jsdom 在「不透明 origin」下没有 localStorage（访问可能抛 SecurityError）。
 * 这里补一个内存版，保证测试在任何 Node/jsdom 组合下都能跑；
 * 浏览器里走的是真 localStorage，不受影响。
 */
function ensureStorage(): void {
  try {
    if (window.localStorage) return;
  } catch {
    /* opaque origin：下面覆盖掉这个 getter */
  }
  const mem = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return mem.size;
    },
    clear: () => mem.clear(),
    getItem: (k: string) => mem.get(k) ?? null,
    key: (i: number) => Array.from(mem.keys())[i] ?? null,
    removeItem: (k: string) => {
      mem.delete(k);
    },
    setItem: (k: string, v: string) => {
      mem.set(k, String(v));
    },
  };
  Object.defineProperty(window, "localStorage", { configurable: true, value: shim });
}
beforeEach(() => {
  ensureStorage();
  window.localStorage.clear();
  document.documentElement.removeAttribute("style");
  document.documentElement.removeAttribute("data-skin");
  _resetBaseCache();
});

describe("normalizeSkin：脏值一律回退默认", () => {
  it("accent / radius / 枚举全坏也能收敛", () => {
    const s = normalizeSkin({
      accent: "not-a-color",
      radius: "abc",
      density: "huge",
      font: 42,
      shadow: null,
    });
    expect(isColor(s.accent)).toBe(true);
    expect(s.radius).toBe(1);
    expect(s.density).toBe("standard");
    expect(s.font).toBe("sans");
    expect(s.shadow).toBe("standard");
  });

  it("圆角夹在 0.5–1.5", () => {
    expect(normalizeSkin({ radius: 9 }).radius).toBe(1.5);
    expect(normalizeSkin({ radius: -3 }).radius).toBe(0.5);
    expect(normalizeSkin({ radius: 1.234 }).radius).toBe(1.23);
  });

  it("非对象输入返回默认", () => {
    expect(normalizeSkin(null).radius).toBe(1);
    expect(normalizeSkin("x").density).toBe("standard");
  });
});

describe("颜色", () => {
  it("深底配白字、浅底配黑字", () => {
    expect(contrastFg("#111111")).toBe("rgb(255, 255, 255)");
    expect(contrastFg("#ffffff")).toBe("rgb(0, 0, 0)");
  });

  it("soft 是 15% 透明", () => {
    expect(softColor("#5b8cff")).toBe("rgba(91, 140, 255, 0.15)");
  });

  it("toHex6 把 rgb 归一成 #rrggbb", () => {
    expect(toHex6("rgb(91, 140, 255)")).toBe("#5b8cff");
    expect(toHex6("#ABC")).toBe("#000000"); // 非 6 位 hex 不猜，直接回退
  });

  it("支持 hex / rgb / hsl", () => {
    expect(isColor("#5b8cff")).toBe(true);
    expect(isColor("rgb(1, 2, 3)")).toBe(true);
    expect(isColor("hsl(220 90% 60%)")).toBe(true);
    expect(isColor("red")).toBe(false);
  });
});

describe("apply / clear", () => {
  it("写入强调色、圆角与间距", () => {
    applySkin({
      version: 1,
      accent: "#ff8800",
      radius: 1.5,
      density: "compact",
      font: "mono",
      shadow: "standard",
    });
    const st = document.documentElement.style;
    expect(st.getPropertyValue("--accent")).toBe("#ff8800");
    expect(st.getPropertyValue("--accent-fg")).toBe("rgb(255, 255, 255)");
    expect(st.getPropertyValue("--r-xs")).toBe("6px"); // 4 × 1.5
    expect(st.getPropertyValue("--sp-1")).toBe("3px"); // 4 × 0.85
    expect(st.getPropertyValue("--font-sans")).toContain("monospace");
  });

  it("恢复默认会清掉内联变量与持久化", () => {
    applySkin({ ...defaultSkin(), accent: "#123456" });
    saveSkin({ ...defaultSkin(), accent: "#123456" });
    clearSkin();
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
    expect(window.localStorage.getItem(SKIN_KEY)).toBeNull();
  });
});

describe("持久化", () => {
  it("坏 JSON 不会抛，load 回默认、init 返回 null", () => {
    window.localStorage.setItem(SKIN_KEY, "{oops");
    expect(loadSkin().radius).toBe(1);
    expect(initSkin()).toBeNull();
  });

  it("initSkin 会应用持久化皮肤并打 data-skin", () => {
    saveSkin({ ...defaultSkin(), accent: "#112233", radius: 1.2 });
    const s = initSkin();
    expect(s?.accent).toBe("#112233");
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("#112233");
    expect(document.documentElement.getAttribute("data-skin")).toBe("custom");
  });

  it("没有持久化时 initSkin 不动任何东西", () => {
    expect(initSkin()).toBeNull();
    expect(document.documentElement.getAttribute("data-skin")).toBeNull();
  });
});
