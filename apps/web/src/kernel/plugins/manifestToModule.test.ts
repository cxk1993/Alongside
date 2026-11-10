import { describe, expect, it } from "vitest";

import { manifestToModule } from "./PluginRegistry";
import type { PluginInfo, PluginManifest } from "./types";

/**
 * ★ 2026-11-09 回归：manifestToModule 曾只构造 9 个字段，
 *   把 contracts/plugin.schema.json 的 36 个顶层字段截掉 27 个。
 *   本文件按「映射前 vs 映射后」逐层守住：
 *     A 组（运行时读）/ B 组（强类型保留）不得丢值；
 *     C 组（不解析）必须原样透传并进入 raw 快照；
 *     未知/未来字段不得报错、不得丢。
 */

/** contracts/plugin.schema.json 的 36 个顶层字段（一个不少、一个不多）。 */
const SCHEMA_FIELDS = [
  "id",
  "name",
  "version",
  "kind",
  "minKernel",
  "kernelApi",
  "icon",
  "description",
  "author",
  "window",
  "entry",
  "api",
  "tabs",
  "absorbs",
  "provides",
  "requires",
  "optionalDependencies",
  "slots",
  "emits",
  "consumes",
  "permissions",
  "activates_on",
  "migrations",
  "settingsSchema",
  "lifecycle",
  "defines",
  "deactivation",
  "riskLevel",
  "execution",
  "sideEffects",
  "track",
  "origin",
  "concurrency",
  "schedule",
  "outputPolicy",
  "extensions",
] as const;

/** C 组：不解析、只透传的字段。 */
const C_GROUP_FIELDS = [
  "author",
  "activates_on",
  "migrations",
  "settingsSchema",
  "deactivation",
  "riskLevel",
  "execution",
  "sideEffects",
  "track",
  "origin",
  "concurrency",
  "schedule",
  "outputPolicy",
  "extensions",
] as const;

/** 一个把 36 个字段写满的容器型插件 manifest。 */
const FULL_MANIFEST = {
  id: "system",
  name: "系统",
  version: "1.2.3",
  kind: "container",
  minKernel: "0.9.0",
  kernelApi: "^1",
  icon: "gear",
  description: "容器型系统窗",
  author: "alongside",
  window: { w: 960, h: 640, minW: 720, minH: 480, singleton: true },
  entry: "@/apps/system",
  api: {
    base: "/api/v1/system",
    health: "/api/v1/system/health",
    tools: { entry: "/entries" },
  },
  tabs: [
    { key: "overview", label: "总览", entry: "dashboard" },
    { key: "export", label: "导出" },
  ],
  absorbs: ["summary"],
  provides: ["system.config.read"],
  requires: ["auth.session.read"],
  optionalDependencies: ["search.index.query"],
  slots: ["settings.page", "command.palette"],
  emits: ["system.loaded"],
  consumes: ["auth.login.succeeded"],
  permissions: { filesystem: false, network: true, subprocess: false },
  activates_on: ["startup:always"],
  migrations: "migrations",
  settingsSchema: "settings.schema.json",
  lifecycle: { onInstall: "hooks/install.py", onEnable: null, onDisable: null, onUninstall: null },
  defines: ["system.config.read"],
  deactivation: "auto",
  riskLevel: "L1",
  execution: { sandbox: "read-only", approval: "ask" },
  sideEffects: { external: false, checkpoint: "none" },
  track: "persistent",
  origin: { source: "builtin", signed: true, signature: "sha256:abc", installedBy: "human.user" },
  concurrency: { optimisticLock: true, exclusive: false, parallelSafe: ["config.read"] },
  schedule: { daily: { time: "09:00:00", timeZone: "Asia/Shanghai" } },
  outputPolicy: { maxInlineTokens: 4000, spillTo: "own", onStoreFailure: "keep-raw" },
  extensions: { note: "扩展位" },
} as const;

function plugin(manifest: unknown): PluginInfo {
  return {
    id: "system",
    name: "系统",
    version: "1.2.3",
    kind: "container",
    enabled: true,
    source: "builtin",
    manifest: manifest as PluginManifest,
  };
}

