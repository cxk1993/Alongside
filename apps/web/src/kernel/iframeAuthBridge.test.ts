/**
 * iframeAuthBridge —— B2 跨源登录态握手桥单测（2026-09-20）。
 *
 * 安全断言为核心：白名单空 = 拒绝一切（fail closed）；origin 不匹配不回信；
 * 非本协议消息忽略；未登录（无 token）不回；回信 targetOrigin 锁定来源源。
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

import { handleMessage, installIframeAuthBridge } from "./iframeAuthBridge";

function makeEvent(opts: {
  origin: string;
  data?: unknown;
  source?: MessageEvent["source"];
}): MessageEvent {
  // jsdom 的 MessageEvent 构造器不支持 source 字段，手动赋值
  const ev = new MessageEvent("message", { origin: opts.origin, data: opts.data });
  if (opts.source) {
    Object.defineProperty(ev, "source", { value: opts.source });
  }
  return ev;
}

function makeSource(): NonNullable<MessageEvent["source"]> {
  return { postMessage: vi.fn() } as never;
}

const TOKEN_READ = () => "jwt-token-abc";

describe("iframeAuthBridge · handleMessage 协议与安全", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("白名单命中 + 协议消息 → 回 token，targetOrigin 锁定来源源", () => {
    const source = makeSource();
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "alongside:request-token" },
      source,
    });
    const handled = handleMessage(ev, ["http://localhost:18091"], TOKEN_READ);
    expect(handled).toBe(true);
    expect(source.postMessage).toHaveBeenCalledWith(
      { type: "alongside:token", token: "jwt-token-abc" },
      { targetOrigin: "http://localhost:18091" },
    );
  });

  it("白名单为空（默认）→ 拒绝一切跨源请求（fail closed）", () => {
    const source = makeSource();
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "alongside:request-token" },
      source,
    });
    expect(handleMessage(ev, [], TOKEN_READ)).toBe(false);
    expect(source.postMessage).not.toHaveBeenCalled();
  });

  it("origin 不在白名单 → 不回信", () => {
    const source = makeSource();
    const ev = makeEvent({
      origin: "http://evil.example.com",
      data: { type: "alongside:request-token" },
      source,
    });
    expect(handleMessage(ev, ["http://localhost:18091"], TOKEN_READ)).toBe(false);
    expect(source.postMessage).not.toHaveBeenCalled();
  });

  it("非本协议消息（含来自白名单源）→ 忽略", () => {
    const source = makeSource();
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "something-else" },
      source,
    });
    expect(handleMessage(ev, ["http://localhost:18091"], TOKEN_READ)).toBe(false);
    expect(source.postMessage).not.toHaveBeenCalled();
    // data 为 null / 非对象同样忽略
    expect(handleMessage(makeEvent({ origin: "http://localhost:18091" }), ["http://localhost:18091"], TOKEN_READ)).toBe(false);
  });

  it("无 token（未登录）→ 不回信", () => {
    const source = makeSource();
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "alongside:request-token" },
      source,
    });
    expect(handleMessage(ev, ["http://localhost:18091"], () => null)).toBe(false);
    expect(source.postMessage).not.toHaveBeenCalled();
  });

  it("无 source（同窗 message）→ 忽略", () => {
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "alongside:request-token" },
    });
    expect(handleMessage(ev, ["http://localhost:18091"], TOKEN_READ)).toBe(false);
  });
});

describe("iframeAuthBridge · installIframeAuthBridge 挂载/卸载", () => {
  it("监听挂载后收到合法握手即回信；dispose 后不再响应", () => {
    const listeners: EventListener[] = [];
    const fakeTarget = {
      addEventListener: vi.fn((type: string, fn: EventListener) => {
        if (type === "message") listeners.push(fn);
      }),
      removeEventListener: vi.fn((_type: string, fn: EventListener) => {
        const i = listeners.indexOf(fn);
        if (i >= 0) listeners.splice(i, 1);
      }),
    } as unknown as Pick<Window, "addEventListener" | "removeEventListener">;

    const handle = installIframeAuthBridge({
      target: fakeTarget,
      allowedOrigins: ["http://localhost:18091"],
      getToken: TOKEN_READ,
    });
    expect(listeners).toHaveLength(1);

    const source = makeSource();
    const ev = makeEvent({
      origin: "http://localhost:18091",
      data: { type: "alongside:request-token" },
      source,
    });
    listeners[0](ev);
    expect(source.postMessage).toHaveBeenCalledTimes(1);

    // dispose 后监听摘除，再来的握手不响应
    handle.dispose();
    expect(listeners).toHaveLength(0);
    listeners[0]?.(ev);
    expect(source.postMessage).toHaveBeenCalledTimes(1);
  });
});
