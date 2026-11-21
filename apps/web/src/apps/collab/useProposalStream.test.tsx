import { act, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/shared/api/client", () => ({
  api: { post: vi.fn(() => Promise.resolve({ ticket: "tk-1", expires_in: 60 })) },
}));

import { api } from "@/shared/api/client";
import { useProposalStream } from "./useProposalStream";

/**
 * T21 · SSE 推送接线测试。
 *
 * jsdom 没有 EventSource，所以造一个只记录「被怎么用的」替身 —— 测的是**协议**：
 *   ① 先 POST 换票，再拿票连 SSE（凭证不进 URL 的方式）；
 *   ② topics 必须带 proposals.*；
 *   ③ 四条事件名都挂了同一个 handler（少挂一条 = 少一种状态不刷新）；
 *   ④ 断线会重新换票重连（旧票可能过期，不能复用）；
 *   ⑤ 卸载时关连接、不留定时器（否则切页面后还在跑）。
 */

class FakeES {
  static instances: FakeES[] = [];
  url: string;
  closed = false;
  onerror: (() => void) | null = null;
  private handlers = new Map<string, Array<() => void>>();
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  addEventListener(name: string, fn: () => void) {
    const list = this.handlers.get(name) ?? [];
    list.push(fn);
    this.handlers.set(name, list);
  }
  close() {
    this.closed = true;
  }
  /** 模拟服务端推一帧。 */
  emit(name: string) {
    for (const fn of this.handlers.get(name) ?? []) fn();
  }
  names() {
    return [...this.handlers.keys()];
  }
}

const post = api.post as unknown as ReturnType<typeof vi.fn>;

function Probe({ onEvent, retryMs }: { onEvent: () => void; retryMs?: number }) {
  useProposalStream(onEvent, retryMs);
  return null;
}

const ORIGINAL = globalThis.EventSource;

beforeEach(() => {
  FakeES.instances = [];
  vi.clearAllMocks();
  post.mockImplementation(() => Promise.resolve({ ticket: "tk-1", expires_in: 60 }));
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeES;
});

afterEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = ORIGINAL;
});

describe("T21 · useProposalStream", () => {
  it("先换票再连：URL 带 topics=proposals.* 与 ticket", async () => {
    render(<Probe onEvent={() => {}} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    expect(post).toHaveBeenCalledWith("/api/v1/events/ticket", {});
    const url = FakeES.instances[0].url;
    expect(url).toContain("/api/v1/events/subscribe");
    expect(url).toContain("topics=proposals.*");
    expect(url).toContain("ticket=tk-1");
  });

  it("四条提案事件都挂上 handler，任一帧都触发刷新", async () => {
    const onEvent = vi.fn();
    render(<Probe onEvent={onEvent} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    const es = FakeES.instances[0];
    expect(es.names().sort()).toEqual([
      "proposals.proposal.approved",
      "proposals.proposal.created",
      "proposals.proposal.rejected",
      "proposals.proposal.undone",
    ]);
    act(() => es.emit("proposals.proposal.created"));
    act(() => es.emit("proposals.proposal.undone"));
    expect(onEvent).toHaveBeenCalledTimes(2);
  });

  it("断线后重新换票重连（不复用旧票）", async () => {
    render(<Probe onEvent={() => {}} retryMs={10} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    const first = FakeES.instances[0];
    post.mockImplementation(() => Promise.resolve({ ticket: "tk-2", expires_in: 60 }));
    act(() => first.onerror?.());
    await waitFor(() => expect(FakeES.instances).toHaveLength(2));
    expect(first.closed).toBe(true);
    expect(FakeES.instances[1].url).toContain("ticket=tk-2");
  });

  it("换票失败 → 不抛错、不连 SSE，安静等退避（列表靠挂载时那次拉取）", async () => {
    post.mockImplementation(() => Promise.reject(new Error("401")));
    render(<Probe onEvent={() => {}} retryMs={10} />);
    // 换票至少试过一次；失败后**不建 EventSource**（没有票绝不裸连），也不冒泡异常。
    // 注：失败重试的下限是 10s（避免 401 时把换票端点打穿），所以这里不验第二次调用。
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(FakeES.instances).toHaveLength(0);
  });

  it("卸载 → 关连接", async () => {
    const { unmount } = render(<Probe onEvent={() => {}} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    unmount();
    expect(FakeES.instances[0].closed).toBe(true);
  });

  it("环境无 EventSource → 静默不订阅（不假装有实时能力）", () => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = undefined;
    render(<Probe onEvent={() => {}} />);
    expect(post).not.toHaveBeenCalled();
    expect(FakeES.instances).toHaveLength(0);
  });
});