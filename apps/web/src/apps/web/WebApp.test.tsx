/**
 * 网页工作台前端测试。
 * 网络层全部 mock，不打真实后端。
 *
 * 覆盖：
 *  - WebApp：空态引导 / 左栏只列启用项 / 内嵌 iframe 的 src / 切到管理页
 *  - WebEntriesPanel：能力条目预览 JSON（★ 输入端产出可见）
 *  - WebFrame：★ 加载超时 → 兜底提示 + 新窗口按钮（绝不白屏到底）
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import WebApp from "./WebApp";
import WebFrame, { FRAME_TIMEOUT_MS } from "./WebFrame";
import WebEntriesPanel from "./WebEntriesPanel";
import { makeWorkspace, useDesktopStore } from "@/kernel/store";
import { WindowInstanceContext } from "@/kernel/windowInstance";
import type { CapabilityEntry, WebEntry } from "./api";

vi.mock("@/shared/api/events", () => ({ usePluginEvent: () => {} }));

const listMock = vi.fn();
const frameUrlMock = vi.fn();
vi.mock("./api", async (orig) => {
  const actual = await orig<typeof import("./api")>();
  return {
    ...actual,
    webApi: {
      list: (...a: unknown[]) => listMock(...a),
      create: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      remove: vi.fn().mockResolvedValue(undefined),
      touch: vi.fn().mockResolvedValue({ id: "e1", opened_at: "now" }),
      frameUrl: (...a: unknown[]) => frameUrlMock(...a),
    },
  };
});

function cap(id: string, name: string): CapabilityEntry {
  return {
    id,
    name,
    kind: "web",
    url: `https://${id}.invalid/`,
    endpoint: null,
    auth_ref: null,
    capabilities: [],
    enabled: true,
    note: null,
    source: "web_entry",
  };
}

function entry(over: Partial<WebEntry> = {}): WebEntry {
  const slug = over.slug ?? "alpha";
  const title = over.title ?? "甲站";
  return {
    id: `id-${slug}`,
    slug,
    title,
    url: `https://${slug}.invalid/`,
    icon: null,
    order: 0,
    enabled: true,
    kind: "web",
    endpoint: null,
    auth_ref: null,
    capabilities: [],
    note: null,
    created_at: "2026-09-19T00:00:00+00:00",
    updated_at: "2026-09-19T00:00:00+00:00",
    capability: cap(slug, title),
    ...over,
  };
}

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("WebFrame", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("iframe 的 src 取自 entry.url（不硬编码任何站点）", () => {
    render(<WebFrame entry={entry({ slug: "zzz", url: "https://zzz.invalid/portal" })} />);
    const frame = document.querySelector("iframe");
    expect(frame).not.toBeNull();
    expect(frame?.getAttribute("src")).toBe("https://zzz.invalid/portal");
  });

  it("★ 加载超时 → 出现兜底提示与新窗口按钮（绝不困住用户）", async () => {
    render(<WebFrame entry={entry()} />);
    expect(screen.getByText("正在加载…")).toBeTruthy();

    await act(async () => {
      vi.advanceTimersByTime(FRAME_TIMEOUT_MS + 50);
    });

    expect(screen.getByText("没能内嵌这个网页")).toBeTruthy();
    // 工具条与兜底层各有一个"在新窗口打开"
    expect(screen.getAllByText(/在新窗口打开/).length).toBeGreaterThanOrEqual(2);
  });

  it("★ T22 点亮后：不在窗口内时两个按钮安全降级为 disabled（给人话原因，不报错）", () => {
    render(<WebFrame entry={entry()} />);
    // 本用例没包 WindowInstanceContext.Provider → instanceId 为 null → 安全降级
    // ★ 本项目未启用 jest-dom 匹配器，用原生断言
    const pin = screen.getByRole("button", { name: /置顶/ });
    const fix = screen.getByRole("button", { name: /固定几何/ });
    expect((pin as HTMLButtonElement).disabled).toBe(true);
    expect((fix as HTMLButtonElement).disabled).toBe(true);
    expect(pin.getAttribute("title")).toContain("不在桌面窗口中");
    // 退路始终存在（工具条 1 个 + 底部兜底 1 个）
    expect(screen.getAllByRole("button", { name: /在新窗口打开/ }).length).toBeGreaterThanOrEqual(
      2,
    );
  });

  it("★ T22：在窗口上下文内，点「置顶」/「固定几何」真的写进桌面 store", () => {
    const snapshot = useDesktopStore.getState().windows;
    useDesktopStore.setState({
      windows: [
        {
          instanceId: "web#1",
          moduleId: "web",
          z: 11,
          minimized: false,
          maximized: false,
          geo: { x: 0, y: 48, w: 800, h: 600 },
          pinned: false,
          fixedGeometry: false,
          workspaceId: "ws1",
        },
      ],
      workspaces: [makeWorkspace(1, { topZ: 11 })],
      activeWorkspaceId: "ws1",
    });
    try {
      render(
        <WindowInstanceContext.Provider value="web#1">
          <WebFrame entry={entry()} />
        </WindowInstanceContext.Provider>,
      );
      const pin = screen.getByRole("button", { name: /置顶/ });
      expect((pin as HTMLButtonElement).disabled).toBe(false);

      fireEvent.click(pin);
      expect(useDesktopStore.getState().windows[0].pinned).toBe(true);
      expect(useDesktopStore.getState().windows[0].pinZ).toBe(1);

      fireEvent.click(screen.getByRole("button", { name: /固定几何/ }));
      expect(useDesktopStore.getState().windows[0].fixedGeometry).toBe(true);
    } finally {
      useDesktopStore.setState({ windows: snapshot });
    }
  });

  // ── ★ 2026-10-10 新增：auth_ref 凭据注入链路（防回退）──────────────
  it("★ 无 auth_ref 的条目：不调 frame-url，iframe 直接用 entry.url（老行为零变化）", async () => {
    const frameMock = frameUrlMock;
    frameMock.mockClear();
    render(<WebFrame entry={entry({ slug: "plain", url: "https://plain.invalid/x" })} />);
    await act(async () => {});
    expect(frameMock).not.toHaveBeenCalled();
    const frame = document.querySelector("iframe");
    expect(frame?.getAttribute("src")).toBe("https://plain.invalid/x");
  });

  it("★ 带 auth_ref 的条目：调 frame-url 拿注入凭据的地址，且 URL 里的 token 不进界面", async () => {
    const frameMock = frameUrlMock;
    frameMock.mockClear();
    frameMock.mockResolvedValue({
      id: "id-secret",
      slug: "secret",
      title: "带凭据",
      url: "https://secret.invalid/pi/?token=SUPERSECRETVALUE123456",
      auth_ref: "token:env:PI_WEB_TOKEN",
      parsed: true,
    });
    render(
      <WebFrame
        entry={entry({
          slug: "secret",
          url: "https://secret.invalid/pi/",
          auth_ref: "token:env:PI_WEB_TOKEN",
        })}
      />,
    );
    await act(async () => {});
    expect(frameMock).toHaveBeenCalledWith("id-secret");
    const frame = document.querySelector("iframe");
    expect(frame?.getAttribute("src")).toContain("token=SUPERSECRETVALUE123456");
    // ★ 凭据只进 iframe，不进工具栏文本
    expect(document.body.textContent ?? "").not.toContain("SUPERSECRETVALUE123456");
  });

  it("★ frame-url 解析失败（.env 缺变量）→ 明确提示，不静默白屏", async () => {
    const frameMock = frameUrlMock;
    frameMock.mockClear();
    frameMock.mockRejectedValue(new Error("凭据解析失败：环境变量 PI_WEB_TOKEN 未设置。"));
    render(
      <WebFrame
        entry={entry({
          slug: "broken",
          url: "https://broken.invalid/",
          auth_ref: "token:env:PI_WEB_TOKEN",
        })}
      />,
    );
    await act(async () => {});
    expect(screen.getByText("凭据没能解析")).toBeTruthy();
    expect(screen.getByText(/PI_WEB_TOKEN 未设置/)).toBeTruthy();
  });
});

describe("WebApp", () => {
  it("没有启用入口时给出空态引导", async () => {
    listMock.mockResolvedValue({ items: [entry({ enabled: false })], total: 1 });
    render(<WebApp />, { wrapper: wrapper() });
    await waitFor(() => expect(screen.getByText("没有可显示的网页")).toBeTruthy());
    expect(screen.getByText("＋ 管理入口")).toBeTruthy();
  });

  it("左栏只列启用项，并内嵌第一个可用项", async () => {
    listMock.mockResolvedValue({
      items: [
        entry({ slug: "alpha", title: "甲站" }),
        entry({ slug: "beta", title: "乙站", enabled: false }),
      ],
      total: 2,
    });
    render(<WebApp />, { wrapper: wrapper() });

    await waitFor(() => expect(screen.getByText("甲站")).toBeTruthy());
    // 禁用的"乙站"不应出现在可浏览列表里
    expect(screen.queryByRole("button", { name: /乙站/ })).toBeNull();
    await waitFor(() =>
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe("https://alpha.invalid/"),
    );
  });

  it("点条目切换内嵌目标", async () => {
    listMock.mockResolvedValue({
      items: [
        entry({ slug: "alpha", title: "甲站" }),
        entry({ slug: "beta", title: "乙站", order: 1 }),
      ],
      total: 2,
    });
    render(<WebApp />, { wrapper: wrapper() });
    await waitFor(() => expect(screen.getByText("乙站")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /乙站/ }));
    await waitFor(() =>
      expect(document.querySelector("iframe")?.getAttribute("src")).toBe("https://beta.invalid/"),
    );
  });

  it("加载失败时显示重试而不是空转", async () => {
    listMock.mockRejectedValue(new Error("boom"));
    render(<WebApp />, { wrapper: wrapper() });
    await waitFor(() => expect(screen.getByText("加载入口列表失败")).toBeTruthy());
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });
});

describe("WebEntriesPanel", () => {
  it("列全部条目（含禁用），并显示启用开关", () => {
    render(
      <WebEntriesPanel
        entries={[entry({ title: "甲站" }), entry({ slug: "b", title: "乙站", enabled: false })]}
      />,
      {
        wrapper: wrapper(),
      },
    );
    expect(screen.getByText("甲站")).toBeTruthy();
    // 禁用项也要出现在管理页
    expect(screen.getByText("乙站")).toBeTruthy();
    expect(screen.getAllByRole("checkbox").length).toBe(2);
  });

  it("★ 新建表单当场显示它会以什么形状进能力目录（source=web_entry）", () => {
    render(<WebEntriesPanel entries={[]} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByRole("button", { name: /新建入口/ }));

    expect(screen.getByText("它将这样进入能力目录")).toBeTruthy();
    const pre = document.querySelector(".web-form__preview-json");
    expect(pre?.textContent).toContain('"source": "web_entry"');

    // 填 slug/名称 → 预览同步变化（输入端 → 输出端当场可见）
    fireEvent.change(screen.getByPlaceholderText(/my-portal/), { target: { value: "portal-x" } });
    fireEvent.change(screen.getByPlaceholderText(/某个内部工具/), { target: { value: "门户 X" } });
    expect(pre?.textContent).toContain('"id": "portal-x"');
    expect(pre?.textContent).toContain('"name": "门户 X"');
  });

  it("auth_ref 输入框提示只写引用（不写明文）", () => {
    render(<WebEntriesPanel entries={[]} />, { wrapper: wrapper() });
    fireEvent.click(screen.getByRole("button", { name: /新建入口/ }));
    expect(screen.getByPlaceholderText(/pat:env:YOUR_TOKEN_VAR/)).toBeTruthy();
    expect(screen.getByText(/只写「方式:env:变量名」/)).toBeTruthy();
  });
});
