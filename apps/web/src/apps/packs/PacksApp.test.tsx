/** PacksApp 冒烟（网络全 mock）。守五件事：列表 / 状态分档 / 试装报告 / 装配 / 卸包。 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import PacksApp from "./PacksApp";

vi.mock("@/shared/api/client", () => {
  return {
    ApiError: class ApiError extends Error {
      detail?: string;
      title?: string;
      constructor(msg: string) {
        super(msg);
        this.detail = msg;
        this.title = msg;
      }
    },
    api: {
      get: vi.fn(),
      post: vi.fn(),
    },
  };
});

const ROWS = [
  {
    id: "official.a",
    path: "/packs/official.a",
    source: "bundled",
    registered: true,
    installed: true,
    pack_dir_exists: true,
    name: "官方包甲",
    version: "0.1.0",
    description: "",
    icon: "",
    kind: "official",
    tagline: "",
  },
  {
    id: "local.b",
    path: "/data/packs/local.b",
    source: "local",
    registered: true,
    installed: false,
    pack_dir_exists: true,
    name: "本地包乙",
    version: "0.1.0",
    description: "",
    icon: "",
    kind: "local",
    tagline: "",
  },
  {
    id: "local.gone",
    path: "/data/packs/local.gone",
    source: "local",
    registered: true,
    installed: false,
    pack_dir_exists: false,
    name: "已删目录丙",
    version: "0.1.0",
    description: "",
    icon: "",
    kind: "local",
    tagline: "",
  },
];

function wrap() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

async function mockApi() {
  const mod = await import("@/shared/api/client");
  const api = mod.api as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };
  api.get.mockResolvedValue({ packs: ROWS, count: ROWS.length });
  api.post.mockImplementation((path: string) => {
    if (path.endsWith("/dry-run")) {
      return Promise.resolve({
        dry_run: true,
        pack: {
          pack_id: "local.b",
          status: "loaded",
          ok: true,
          error: null,
          members: [{ id: "finance", status: "loaded", reason: null }],
        },
      });
    }
    if (path.endsWith("/assemble")) {
      return Promise.resolve({
        assembled: true,
        registered: true,
        pack: {
          pack_id: "local.b",
          status: "loaded",
          ok: true,
          error: null,
          members: [{ id: "finance", status: "loaded", reason: null }],
        },
      });
    }
    return Promise.resolve({ unregistered: true, id: "local.b", count: 0 });
  });
  return api;
}

beforeEach(() => vi.clearAllMocks());

describe("PacksApp", () => {
  it("列出包名与 id", async () => {
    await mockApi();
    render(<PacksApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByText("官方包甲")).toBeTruthy());
    expect(screen.getByText("本地包乙")).toBeTruthy();
    expect(screen.getByText("local.gone")).toBeTruthy();
  });

  it("状态分三档半：已装配 / 已登记 / 目录缺失", async () => {
    await mockApi();
    render(<PacksApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByText("官方包甲")).toBeTruthy());
    expect(screen.getByText("已装配")).toBeTruthy();
    expect(screen.getByText("已登记")).toBeTruthy();
    expect(screen.getByText("目录缺失")).toBeTruthy();
  });

  it("试装后逐成员报告可见，且不调用 assemble", async () => {
    const api = await mockApi();
    render(<PacksApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByText("本地包乙")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "试装 local.b" }));
    await waitFor(() => expect(screen.getByLabelText("装配报告")).toBeTruthy());
    expect(screen.getByText("试装结果（未改动任何插件）", { exact: false })).toBeTruthy();
    expect(screen.getByText("finance")).toBeTruthy();
    const paths = api.post.mock.calls.map((c) => String(c[0]));
    expect(paths).toContain("/api/v1/packs/local.b/dry-run");
    expect(paths.some((p) => p.endsWith("/assemble"))).toBe(false);
  });

  it("点装配调用 assemble", async () => {
    const api = await mockApi();
    render(<PacksApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByRole("button", { name: "装配 local.b" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "装配 local.b" }));
    await waitFor(() => {
      expect(api.post.mock.calls.map((c) => String(c[0]))).toContain(
        "/api/v1/packs/local.b/assemble",
      );
    });
  });

  it("点卸包调用 uninstall", async () => {
    const api = await mockApi();
    render(<PacksApp />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByRole("button", { name: "卸包 local.b" })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "卸包 local.b" }));
    await waitFor(() => {
      expect(api.post.mock.calls.map((c) => String(c[0]))).toContain(
        "/api/v1/packs/local.b/uninstall",
      );
    });
  });
});
