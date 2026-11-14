/**
 * r5-P1 · 运行时远程入口加载器（runtimeLoader）单测。
 * 判据：URL 形状判定（绝对/协议相对/根相对都认，内置说明符不认）、
 *       带鉴权 fetch、HTTP 失败诚实报错、缺 default 导出报错、Blob 执行缝可注入。
 * 网络与 Blob 执行全部 mock，不依赖真实资产端点。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "@/shared/api/client";
import {
  __setRemoteImporterForTest,
  isRemoteEntry,
  loadRemoteModule,
  resolveRemoteUrl,
} from "./runtimeLoader";

const OK_MODULE = { default: { manifestId: "p", Component: () => null } };

function stubFetch(impl: () => Promise<Partial<Response>>) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  // jsdom 不一定实现对象 URL；加载器会用到，统一打桩。
  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();
  __setRemoteImporterForTest(async () => OK_MODULE);
});

afterEach(() => {
  __setRemoteImporterForTest(null);
  vi.unstubAllGlobals();
  setToken(null);
  vi.restoreAllMocks();
});

describe("isRemoteEntry · URL 形状判定", () => {
  it("绝对 / 协议相对 / 根相对都认（根相对是后端实际吐的形状，不能漏）", () => {
    expect(isRemoteEntry("https://host/p.js")).toBe(true);
    expect(isRemoteEntry("http://host/p.js")).toBe(true);
    expect(isRemoteEntry("//host/p.js")).toBe(true);
    expect(isRemoteEntry("/plugin-assets/p/index.js")).toBe(true);
  });

  it("内置说明符一律不认（仍走构建期 glob）", () => {
    expect(isRemoteEntry("@/apps/dashboard")).toBe(false);
    expect(isRemoteEntry("@apps/dashboard")).toBe(false);
    expect(isRemoteEntry("@mocks/alpha")).toBe(false);
    expect(isRemoteEntry("../apps/dashboard")).toBe(false);
    expect(isRemoteEntry("")).toBe(false);
  });

  it("根相对但落在内置源码命名空间（/src/apps/…）的不算远程 —— 保住内置原语义", () => {
    expect(isRemoteEntry("/src/apps/dashboard/index.tsx")).toBe(false);
    expect(isRemoteEntry("/src/apps/__missing__/index.tsx")).toBe(false);
  });
});

describe("resolveRemoteUrl · 规整为绝对 URL", () => {
  it("根相对按当前 origin 补全", () => {
    expect(resolveRemoteUrl("/plugin-assets/p/index.js")).toBe(
      `${window.location.origin}/plugin-assets/p/index.js`,
    );
  });
  it("绝对 URL 原样", () => {
    expect(resolveRemoteUrl("https://host/p.js")).toBe("https://host/p.js");
  });
});

describe("loadRemoteModule · 带鉴权取回并执行", () => {
  it("成功：fetch 带 Authorization，返回 { default: PluginModule }", async () => {
    setToken("tok123");
    const fetchMock = stubFetch(async () => ({ ok: true, status: 200, text: async () => "code" }));
    const mod = await loadRemoteModule("/plugin-assets/p/index.js");
    expect(mod.default.manifestId).toBe("p");
    expect(fetchMock).toHaveBeenCalledWith(`${window.location.origin}/plugin-assets/p/index.js`, {
      headers: { Accept: "*/*", Authorization: "Bearer tok123" },
    });
    // 用完即 revoke，避免对象 URL 泄漏
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake");
  });

  it("未登录（无 token）：不带 Authorization 头", async () => {
    setToken(null);
    const fetchMock = stubFetch(async () => ({ ok: true, status: 200, text: async () => "code" }));
    await loadRemoteModule("/plugin-assets/p/index.js");
    expect(fetchMock).toHaveBeenCalledWith(expect.any(String), { headers: { Accept: "*/*" } });
  });

  it("HTTP 404（未启用/无 UI）：抛出含状态码的诚实错误，不静默", async () => {
    stubFetch(async () => ({ ok: false, status: 404, text: async () => "" }));
    await expect(loadRemoteModule("/plugin-assets/p/index.js")).rejects.toThrow("HTTP 404");
  });

  it("网络不可用：抛出可辨识错误", async () => {
    stubFetch(async () => {
      throw new TypeError("failed to fetch");
    });
    await expect(loadRemoteModule("/plugin-assets/p/index.js")).rejects.toThrow("网络不可用");
  });

  it("模块缺 default 导出：报错而不是塞个空壳", async () => {
    stubFetch(async () => ({ ok: true, status: 200, text: async () => "code" }));
    __setRemoteImporterForTest(async () => ({ notDefault: 1 }));
    await expect(loadRemoteModule("/plugin-assets/p/index.js")).rejects.toThrow("default");
  });
});
