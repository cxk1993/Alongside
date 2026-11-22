import { describe, expect, it } from "vitest";

import type { ModuleReg } from "../../kernel/store";
import type { ModuleManifest } from "../../kernel/types";
import { buildCapabilities, collectTopics, filterCapabilities, quickCapabilities } from "./capabilities";

/**
 * P0-c · 能力条纯函数层。
 *
 * 锁五件事（每件都对应一个会让「方便」变回「不方便」的真实错法）：
 *   ① 只列启用模块 —— 停用模块点不动，列出来就是假入口；
 *   ② 排除 collab 自己 —— 在自己身上再开一扇窗是纯噪声；
 *   ③ 顺序 = 注册顺序 —— 按字母序重排会把常用的「日历/待办」排到后面；
 *   ④ 检索要能按中文名、能力名、id 三种口径命中；
 *   ⑤ topic 并集去重且只看启用模块（订阅用了停用模块的声明 = 白挂监听）。
 */

function mod(id: string, name: string, extra: Partial<ModuleManifest> = {}): ModuleReg {
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

function regs(list: ModuleReg[]): Record<string, ModuleReg> {
  return Object.fromEntries(list.map((r) => [r.manifest.id, r]));
}

describe("P0-c · buildCapabilities", () => {
  it("只列启用模块", () => {
    const out = buildCapabilities(
      regs([mod("todo", "待办"), { ...mod("old", "已停用"), enabled: false }]),
    );
    expect(out.map((e) => e.id)).toEqual(["todo"]);
  });

  it("排除 collab 自己（不给自己再开一扇窗）", () => {
    const out = buildCapabilities(regs([mod("collab", "共处工作台"), mod("todo", "待办")]));
    expect(out.map((e) => e.id)).toEqual(["todo"]);
  });

  it("保持注册顺序，不按 id 重排", () => {
    const out = buildCapabilities(regs([mod("todo", "待办"), mod("calendar", "日历")]));
    expect(out.map((e) => e.id)).toEqual(["todo", "calendar"]);
  });

  it("provides 透传、name 缺失时退回 id", () => {
    const out = buildCapabilities(
      regs([mod("calendar", "", { provides: ["calendar.event.read", 42 as unknown as string] })]),
    );
    expect(out[0].name).toBe("calendar");
    expect(out[0].provides).toEqual(["calendar.event.read"]);
  });

  it("学习插件收进 study-hub，父项占常驻位；搜子插件名也能命中父项", () => {
    const out = buildCapabilities(
      regs([
        mod("todo", "待办"),
        mod("study-hub", "学习软件"),
        mod("mooc", "中国大学MOOC"),
        mod("yuketang", "雨课堂"),
      ]),
    );
    expect(out.map((e) => e.id)).toEqual(["todo", "study-hub"]);
    const hub = out.find((e) => e.id === "study-hub")!;
    expect(hub.quick).toBe(true);
    expect(hub.keywords).toContain("雨课堂");
    expect(quickCapabilities(out, 2).map((e) => e.id)).toEqual(["study-hub", "todo"]);
  });

  it("父项缺失/停用时，子插件照常单列（入口不丢）", () => {
    const out = buildCapabilities(regs([mod("mooc", "中国大学MOOC"), mod("todo", "待办")]));
    expect(out.map((e) => e.id)).toEqual(["mooc", "todo"]);
    const out2 = buildCapabilities(
      regs([{ ...mod("study-hub", "学习软件"), enabled: false }, mod("mooc", "中国大学MOOC")]),
    );
    expect(out2.map((e) => e.id)).toEqual(["mooc"]);
  });

  it("清单为空时返回空数组（上层零渲染）", () => {
    expect(buildCapabilities({})).toEqual([]);
  });
});

describe("P0-c · filterCapabilities", () => {
  const entries = buildCapabilities(
    regs([
      mod("calendar", "日历", { provides: ["calendar.event.read"] }),
      mod("todo", "待办", { provides: ["todo.item.write"] }),
    ]),
  );

  it("空查询返回全量", () => {
    expect(filterCapabilities(entries, "   ")).toHaveLength(2);
  });

  it("中文名可命中", () => {
    expect(filterCapabilities(entries, "日历").map((e) => e.id)).toEqual(["calendar"]);
  });

  it("能力名可命中（含大小写混写）", () => {
    expect(filterCapabilities(entries, "EVENT.READ").map((e) => e.id)).toEqual(["calendar"]);
  });

  it("无命中返回空数组（不是全量）", () => {
    expect(filterCapabilities(entries, "zzz")).toEqual([]);
  });
});

describe("P0-c · quickCapabilities", () => {
  it("按 n 截断", () => {
    const entries = buildCapabilities(regs([mod("a", "A"), mod("b", "B"), mod("c", "C")]));
    expect(quickCapabilities(entries, 2).map((e) => e.id)).toEqual(["a", "b"]);
  });

  it("n=0 返回空数组而不是全量", () => {
    const entries = buildCapabilities(regs([mod("a", "A")]));
    expect(quickCapabilities(entries, 0)).toEqual([]);
  });
});

describe("P0-c · collectTopics", () => {
  it("取并集、去重、排序", () => {
    const t = collectTopics(
      regs([
        mod("todo", "待办", { emits: ["todo.item.done", "todo.item.created"] }),
        mod("cal", "日历", { emits: ["todo.item.done"] }),
      ]),
    );
    expect(t).toEqual(["todo.item.created", "todo.item.done"]);
  });

  it("忽略停用模块与非法 emits", () => {
    const t = collectTopics(
      regs([
        { ...mod("off", "停用", { emits: ["off.thing"] }), enabled: false },
        mod("odd", "怪", { emits: [1 as unknown as string, ""] }),
      ]),
    );
    expect(t).toEqual([]);
  });
});