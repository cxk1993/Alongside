/** 外观与皮肤面板：分区渲染、改动即时落变量、恢复默认。 */
import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { SKIN_KEY } from "@/shared/styles/skin";

import AppearanceApp from "./AppearanceApp";

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
});

describe("AppearanceApp", () => {
  it("五个分区都在", () => {
    render(<AppearanceApp />);
    for (const title of ["主色", "圆角", "密度", "字体", "阴影", "壁纸"]) {
      expect(screen.getByText(title)).toBeTruthy();
    }
  });

  it("点「紧凑」即时改间距变量并持久化（v2 三分区结构）", () => {
    render(<AppearanceApp />);
    fireEvent.click(screen.getByText("紧凑"));

    expect(document.documentElement.style.getPropertyValue("--sp-1")).toBe("3px");
    const stored = JSON.parse(window.localStorage.getItem(SKIN_KEY) ?? "{}") as {
      version?: number;
      tokens?: { theme?: Record<string, string> };
    };
    expect(stored.version).toBe(2);
    expect(stored.tokens?.theme?.["--sp-1"]).toBe("3px");
  });

  it("「恢复默认皮肤」清掉内联变量", () => {
    render(<AppearanceApp />);
    fireEvent.click(screen.getByText("宽松"));
    fireEvent.click(screen.getByTestId("appearance-reset"));

    expect(document.documentElement.style.getPropertyValue("--sp-1")).toBe("");
    expect(window.localStorage.getItem(SKIN_KEY)).toBeNull();
  });

  it("切换字体只改 --font-sans", () => {
    render(<AppearanceApp />);
    fireEvent.click(screen.getByText("衬线"));

    expect(document.documentElement.style.getPropertyValue("--font-sans")).toContain("serif");
  });

  it("拖圆角只写一个 --radius 基准（四档由 CSS 派生）", () => {
    render(<AppearanceApp />);
    fireEvent.change(screen.getByLabelText("圆角系数"), { target: { value: "1.4" } });

    expect(document.documentElement.style.getPropertyValue("--radius")).toBe("14px");
    expect(document.documentElement.style.getPropertyValue("--r-lg")).toBe("");
  });

  it("已存 v1 皮肤时面板按迁移后的值显示（不白屏、不显示乱码）", () => {
    window.localStorage.setItem(
      SKIN_KEY,
      JSON.stringify({ version: 1, accent: "#112233", radius: 1.2, density: "compact" }),
    );
    render(<AppearanceApp />);

    expect(screen.getByText("1.20×")).toBeTruthy();
    expect((screen.getByLabelText("十六进制颜色") as HTMLInputElement).value).toBe("#112233");
  });
});
