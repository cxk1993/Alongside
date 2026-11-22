import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/shared/api/client", () => ({
  api: { post: vi.fn(() => Promise.resolve({ ticket: "tk-1", expires_in: 60 })) },
}));

import { api } from "@/shared/api/client";
import type { ModuleReg } from "../../kernel/store";
import { useDesktopStore } from "../../kernel/store";
import type { ModuleManifest } from "../../kernel/types";
import { ActivityStrip } from "./ActivityStrip";
import { mergeEvents, parseEvent, summarizePayload, useEventStream } from "./useEventStream";
import type { ActivityEvent } from "./useEventStream";

/**
 * P0-c · AgentStream（事件流）接线测试。
 *
 * 纯函数部分锁「不编数据」：缺 topic / 缺 id 的帧一律丢弃，摘要只认四个字段。
 * 接线部分锁协议：先换票再连、topic 全部进 query 且逐个挂命名监听、
 * 断线重连要**重新换票**、卸载要关连接（否则切页面后还在跑）。
 */

class FakeES {
  static instances: FakeES[] = [];
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private handlers = new Map<string, Array<(ev: MessageEvent) => void>>();
  constructor(url: string) {
    this.url = url;
    FakeES.instances.push(this);
  }
  addEventListener(name: string, fn: (ev: MessageEvent) => void) {
    const list = this.handlers.get(name) ?? [];
    list.push(fn);
    this.handlers.set(name, list);
  }
  close() {
    this.closed = true;
  }
  /** 服务端推一帧：事件名 = topic。 */
  emit(name: string, data: unknown) {
    for (const fn of this.handlers.get(name) ?? []) {
      fn({ data: JSON.stringify(data) } as MessageEvent);
    }
  }
  names() {
    return [...this.handlers.keys()];
  }
}

const post = api.post as unknown as ReturnType<typeof vi.fn>;
const ORIGINAL = globalThis.EventSource;

function Probe({ topics, retryMs }: { topics: string[]; retryMs?: number }) {
  const { events, status } = useEventStream(topics, 20, retryMs);
  return (
    <div>
      <span data-testid="status">{status}</span>
      <span data-testid="n">{events.length}</span>
      <span data-testid="first">{events[0]?.summary ?? ""}</span>
    </div>
  );
}

function mod(id: string, name: string, emits: string[] = []): ModuleReg {
  const manifest: ModuleManifest = {
    id,
    name,
    kind: "builtin",
    entry: `@/apps/${id}`,
    icon: "grid",
    window: { w: 640, h: 420 },
    emits,
  } as ModuleManifest;
  return { manifest, enabled: true };
}

beforeEach(() => {
  FakeES.instances = [];
  vi.clearAllMocks();
  post.mockImplementation(() => Promise.resolve({ ticket: "tk-1", expires_in: 60 }));
  (globalThis as unknown as { EventSource: unknown }).EventSource = FakeES;
});

afterEach(() => {
  (globalThis as unknown as { EventSource: unknown }).EventSource = ORIGINAL;
  useDesktopStore.setState({ modules: {} });
});

describe("P0-c · 事件帧解析（不编数据）", () => {
  it("摘要按 title → summary → name → label 顺序取第一个非空字符串", () => {
    expect(summarizePayload({ title: "删了待办", summary: "x" })).toBe("删了待办");
    expect(summarizePayload({ summary: "  ", name: "日程" })).toBe("日程");
    expect(summarizePayload({ label: "done" })).toBe("done");
    expect(summarizePayload({ something: "else" })).toBeNull();
    expect(summarizePayload("字符串不是对象")).toBeNull();
  });

  it("缺 topic 或缺 id 的帧丢弃（不补假值）", () => {
    expect(parseEvent({ id: 1 })).toBeNull();
    expect(parseEvent({ topic: "todo.item.done" })).toBeNull();
    expect(parseEvent({ id: 3, topic: "todo.item.done", at: "2026-11-22T02:00:00Z" })).toMatchObject({
      id: 3,
      topic: "todo.item.done",
      summary: null,
    });
  });

  it("合并：按 eid 去重、按 id 倒序、截断到 max", () => {
    const mk = (id: number, eid = `e${id}`): ActivityEvent => ({
      id,
      eid,
      topic: "t",
      at: "",
      source: "",
      summary: null,
    });
    let list: ActivityEvent[] = [];
    for (const id of [2, 1, 3]) list = mergeEvents(list, mk(id), 2);
    expect(list.map((e) => e.id)).toEqual([3, 2]);
    expect(mergeEvents(list, mk(3), 5)).toHaveLength(2);
  });
});

