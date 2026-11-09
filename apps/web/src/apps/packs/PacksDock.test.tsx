/**
 * ★ 整合包窗口的 Dock 可见性判据（A 线端到端验收 · 2026-11-09）。
 *
 * 背景：packs 是内置模块（`services/api/modules/packs/manifest.json`，
 *   `entry: "@/apps/packs"`）。前端**没有**手写注册表 —— 内核用
 *   `import.meta.glob("../apps/<dir>/index.tsx")` 按目录约定自动发现
 *   （见 `src/kernel/ModuleRegistry.ts` 的模块头）。
 *
 * 所以「窗口能不能出现在 Dock」由两件事共同决定，缺一不可：
 *   ① `entry` 能解析到真实模块（glob 命中 `src/apps/packs/index.tsx`）；
 *   ② `Dock.tsx` 的 `DOCK_MERGE` **没有**把 packs 归口到别的容器
 *      （Dock 遍历 store 全部模块、从不读 manifest.slots —— 坑谱 #15）。
 *
 * 本文件把这两条都锁住：将来谁把 packs 加进 DOCK_MERGE、或改了入口文件名，
 * 这里先红，而不是等到界面上「窗口消失」才发现。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Dock } from "@/kernel/Dock";
import { resolveLoader } from "@/kernel/ModuleRegistry";
import { useDesktopStore } from "@/kernel/store";
import type { ModuleManifest } from "@/kernel/types";

/** 与 `services/api/modules/packs/manifest.json` 保持同形的字段子集。 */
const PACKS_MANIFEST = {
  id: "packs",
  name: "整合包",
  version: "0.1.0",
  kind: "core",
  icon: "archive",
  entry: "@/apps/packs",
} as unknown as ModuleManifest;

afterEach(() => {
  cleanup();
  useDesktopStore.setState({ modules: {}, windows: [] });
});

describe("整合包 · Dock 可见性判据", () => {
  it("① 入口可解析：`@/apps/packs` 命中真实模块（自动发现，不靠手写映射表）", async () => {
    const loader = resolveLoader("@/apps/packs");
    const mod = await loader();
    expect(mod.default).toBeTruthy();
    expect((mod.default as { manifestId?: string }).manifestId).toBe("packs");
  });

  it("② 不被归口：packs 不在 DOCK_MERGE 里 ⇒ Dock 单出「整合包」按钮", () => {
    useDesktopStore.setState({
      modules: { packs: { manifest: PACKS_MANIFEST, enabled: true } },
      windows: [],
    });
    render(<Dock />);
    expect(screen.getByLabelText("整合包")).toBeTruthy();
  });

  it("③ 容器不吞：即便 system / schedule 等容器都在，packs 仍单独出现", () => {
    const mk = (id: string, name: string) =>
      ({ id, name, version: "0.1.0", kind: "builtin" }) as unknown as ModuleManifest;
    useDesktopStore.setState({
      modules: {
        system: { manifest: mk("system", "系统"), enabled: true },
        schedule: { manifest: mk("schedule", "日程待办"), enabled: true },
        knowledge: { manifest: mk("knowledge", "知识库"), enabled: true },
        packs: { manifest: PACKS_MANIFEST, enabled: true },
      },
      windows: [],
    });
    render(<Dock />);
    expect(screen.getByLabelText("整合包")).toBeTruthy();
  });

  it("④ 停用态如实反映：enabled=false 时按钮存在但不可点（不静默隐藏）", () => {
    useDesktopStore.setState({
      modules: { packs: { manifest: PACKS_MANIFEST, enabled: false } },
      windows: [],
    });
    render(<Dock />);
    const btn = screen.getByLabelText("整合包") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});
