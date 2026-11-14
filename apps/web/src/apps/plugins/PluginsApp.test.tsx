/** PluginsApp 冒烟（网络全 mock）。 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import PluginsApp from "./PluginsApp";
import { onPluginListRefresh } from "@/kernel/plugins/pluginListSignal";

vi.mock("@/shared/api/client", () => {
  return {
    ApiError: class ApiError extends Error {
      detail?: string;
      constructor(msg: string) {
        super(msg);
        this.detail = msg;
      }
    },
    api: {
      get: vi.fn().mockResolvedValue([
        { id: "calendar", name: "日程", enabled: true, kind: "builtin", version: "0.1.0" },
        { id: "query", name: "跨区块查询", enabled: false, kind: "builtin", version: "0.1.0" },
      ]),
      post: vi.fn().mockResolvedValue({}),
    },
  };
});

function wrap() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => vi.clearAllMocks());

describe("PluginsApp", () => {
  it("列出插件与启停按钮", async () => {
    render(<PluginsApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByText("日程")).toBeTruthy());
    expect(screen.getByRole("button", { name: "停用 calendar" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "启用 query" })).toBeTruthy();
  });

  it("点启用调用 enable 接口", async () => {
    const { api } = await import("@/shared/api/client");
    render(<PluginsApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByRole("button", { name: "启用 query" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "启用 query" }));
    await waitFor(() => {
      expect(api.post).toHaveBeenCalledWith("/api/v1/plugins/query/enable", {});
    });
  });

  // ★ r5：启停成功要 poke 内核清单刷新信号 —— 这是「装/卸/启停后不刷新页面即同步到桌面坞」
  //   的前端触发点；缺了它，Dock 要等下次重渲染才变（会是查不出的悬案）。
  it("启停成功后 poke 清单刷新信号（桌面坞无需刷新页面）", async () => {
    let pokes = 0;
    const off = onPluginListRefresh(() => {
      pokes += 1;
    });
    render(<PluginsApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByRole("button", { name: "启用 query" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "启用 query" }));
    await waitFor(() => expect(pokes).toBeGreaterThan(0));
    off();
  });
});
