/**
 * 图标映射层单测：语义名解析、整包替换、未知降级。
 * 依据：demo重构/开源调研-皮肤与图标-20261118/03-图标系统-Lucide-Iconify/调研笔记.md
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Icon } from "./Icon";
import { DEFAULT_ICON_LIBRARY_MAP, DEFAULT_ICON_SET } from "./default-set";
import {
  _resetIconSets,
  isGlyphFallback,
  listIconSets,
  registerIconSet,
  resolveIcon,
} from "./registry";

afterEach(() => _resetIconSets());

describe("默认图标集", () => {
  it("覆盖当前 manifest 在用的全部语义名", () => {
    // 实测在用（40 处 → 24 去重），含 3 个曾 MISS 的：gear / api / dashboard
    for (const name of [
      "bot",
      "palette",
      "shield",
      "box",
      "api",
      "download",
      "compass",
      "book-open",
      "archive",
      "bell",
      "search",
      "dashboard",
      "gear",
    ]) {
      expect(DEFAULT_ICON_SET[name], `缺 ${name}`).toBeTruthy();
    }
  });

  it("库内名映射符合调研结论（gear→settings、api→plug、dashboard→layout-dashboard）", () => {
    expect(DEFAULT_ICON_LIBRARY_MAP.gear).toBe("settings");
    expect(DEFAULT_ICON_LIBRARY_MAP.api).toBe("plug");
    expect(DEFAULT_ICON_LIBRARY_MAP.dashboard).toBe("layout-dashboard");
  });
});

describe("registry", () => {
  it("未知名解析为 undefined（交给兜底链）", () => {
    expect(resolveIcon("no-such-icon")).toBeUndefined();
  });

  it("注册另一套图标可整包替换，同名键被覆盖、未覆盖的回落到默认集", () => {
    const off = registerIconSet({
      id: "test:set",
      name: "测试集",
      license: "MIT",
      icons: { box: <path d="M0 0h24v24H0z" /> },
    });
    expect(listIconSets()).toContain("test:set");
    // 覆盖生效
    expect(resolveIcon("box")).toBeTruthy();
    expect(resolveIcon("box")).not.toBe(DEFAULT_ICON_SET.box);
    // 未覆盖的回落
    expect(resolveIcon("gear")).toBe(DEFAULT_ICON_SET.gear);
    // 注销后恢复
    off();
    expect(resolveIcon("box")).toBe(DEFAULT_ICON_SET.box);
    expect(listIconSets()).not.toContain("test:set");
  });

  it("单字判定：中文单字走字形兜底，多字不走", () => {
    expect(isGlyphFallback("课")).toBe(true);
    expect(isGlyphFallback("box")).toBe(false);
  });

  it("别名收敛：manifest 里的 globe 解析到 web", () => {
    expect(resolveIcon("globe")).toBe(DEFAULT_ICON_SET.web);
  });
});

describe("<Icon> 渲染", () => {
  it("已知语义名渲染 svg，并带 data-icon 便于审计", () => {
    const { container } = render(<Icon name="palette" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeTruthy();
    expect(svg!.getAttribute("data-icon")).toBe("palette");
    expect(svg!.getAttribute("width")).toBe("18");
  });

  it("中文单字原样显示（保留旧兜底行为）", () => {
    const { container } = render(<Icon name="课" />);
    expect(container.textContent).toBe("课");
    expect(container.querySelector("svg")).toBeNull();
  });

  it("未知名用 fallback 首字；都没有则 ·", () => {
    const a = render(<Icon name="no-such" fallback="日历" />);
    expect(a.container.textContent).toBe("日");
    const b = render(<Icon name="no-such" />);
    expect(b.container.textContent).toBe("·");
  });

  it("传 label 时对读屏可见（role=img）", () => {
    render(<Icon name="bell" label="通知" />);
    expect(screen.getByRole("img", { name: "通知" })).toBeTruthy();
  });
});
