/**
 * 桌面壁纸四态判据（V1-EXT2 · 主人③后半 · 单）。
 * jsdom 无真实 IndexedDB/布局，这里用 fake-indexeddb 不引入（零新依赖红线）——
 * IndexedDB 分支走 try/catch 静默降级路径，测试聚焦：四态 CSS 变量应用 / 配置
 * 持久化 / 脏值退回 / data-wp 标记。
 */
import { describe, expect, it, beforeEach } from "vitest";

import { applyWallpaper, type WallpaperConfig } from "./wallpaperApply";
import { readWallpaperConfig, DEFAULT_CFG } from "./wallpaper";

function desktopEl(): HTMLElement {
  let el = document.querySelector<HTMLElement>(".desktop");
  if (!el) {
    el = document.createElement("div");
    el.className = "desktop";
    document.body.appendChild(el);
  }
  return el;
}

describe("wallpaper 四态（V1-EXT2）", () => {
  beforeEach(() => {
    localStorage.clear();
    const el = desktopEl();
    el.removeAttribute("data-wp");
    el.style.removeProperty("--wp-image");
    el.style.removeProperty("--wp-color");
    document.body.innerHTML = "";
  });

  it("grid 默认态：data-wp=grid，不设图片/颜色变量", () => {
    const el = desktopEl();
    applyWallpaper({ mode: "grid" });
    expect(el.dataset.wp).toBe("grid");
    expect(el.style.getPropertyValue("--wp-image")).toBe("");
    expect(el.style.getPropertyValue("--wp-color")).toBe("");
  });

  it("color 态：--wp-color 落变量；切走后变量被移除", () => {
    const el = desktopEl();
    applyWallpaper({ mode: "color", color: "#123456" });
    expect(el.dataset.wp).toBe("color");
    expect(el.style.getPropertyValue("--wp-color")).toBe("#123456");
    applyWallpaper({ mode: "off" });
    expect(el.dataset.wp).toBe("off");
    expect(el.style.getPropertyValue("--wp-color")).toBe("");
  });

  it("image 态：--wp-image 落 url；无 resolvedUrl 时不设", () => {
    const el = desktopEl();
    applyWallpaper({ mode: "image", resolvedUrl: "blob:http://x/1" });
    expect(el.dataset.wp).toBe("image");
    expect(el.style.getPropertyValue("--wp-image")).toContain("blob:http://x/1");
    applyWallpaper({ mode: "image" });
    expect(el.style.getPropertyValue("--wp-image")).toBe("");
  });

  it("配置持久化：save 后 read 回读一致；脏 JSON 安全退默认", () => {
    localStorage.setItem(
      "alongside.desktop.wallpaper",
      JSON.stringify({ mode: "color", color: "#abc" } satisfies WallpaperConfig),
    );
    expect(readWallpaperConfig()).toEqual({ mode: "color", color: "#abc" });
    localStorage.setItem("alongside.desktop.wallpaper", "{broken");
    expect(readWallpaperConfig()).toEqual(DEFAULT_CFG);
    expect(readWallpaperConfig().mode).toBe("grid");
  });
});
