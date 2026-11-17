/**
 * 桌面壁纸四态判据（V1-EXT2 · 主人③后半 · 单）。
 * jsdom 无真实 IndexedDB/布局，这里用 fake-indexeddb 不引入（零新依赖红线）——
 * IndexedDB 分支走 try/catch 静默降级路径，测试聚焦：四态 CSS 变量应用 / 配置
 * 持久化 / 脏值退回 / data-wp 标记。
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { act } from "@testing-library/react";

import { applyWallpaper, openWallpaperSettings, type WallpaperConfig } from "./wallpaperApply";
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

/**
 * 开面板的 dismiss 竞态回归（PR#7 实机验收抓出）：
 * openWallpaperSettings 由 click 处理器调用时，同一次 click 还会冒泡到 document。
 * 若 dismiss 监听同步挂上，会把刚开的面板当「面板外点击」立刻关掉（面板一闪即没）。
 * 修法：监听延迟到下一拍（setTimeout 0）再挂。这里用假时钟钉住时序。
 */
describe("openWallpaperSettings · dismiss 竞态", () => {
  const host = () => ({
    current: { mode: "grid" } as WallpaperConfig,
    onPick: () => {},
    onChange: () => {},
  });

  beforeEach(() => {
    document.body.innerHTML = "";
    vi.useFakeTimers();
  });

  it("开启面板的那次 click 冒泡到 document 不会关掉面板", () => {
    const btn = document.createElement("button");
    document.body.appendChild(btn);
    btn.addEventListener("click", () => openWallpaperSettings(host()));
    // 真实路径：一次 click 从按钮冒泡到 document
    btn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.querySelector(".wp-settings-host")).toBeTruthy();
    // 下一拍后 dismiss 才挂上；此后点面板外才关
    vi.runAllTimers();
    expect(document.querySelector(".wp-settings-host")).toBeTruthy();
    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.querySelector(".wp-settings-host")).toBeNull();
    vi.useRealTimers();
  });

  it("面板内点击不关面板；重开后旧 dismiss 不泄漏", () => {
    act(() => {
      openWallpaperSettings(host());
    });
    vi.runAllTimers();
    const panel = document.querySelector(".wp-settings");
    expect(panel).toBeTruthy();
    panel!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.querySelector(".wp-settings-host")).toBeTruthy();
    // 重开（closeSettings + 新 host）：旧定时挂载已跑过，无陈旧监听
    act(() => {
      openWallpaperSettings(host());
    });
    vi.runAllTimers();
    expect(document.querySelectorAll(".wp-settings-host").length).toBe(1);
    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.querySelector(".wp-settings-host")).toBeNull();
    // 再点一次外面：不应有第二个残留 dismiss 报错/重复关闭
    document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    vi.useRealTimers();
  });
});
