/**
 * r5-P1 · ModuleRegistry 远程入口分支与运行时注册口。
 * 硬判据：内置 glob 分支**零变化**（存在/缺失语义都不变），远程 URL 乐观可解析，
 *         运行时 loader 注册/注销可用且不引入业务名。
 */
import { afterEach, describe, expect, it } from "vitest";

import {
  canResolveLoader,
  registerRuntimeLoader,
  resolveLoader,
  unregisterRuntimeLoader,
} from "./ModuleRegistry";

afterEach(() => {
  unregisterRuntimeLoader("/plugin-assets/__test__/index.js");
});

describe("canResolveLoader · 远程 URL 乐观为真", () => {
  it("绝对 / 协议相对 / 根相对都返回 true（交给错误边界诚实呈现）", () => {
    expect(canResolveLoader("https://host/p.js")).toBe(true);
    expect(canResolveLoader("//host/p.js")).toBe(true);
    expect(canResolveLoader("/plugin-assets/p/index.js")).toBe(true);
  });

  it("内置 entry 语义不变：存在为真、缺失为假", () => {
    expect(canResolveLoader("@/apps/dashboard")).toBe(true);
    expect(canResolveLoader("@apps/dashboard")).toBe(true);
    expect(canResolveLoader("@mocks/alpha")).toBe(true);
    expect(canResolveLoader("@/apps/__not_exist_xyz__")).toBe(false);
    // ★ /src/apps/… 是构建期内置命名空间，缺失时仍是「假」（不漂成远程乐观）
    expect(canResolveLoader("/src/apps/__not_exist_xyz__")).toBe(false);
  });
});

describe("resolveLoader · 远程返回可调用 loader，内置不受影响", () => {
  it("远程 entry 返回函数（不在此真正发起网络加载）", () => {
    expect(typeof resolveLoader("https://host/p.js")).toBe("function");
    expect(typeof resolveLoader("/plugin-assets/p/index.js")).toBe("function");
  });

  it("内置 entry 仍真正加载出 default", async () => {
    const mod = await resolveLoader("@/apps/dashboard")();
    expect(mod.default).toBeTruthy();
  });

  it("未知非 URL entry 仍拒绝（不静默）", async () => {
    await expect(resolveLoader("@/apps/__not_exist_xyz__")()).rejects.toThrow("未找到插件入口");
  });
});

describe("运行时 loader 注册口（供测试/未来扩展，无业务名）", () => {
  it("注册后优先命中，注销后回落到 URL 通道", async () => {
    const entry = "/plugin-assets/__test__/index.js";
    const sentinel = { default: { manifestId: "sentinel", Component: () => null } };
    registerRuntimeLoader(entry, async () => sentinel);
    expect(await resolveLoader(entry)()).toBe(sentinel);
    unregisterRuntimeLoader(entry);
    // 注销后仍是可解析的远程 entry（返回函数），不再命中替身
    expect(typeof resolveLoader(entry)).toBe("function");
  });
});
