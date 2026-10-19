/**
 * ★ Pi 智能体对话窗 UI 测试。
 *
 * 覆盖：初始态 / 流式逐字 / 降级提示 / 会话锁定 / 空输入禁用 / 停止。
 * ★ 流式走 `fetch` + `ReadableStream`（原生 EventSource 不能带 header），
 *   故此处 mock 的是 `global.fetch` 而不是 api 客户端。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api } from "@/shared/api/client";
// ★ 2026-09-26 重构：对话逻辑已迁至 PiChatView（PiAgentApp 现在是两页容器）。
//   本文件测的是**对话页**，故改测 PiChatView；容器另有 PiAgentApp 的 tab 测试。
import { PiChatView } from "./index";

vi.mock("@/shared/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/shared/api/client")>("@/shared/api/client");
  return {
    ...actual,
    api: { get: vi.fn(), post: vi.fn() },
  };
});

/** 造一个 SSE 响应体（按 chunk 分块，模拟逐字到达）。 */
function sseResponse(events: Array<{ event: string; data: unknown }>): Response {
  const text = events
    .map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`)
    .join("");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

const STATUS_L1 = { level: "L1", level_text: "AI 工具能力可用", model: "alongside:high", pool: {} };
const STATUS_L3 = { level: "L3", level_text: "AI 工具能力暂不可用（pi 熔断）", pool: {} };

describe("★ PiAgentApp", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.endsWith("/status")) return STATUS_L1 as never;
      if (path.endsWith("/sessions")) return { sessions: [], pool: {} } as never;
      return {} as never;
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("P1 · 初始有欢迎语 + 输入/发送就绪（空输入禁用）", async () => {
    render(<PiChatView />);
    expect(screen.getByText(/我是 Pi 智能体/)).toBeTruthy();
    expect(screen.getByTestId("pi-input")).toBeTruthy();
    expect((screen.getByTestId("pi-send") as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(screen.getByTestId("pi-level")).toBeTruthy());
  });

  it("P2 · 发送 → 流式逐字追加 + 请求契约正确", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        { event: "delta", data: { text: "今" } },
        { event: "delta", data: { text: "天" } },
        { event: "delta", data: { text: "好" } },
        { event: "settled", data: {} },
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<PiChatView />);
    fireEvent.change(screen.getByTestId("pi-input"), { target: { value: "打个招呼" } });
    fireEvent.click(screen.getByTestId("pi-send"));

    await waitFor(() => expect(screen.getByText("今天好")).toBeTruthy());
    // 契约：POST /chat/stream，body 带 session_id
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/pi-agent/chat/stream");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      message: "打个招呼",
      session_id: "default",
    });
  });

  it("P3 · 流式报错 → 显示降级提示（含层级）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([{ event: "error", data: { detail: "pi 处于熔断态", level: "L3" } }]),
      ),
    );
    // 流式一个字都没来 → 会退回同步接口；让它也返回降级
    vi.mocked(api.post).mockResolvedValue({
      session_id: "default",
      reply: "",
      level: "L3",
      settled: false,
      degraded: true,
      detail: "pi 处于熔断态（L3）",
    } as never);

    render(<PiChatView />);
    fireEvent.change(screen.getByTestId("pi-input"), { target: { value: "你好" } });
    fireEvent.click(screen.getByTestId("pi-send"));

    await waitFor(() => expect(screen.getByText(/降级（L3）/)).toBeTruthy());
  });

  it("P4 · L3 状态显示「已熔断」并出现「重试」按钮", async () => {
    vi.mocked(api.get).mockImplementation(async (path: string) => {
      if (path.endsWith("/status")) return STATUS_L3 as never;
      return { sessions: [], pool: {} } as never;
    });
    render(<PiChatView />);
    await waitFor(() => expect(screen.getByText("已熔断")).toBeTruthy());
    expect(screen.getByText("重试")).toBeTruthy();
  });

  it("P5 · 锁定会话 → 调 sessionAction(lock)", async () => {
    vi.mocked(api.post).mockResolvedValue({ locked: true } as never);
    render(<PiChatView />);
    fireEvent.change(screen.getByLabelText("会话名"), { target: { value: "alpha" } });
    fireEvent.click(screen.getByTestId("pi-lock"));
    await waitFor(() =>
      expect(vi.mocked(api.post)).toHaveBeenCalledWith("/api/v1/pi-agent/sessions", {
        action: "lock",
        session: "alpha",
      }),
    );
  });
});