describe("manifestToModule · 36 字段不截断（2026-11-09）", () => {
  it("夹具本身写满 36 个 schema 字段（防止测试自己漏测）", () => {
    expect(Object.keys(FULL_MANIFEST)).toHaveLength(36);
    expect(Object.keys(FULL_MANIFEST).sort()).toEqual([...SCHEMA_FIELDS].sort());
  });

  it("36 字段进 → 36 字段出（+ raw 快照），一个不少", () => {
    const mod = manifestToModule(plugin(FULL_MANIFEST));
    const lost = SCHEMA_FIELDS.filter((field) => !(field in mod));
    expect(lost).toEqual([]);
    // 顶层键恰好 = schema 36 字段 + raw（没有多余、没有缺失）
    expect(Object.keys(mod).sort()).toEqual([...SCHEMA_FIELDS, "raw"].sort());
    // raw 是完整快照
    expect(mod.raw).toEqual(FULL_MANIFEST);
  });

  it("A 组（运行时要读）：api/window/slots/tabs/absorbs/provides 原值保留", () => {
    const mod = manifestToModule(plugin(FULL_MANIFEST));
    expect(mod.window).toEqual(FULL_MANIFEST.window);
    expect(mod.api).toEqual(FULL_MANIFEST.api);
    expect(mod.slots).toEqual(["settings.page", "command.palette"]);
    // 容器型模块的两块「被吞掉」的字段
    expect(mod.tabs).toEqual([
      { key: "overview", label: "总览", entry: "dashboard" },
      { key: "export", label: "导出" },
    ]);
    expect(mod.absorbs).toEqual(["summary"]);
    expect(mod.provides).toEqual(["system.config.read"]);
  });

  it("B 组（应保留）：requires/optionalDependencies/permissions/kernelApi/minKernel/defines/emits/consumes/lifecycle", () => {
    const mod = manifestToModule(plugin(FULL_MANIFEST));
    expect(mod.requires).toEqual(["auth.session.read"]);
    expect(mod.optionalDependencies).toEqual(["search.index.query"]);
    expect(mod.permissions).toEqual({ filesystem: false, network: true, subprocess: false });
    expect(mod.kernelApi).toBe("^1");
    expect(mod.minKernel).toBe("0.9.0");
    expect(mod.defines).toEqual(["system.config.read"]);
    expect(mod.emits).toEqual(["system.loaded"]);
    expect(mod.consumes).toEqual(["auth.login.succeeded"]);
    expect(mod.lifecycle).toEqual(FULL_MANIFEST.lifecycle);
  });

  it("C 组（可透传）：未解析字段在顶层与 raw 里都原样保留", () => {
    const mod = manifestToModule(plugin(FULL_MANIFEST));
    for (const key of C_GROUP_FIELDS) {
      expect(mod[key]).toEqual(FULL_MANIFEST[key]);
      expect(mod.raw?.[key]).toEqual(FULL_MANIFEST[key]);
    }
  });

  it("未知/未来字段不报错：x_ 扩展位与未登记字段照样保留", () => {
    const manifest = { ...FULL_MANIFEST, x_vendor: { a: 1 }, brandNewField: "keep-me" };
    expect(() => manifestToModule(plugin(manifest))).not.toThrow();
    const mod = manifestToModule(plugin(manifest));
    expect(mod.x_vendor).toEqual({ a: 1 });
    expect(mod.brandNewField).toBe("keep-me");
    expect(mod.raw?.brandNewField).toBe("keep-me");
  });

  it("旧数组格式 permissions 原样保留（双格式向后兼容）", () => {
    const mod = manifestToModule(plugin({ ...FULL_MANIFEST, permissions: ["db:own", "net:out"] }));
    expect(mod.permissions).toEqual(["db:own", "net:out"]);
  });

  it("脏 tabs：坏项丢弃、非数组不抛错（Tab 立面不因脏数据崩）", () => {
    const mod = manifestToModule(
      plugin({
        ...FULL_MANIFEST,
        tabs: [{ key: "ok", label: "好的" }, { key: "", label: "空键" }, "坏项", { label: "缺键" }],
      }),
    );
    expect(mod.tabs).toEqual([{ key: "ok", label: "好的" }]);

    const weird = manifestToModule(plugin({ ...FULL_MANIFEST, tabs: "not-an-array" }));
    expect(weird.tabs).toBeUndefined();
  });

  it("缺 manifest 的幽灵项：安全默认值 + raw 空对象（ISSUE-012 防御保持）", () => {
    const ghost = {
      id: "ghost",
      name: "幽灵",
      version: "0.1.0",
      kind: "builtin",
      enabled: true,
      source: "builtin",
    } as unknown as PluginInfo;
    expect(() => manifestToModule(ghost)).not.toThrow();
    const mod = manifestToModule(ghost);
    expect(mod.entry).toBe("");
    expect(mod.window).toEqual({ w: 640, h: 420 });
    expect(mod.slots).toEqual([]);
    expect(mod.raw).toEqual({});
  });
});
