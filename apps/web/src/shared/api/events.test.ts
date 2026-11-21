/**
 * 内核事件总线（SSE）回归 —— ★ B0（bidiu 2026-11-21）横切修复的守门测试。
 *
 * 背景：后端 SSE 帧是**具名事件**（`core/events.py::_format_sse` 每条都写
 * `event: <topic>`）。旧实现用 `es.onmessage` 收帧——EventSource 语义里具名帧
 * 根本不会触发 onmessage ⇒ 28 个订阅点 / 11 个 app 的实时刷新从未生效过。
 * 本文件锁住修复后的行为：
 *   1. 具名帧按 topic 投递（addEventListener 路径）
 *   2. 重连（新 EventSource 对象）后监听器全量重挂
 *   3. 退订摘监听
 *   4. 匿名帧按信封 topic 兜底派发
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  api: { post: vi.fn() },
  ApiError: class ApiError extends Error {
    status?: number;
    constructor(message: string, status?: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from "./client";
import { eventBus, type Handler } from "./events";

/** 最小 EventSource 替身：按具名事件分发，暴露 fire 帮助方法。 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  named = new Map<string, Set<EventListener>>();
  onmessage: EventListener | null = null;
  onerror: EventListener | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, l: EventListener): void {
    let set = this.named.get(type);
    if (!set) {
      set = new Set();
      this.named.set(type, set);
    }
    set.add(l);
  }
  removeEventListener(type: string, l: EventListener): void {
    this.named.get(type)?.delete(l);
  }
  close(): void {
    this.closed = true;
  }
  /** 模拟后端发一条具名帧：`event: <topic>` + `data: <json>`。 */
  fireNamed(topic: string, data: string): void {
    for (const l of [...(this.named.get(topic) ?? [])]) {
      l({ data } as unknown as MessageEvent);
    }
  }
  /** 模拟一条匿名帧（无 `event:` 行）→ 走 onmessage。 */
  fireAnonymous(data: string): void {
    this.onmessage?.({ data } as unknown as MessageEvent);
  }
}

const apiPost = api.post as ReturnType<typeof vi.fn>;

const unsubs: Array<() => void> = [];

beforeEach(() => {
  vi.mocked(apiPost).mockReset().mockResolvedValue({ ticket: "t" });
  FakeEventSource.instances = [];
  // ★ jsdom 没有原生 EventSource——不 stub 的话 connect() 会在
  // `typeof EventSource === "undefined"` 处直接短路，一条用例都跑不到。
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  for (const u of unsubs.splice(0)) u();
  vi.useRealTimers();
});

/** 等 connect() 的 async 链完成（fetchTicket → new EventSource）。 */
async function awaitConnected(expectCount?: number): Promise<FakeEventSource> {
  await vi.waitFor(() => {
    if (expectCount !== undefined) {
      expect(FakeEventSource.instances.length).toBe(expectCount);
    } else {
      expect(FakeEventSource.instances.length).toBeGreaterThan(0);
    }
  });
  return FakeEventSource.instances[expectCount ? expectCount - 1 : 0];
}

describe("EventBus（B0 具名帧修复）", () => {
  it("具名帧按 topic 投递给订阅者（旧 onmessage 实现下这条永远不成立）", async () => {
    const cb: Handler = vi.fn();
    unsubs.push(eventBus.subscribe("todo.item.due", cb));
    const es = await awaitConnected();
    expect(es.named.size).toBe(1); // 监听器挂在具名事件上，不是 onmessage 上

    es.fireNamed("todo.item.due", JSON.stringify({ topic: "todo.item.due", payload: { id: 1 } }));
    expect(cb).toHaveBeenCalledTimes(1);
    expect(cb).toHaveBeenCalledWith({ id: 1 });
  });

  it("重连后（新 EventSource 对象）监听器全量重挂", async () => {
    const cb: Handler = vi.fn();
    unsubs.push(eventBus.subscribe("todo.item.due", cb));
    await awaitConnected(1);

    // 断线 → 旧对象 close + 500ms 退避重连（票据换新，fetchTicket 已 mock）
    // 用真实计时器等重连落地（退避 500ms，waitFor 默认 1s 超时内足够）。
    FakeEventSource.instances[0].onerror?.({} as Event);
    await vi.waitFor(
      () => expect(FakeEventSource.instances.length).toBe(2),
      { timeout: 3000 },
    );

    // 新实例上具名监听还在（这是重连后「总线死掉」的回归防线）
    const es2 = FakeEventSource.instances[1];
    expect(es2.named.has("todo.item.due")).toBe(true);
    es2.fireNamed("todo.item.due", JSON.stringify({ topic: "todo.item.due", payload: "ok" }));
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("最后一个订阅者退订 → 该 topic 的具名监听被摘掉", async () => {
    const cb: Handler = vi.fn();
    const unsub = eventBus.subscribe("notes.note.created", cb);
    const es = await awaitConnected();
    expect(es.named.has("notes.note.created")).toBe(true);

    unsub();
    // 监听器被摘掉（Map 里留着空 Set 也算摘掉——关键是没有活的 listener）
    expect(es.named.get("notes.note.created")?.size ?? 0).toBe(0);
    es.fireNamed("notes.note.created", "{}");
    expect(cb).not.toHaveBeenCalled();
  });

  it("匿名帧（未来形态）按信封里的 topic 兜底派发", async () => {
    const cb: Handler = vi.fn();
    unsubs.push(eventBus.subscribe("push.sent", cb));
    const es = await awaitConnected();

    es.fireAnonymous(JSON.stringify({ topic: "push.sent", payload: { p: 2 } }));
    expect(cb).toHaveBeenCalledWith({ p: 2 });

    // 信封字段是历史名 type 的，也能兜住
    const cb2: Handler = vi.fn();
    unsubs.push(eventBus.subscribe("export.done", cb2));
    es.fireAnonymous(JSON.stringify({ type: "export.done", payload: { p: 3 } }));
    expect(cb2).toHaveBeenCalledWith({ p: 3 });
  });

  it("非法 JSON 不炸总线（后续帧照常投递）", async () => {
    const cb: Handler = vi.fn();
    unsubs.push(eventBus.subscribe("todo.item.due", cb));
    const es = await awaitConnected();

    es.fireNamed("todo.item.due", "这不是 JSON");
    expect(cb).not.toHaveBeenCalled();
    es.fireNamed("todo.item.due", JSON.stringify({ topic: "todo.item.due", payload: "live" }));
    expect(cb).toHaveBeenCalledWith("live");
  });
});