describe("P0-c · useEventStream 接线", () => {
  it("先换票再连，topics 进 query，逐个挂命名监听", async () => {
    render(<Probe topics={["todo.item.done", "calendar.event.created"]} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    const es = FakeES.instances[0];
    expect(es.url).toContain("/api/v1/events/subscribe?");
    expect(es.url).toContain("ticket=tk-1");
    expect(es.url).toContain("todo.item.done");
    expect(es.url).toContain("calendar.event.created");
    expect(es.names()).toEqual(
      expect.arrayContaining(["todo.item.done", "calendar.event.created", "message"]),
    );
  });

  it("收到帧就入列表；状态从 connecting 变 live", async () => {
    render(<Probe topics={["todo.item.done"]} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    const es = FakeES.instances[0];
    act(() => {
      es.onopen?.();
      es.emit("todo.item.done", {
        id: 7,
        eid: "e7",
        topic: "todo.item.done",
        at: "2026-11-22T02:00:00Z",
        source: "todo",
        payload: { title: "勾掉了「写周报」" },
      });
    });
    expect(screen.getByTestId("status").textContent).toBe("live");
    expect(screen.getByTestId("n").textContent).toBe("1");
    expect(screen.getByTestId("first").textContent).toBe("勾掉了「写周报」");
  });

  it("坏帧（非 JSON / 缺字段）不产生条目", async () => {
    render(<Probe topics={["todo.item.done"]} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    const es = FakeES.instances[0];
    act(() => {
      es.emit("todo.item.done", { topic: "todo.item.done" });
    });
    expect(screen.getByTestId("n").textContent).toBe("0");
  });

  it("断线后重新换票重连（不复用旧票）", async () => {
    render(<Probe topics={["todo.item.done"]} retryMs={10} />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    act(() => {
      FakeES.instances[0].onerror?.();
    });
    await waitFor(() => expect(post.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.getByTestId("status").textContent).toBe("connecting");
  });

  it("没有 EventSource 时不订阅、如实报 offline", async () => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = undefined;
    render(<Probe topics={["todo.item.done"]} />);
    expect(screen.getByTestId("status").textContent).toBe("offline");
    expect(post).not.toHaveBeenCalled();
  });
});

describe("P0-c · ActivityStrip", () => {
  it("模块清单给出 topic 时，订阅并显示事件", async () => {
    useDesktopStore.setState({ modules: { todo: mod("todo", "待办", ["todo.item.done"]) } });
    render(<ActivityStrip />);
    await waitFor(() => expect(FakeES.instances).toHaveLength(1));
    expect(FakeES.instances[0].url).toContain("todo.item.done");
    act(() => {
      FakeES.instances[0].emit("todo.item.done", {
        id: 9,
        eid: "e9",
        topic: "todo.item.done",
        at: "2026-11-22T03:15:00Z",
        source: "todo",
        payload: { title: "新建了「买菜」" },
      });
    });
    expect(screen.getByTestId("activity-row")).toBeTruthy();
    expect(screen.getAllByText("新建了「买菜」").length).toBeGreaterThan(0);
  });

  it("没有 EventSource 时写「实时未连接」，不写「暂无活动」", () => {
    (globalThis as unknown as { EventSource: unknown }).EventSource = undefined;
    useDesktopStore.setState({ modules: { todo: mod("todo", "待办", ["todo.item.done"]) } });
    render(<ActivityStrip />);
    expect(screen.getByText("实时未连接")).toBeTruthy();
    expect(screen.getByText(/事件流不可用/)).toBeTruthy();
  });
});