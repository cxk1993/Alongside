/**
 * 笔记模块前端测试：列表 / 搜索 / 详情 / 空态 / 桥离线 / 同步（网络全 mock）。
 * ★ vi.mock 工厂会被提升到文件顶部，工厂里不能引用顶层 const（TDZ），
 *   夹具必须用 vi.hoisted 放进工厂可见的作用域。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import NotesApp from "./NotesApp";
import { notesApi, type NoteBrief } from "./api";
import { ApiError } from "@/shared/api/client";

const { brief, brief2, lib, disabledLib } = vi.hoisted(() => {
  const brief = {
    id: "n1",
    lib_id: "l1",
    lib_key: "main",
    rel_path: "a/one.md",
    title: "周报",
    excerpt: "进度摘要",
    mtime: 1,
    size: 10,
    synced_at: null,
  };
  const brief2 = {
    id: "n2",
    lib_id: "l1",
    lib_key: "main",
    rel_path: "b/two.md",
    title: "",
    excerpt: "",
    mtime: 2,
    size: 20,
    synced_at: null,
  };
  const lib = { id: "l1", key: "main", name: "主仓库", enabled: true, md_count: 1 };
  const disabledLib = {
    id: "l2",
    key: "claude",
    name: "云昔",
    enabled: false,
    md_count: 0,
  };
  return { brief, brief2, lib, disabledLib };
});

vi.mock("./api", () => ({
  notesApi: {
    libs: vi.fn(),
    createLib: vi.fn(),
    syncLib: vi.fn(),
    search: vi.fn(),
    get: vi.fn(),
  },
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function resetMocks() {
  vi.mocked(notesApi.libs).mockResolvedValue([lib, disabledLib]);
  vi.mocked(notesApi.createLib).mockResolvedValue(lib);
  vi.mocked(notesApi.syncLib).mockResolvedValue({
    lib_key: "main",
    upserted: 1,
    removed: 0,
    md_count: 1,
  });
  vi.mocked(notesApi.search).mockResolvedValue({ items: [brief], total: 1 });
  vi.mocked(notesApi.get).mockResolvedValue({
    ...brief,
    content: "# 周报\n正文",
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  resetMocks();
});

describe("NotesApp", () => {
  /* ★ 2026-11-22（汐瑶 · 三态收口 9c73b4f）：error 与「真的没有索引」必须分开。
     此前 search.data?.items.length === 0 在 data 为 undefined 时既不 loading 也不 empty
     → 整块白板，一句话都没有。 */
  it("搜索接口 500 → 说「读取失败」并给重试，不再显示「还没有索引」", async () => {
    vi.mocked(notesApi.search).mockRejectedValue(
      new ApiError({ type: "about:blank", title: "boom", status: 500 }),
    );
    render(<NotesApp />, { wrapper: makeWrapper() });
    expect(await screen.findByText("笔记列表读取失败")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
    expect(screen.queryByText(/还没有索引/)).toBeNull();
  });

  it("搜索接口 404 → 说「未接线」而不是「坏」（notes phase=PENDING）", async () => {
    vi.mocked(notesApi.search).mockRejectedValue(
      new ApiError({ type: "about:blank", title: "not found", status: 404 }),
    );
    render(<NotesApp />, { wrapper: makeWrapper() });
    expect(await screen.findByText(/笔记层未接线/)).toBeTruthy();
    expect(screen.queryByText("笔记列表读取失败")).toBeNull();
  });

  it("挂载并渲染搜索结果（列表）", async () => {
    render(<NotesApp />, { wrapper: makeWrapper() });
    expect(screen.getByLabelText("搜索笔记")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("周报")).toBeTruthy();
    });
    expect(screen.getByText("进度摘要")).toBeTruthy();
  });

  it("点开列表项加载全文（详情 · V7 MdView 渲染）", async () => {
    render(<NotesApp />, { wrapper: makeWrapper() });
    await waitFor(() => screen.getByText("周报"));
    fireEvent.click(screen.getByText("周报"));
    await waitFor(() => {
      // V7 渲染栈：md 由 MdView 渲染为标题+段落（不再是 <pre> 裸文本）
      expect(screen.getByRole("heading", { level: 1, name: "周报" })).toBeTruthy();
    });
    expect(screen.getByText("正文")).toBeTruthy();
    expect(notesApi.get).toHaveBeenCalledWith("n1");
    expect(screen.getByText("a/one.md")).toBeTruthy();
  });

  it("搜索框输入触发查询（搜索）", async () => {
    render(<NotesApp />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByLabelText("搜索笔记"), {
      target: { value: "周报" },
    });
    await waitFor(() => {
      expect(notesApi.search).toHaveBeenCalledWith("周报");
    });
  });

  it("空态：无索引时提示同步", async () => {
    vi.mocked(notesApi.search).mockResolvedValue({ items: [], total: 0 });
    render(<NotesApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByText("还没有索引，点右上角「同步」从本机桥拉取")).toBeTruthy();
    });
  });

  it("无标题笔记回退显示 rel_path", async () => {
    vi.mocked(notesApi.search).mockResolvedValue({
      items: [brief2 as NoteBrief],
      total: 1,
    });
    render(<NotesApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByText("b/two.md")).toBeTruthy();
    });
  });

  it("选中项带 active 类", async () => {
    render(<NotesApp />, { wrapper: makeWrapper() });
    const btn = await screen.findByRole("button", { name: /周报/ });
    expect(btn.className).toBe("notes-item");
    fireEvent.click(btn);
    await waitFor(() => {
      expect(btn.className).toBe("notes-item notes-item--active");
    });
  });

  it("桥离线时详情提示全文暂不可用", async () => {
    vi.mocked(notesApi.get).mockResolvedValue({
      ...brief,
      content: "",
    });
    render(<NotesApp />, { wrapper: makeWrapper() });
    await waitFor(() => screen.getByText("周报"));
    fireEvent.click(screen.getByText("周报"));
    await waitFor(() => {
      expect(screen.getByText("（全文暂不可用——本机桥未连接）")).toBeTruthy();
    });
  });

  it("V7 验收样例：Boyle 定律表格 + KaTeX 公式渲染", async () => {
    vi.mocked(notesApi.get).mockResolvedValue({
      ...brief,
      content:
        "| 定律 | 控制条件 | 结论 | 关系式 |\n" +
        "|---|---|---|---|\n" +
        "| **Boyle 定律** | $n$、$T$ 一定 | $V$ 与 $p$ 成**反比** | $V \\propto \\dfrac{1}{p}$ |",
    });
    render(<NotesApp />, { wrapper: makeWrapper() });
    await waitFor(() => screen.getByText("周报"));
    fireEvent.click(screen.getByText("周报"));
    await waitFor(() => {
      expect(document.querySelector(".md-view table")).toBeTruthy(); // GFM 表格
    });
    expect(document.querySelectorAll(".md-view table th").length).toBe(4); // 四列表头
    expect(document.querySelector(".md-view .katex")).toBeTruthy(); // KaTeX 公式（含 \dfrac）
    expect(screen.getByText("Boyle 定律")).toBeTruthy();
  });

  it("同步按钮只同步 enabled 库", async () => {
    render(<NotesApp />, { wrapper: makeWrapper() });
    fireEvent.click(screen.getByRole("button", { name: "同步" }));
    await waitFor(() => {
      expect(notesApi.syncLib).toHaveBeenCalledWith("l1");
    });
    expect(notesApi.syncLib).toHaveBeenCalledTimes(1);
    expect(notesApi.syncLib).not.toHaveBeenCalledWith("l2");
  });
});
