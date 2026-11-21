import { describe, expect, it, vi } from "vitest";

import { buildCommands, filterCommands, groupCommands, isRunnable } from "./commands";
import { makeWorkspace, type ModuleReg } from "./store";
import type { ModuleManifest } from "./types";

/**
 * T17 · 命令面板纯函数层测试。
 *
 * 锁四件事（每件对应一个真实会出错的假设）：
 *   ① 只列**启用**模块 —— 停用模块出现 = 点不动的假入口；
 *   ② 动作标题随状态翻转 —— 按钮文案与事实相反比没有按钮更坏；
 *   ③ 能力条目**只读**且去重 —— 同名能力两条一模一样，点哪条都一样；
 *   ④ 检索规则（中文/英文/大小写/空查询）—— 打字打不出来就等于没有面板。
 */

function mod(
  id: string,
  name: string,
  extra: Partial<ModuleManifest> = {},
): ModuleReg {
  const manifest: ModuleManifest = {
    id,
    name,
    kind: "builtin",
    entry: `@/apps/${id}`,
    icon: "grid",
    window: { w: 640, h: 420 },
    ...extra,
  } as ModuleManifest;
  return { manifest, enabled: true };
}

function deps(over: Partial<Parameters<typeof buildCommands>[0]> = {}) {
  const calls = {
    openModule: vi.fn(),
    openPluginManager: vi.fn(),
    toggleTopbar: vi.fn(),
    toggleBottombar: vi.fn(),
    tidyDesktop: vi.fn(),
    minimizeAllUnpinned: vi.fn(),
    createWorkspace: vi.fn(),
    switchWorkspace: vi.fn(),
    toggleTheme: vi.fn(),
  };
  return {
    calls,
    d: {
      modules: {
        notes: mod("notes", "笔记"),
        todo: mod("todo", "待办"),
        off: { ...mod("off", "停用模块"), enabled: false },
      } as Record<string, ModuleReg>,
      workspaces: [makeWorkspace(1), makeWorkspace(2)],
      activeWorkspaceId: "ws1",
      topbarCollapsed: false,
      dockCollapsed: true,
      openModule: calls.openModule,
      toggleTopbar: calls.toggleTopbar,
      toggleBottombar: calls.toggleBottombar,
      tidyDesktop: calls.tidyDesktop,
      minimizeAllUnpinned: calls.minimizeAllUnpinned,
      createWorkspace: calls.createWorkspace,
      switchWorkspace: calls.switchWorkspace,
      toggleTheme: calls.toggleTheme,
      ...over,
    },
  };
}

describe("T17 · buildCommands", () => {
  it("只列已启用模块（停用模块不出现，避免假入口）", () => {
    const { d } = deps();
    const ids = buildCommands(d).map((c) => c.id);
    expect(ids).toContain("mod:notes");
    expect(ids).toContain("mod:todo");
    expect(ids).not.toContain("mod:off");
  });

  it("分组顺序固定：模块 → 动作 → 能力（只读）", () => {
    const { d } = deps({
      modules: { notes: mod("notes", "笔记", { provides: ["notes.read"] }) },
    });
    const groups = groupCommands(buildCommands(d)).map((b) => b.group);
    expect(groups).toEqual(["模块", "动作", "能力（只读）"]);
  });

  it("Dock 文案随状态翻转，run 真调 toggleBottombar", () => {
    const shown = buildCommands(deps({ dockCollapsed: true }).d).find(
      (c) => c.id === "act:toggle-dock",
    );
    expect(shown?.title).toBe("显示 Dock（模块坞）");

    const { d, calls } = deps({ dockCollapsed: false });
    const hidden = buildCommands(d).find((c) => c.id === "act:toggle-dock");
    expect(hidden?.title).toBe("隐藏 Dock（模块坞）");
    hidden?.run();
    expect(calls.toggleBottombar).toHaveBeenCalledTimes(1);
  });

  it("工作区：当前那个不列，其它可切", () => {
    const { d, calls } = deps();
    const wsCmds = buildCommands(d).filter((c) => c.id.startsWith("ws:"));
    expect(wsCmds.map((c) => c.id)).toEqual(["ws:ws2"]);
    wsCmds[0].run();
    expect(calls.switchWorkspace).toHaveBeenCalledWith("ws2");
  });

  it("能力组：只读（run 只是跳到提供者），同名能力只留一条", () => {
    const a = mod("cal", "日历", { provides: ["calendar.event.read", "calendar.event.write"] });
    const b = mod("cal2", "日历二", { provides: ["calendar.event.read"] });
    const { d, calls } = deps({
      modules: { cal: a, cal2: b, notes: mod("notes", "笔记") },
    });
    const caps = buildCommands(d).filter((c) => c.group === "能力（只读）");
    // 两个模块都声明了 calendar.event.read → 只出现一次
    expect(caps.filter((c) => c.title === "能力 · calendar.event.read")).toHaveLength(1);
    expect(caps.map((c) => c.title).sort()).toEqual([
      "能力 · calendar.event.read",
      "能力 · calendar.event.write",
    ]);
    caps[0].run();
    // 只跳转，不执行任何"能力调用"——v1 没有这个映射
    expect(calls.openModule).toHaveBeenCalledWith("cal");
  });

  it("插件管理：无提供者时不出这条；有则 run 指向它", () => {
    const { d } = deps();
    expect(buildCommands(d).some((c) => c.id === "act:plugin-manager")).toBe(false);

    const spy = vi.fn();
    const r = deps({ openPluginManager: spy });
    const cmd = buildCommands(r.d).find((c) => c.id === "act:plugin-manager");
    expect(cmd).toBeTruthy();
    cmd?.run();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("T17 · filterCommands", () => {
  const list = buildCommands(
    deps({
      modules: {
        notes: mod("notes", "笔记"),
        cal: mod("cal", "日历", { provides: ["calendar.event.read"] }),
      },
    }).d,
  );

  it("空查询返回全部", () => {
    expect(filterCommands(list, "   ")).toHaveLength(list.length);
  });

  it("中文、英文、大小写都能命中", () => {
    expect(filterCommands(list, "笔记").map((c) => c.id)).toEqual(["mod:notes"]);
    expect(filterCommands(list, "DOCK").some((c) => c.id === "act:toggle-dock")).toBe(true);
    expect(filterCommands(list, "calendar.event").some((c) => c.id === "cap:calendar.event.read")).toBe(
      true,
    );
  });

  it("分组名可检索（输入「能力」只剩能力组）", () => {
    const hits = filterCommands(list, "能力");
    expect(hits.length).toBeGreaterThan(0);
    expect(hits.every((c) => c.group === "能力（只读）")).toBe(true);
  });

  it("无匹配返回空数组（不是 undefined，也不是全量）", () => {
    expect(filterCommands(list, "zzz-none")).toEqual([]);
  });
});

describe("T17 · groupCommands / isRunnable", () => {
  it("空组不出现（不给人看只有标题的空分区）", () => {
    const onlyModules = groupCommands(
      buildCommands(deps({ modules: { notes: mod("notes", "笔记") } }).d).filter(
        (c) => c.group === "模块",
      ),
    );
    expect(onlyModules.map((b) => b.group)).toEqual(["模块"]);
  });

  it("disabled 的命令不可执行", () => {
    expect(isRunnable(undefined)).toBe(false);
    expect(isRunnable({ id: "x", title: "x", group: "动作", keywords: [], disabled: true, run: () => {} })).toBe(
      false,
    );
    expect(isRunnable({ id: "y", title: "y", group: "动作", keywords: [], run: () => {} })).toBe(true);
  });
});