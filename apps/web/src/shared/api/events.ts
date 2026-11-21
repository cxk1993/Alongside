/**
 * 内核事件总线（SSE 订阅）。
 *
 * 总纲 §1.4：所有模块事件统一经 `GET /api/v1/events/subscribe` 的 SSE 流推送。
 * 这里做：自动重连 + 指数退避；后端未就绪时静默降级，不抛不崩。
 *
 * 模块侧用 `usePluginEvent(type, cb)` 订阅自己关心的事件。
 */

import { useEffect } from "react";

import { api, ApiError } from "./client";

export interface PluginEventEnvelope {
  /** ★ bidiu 2026-11-21（B0 横切修复）：信封字段名与后端一致（`core/events.py` 的 `topic`，
   *  如 `todo.item.due`）。历史遗留的 `type` 字段后端从未发送过——
   *  保留 `type` 仅做读取兼容，新代码一律用 `topic`。 */
  topic: string;
  type?: string;
  payload?: unknown;
}

type Handler = (payload: unknown) => void;
export type { Handler };

const SSE_URL = "/api/v1/events/subscribe";
const MAX_BACKOFF = 30_000;

class EventBus {
  private es: EventSource | null = null;
  private handlers = new Map<string, Set<Handler>>();
  /** ★ B0（bidiu 2026-11-21）：每个 topic 的具名事件监听器（`addEventListener` 的引用）。
   *  add/removeEventListener 必须同一函数引用；换连接（新 EventSource 对象）后要全量重挂。 */
  private listeners = new Map<string, EventListener>();
  private retry = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  /** 取票期间的重入保护：多个订阅同时触发时只换一次票。 */
  private connecting = false;

  subscribe(type: string, handler: Handler): () => void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
      // ★ B0：晚到的订阅者——连接还活着就把监听器挂上，不必等下一次重连。
      if (this.es) this.bindTopic(this.es, type);
    }
    set.add(handler);
    void this.connect();
    return () => {
      const s = this.handlers.get(type);
      if (!s) return;
      s.delete(handler);
      if (s.size === 0) {
        this.handlers.delete(type);
        // ★ B0：最后一个订阅者退走 → 把该 topic 的具名监听也摘掉，
        //   否则帧来了会 dispatch 给空集合（无害但白烧）。
        const listener = this.listeners.get(type);
        if (this.es && listener) this.es.removeEventListener(type, listener);
        this.listeners.delete(type);
        if (this.handlers.size === 0) this.disconnect();
      }
    };
  }

  /**
   * ★ B0（bidiu 2026-11-21）：把某个 topic 的监听器挂到一个 EventSource 上。
   *
   * 为什么必须按 topic 挂（`addEventListener`）而不是 `es.onmessage`：
   * 后端 SSE 帧是**具名事件**——`core/events.py` 的 `_format_sse` 每条都写
   * `event: <topic>`。EventSource 语义：具名帧**不触发 onmessage**，只投递给
   * 对应名字的事件监听器。旧实现用 onmessage 收 → 28 个订阅点 / 11 个 app 的
   * 实时刷新从未生效过（帧全被静默丢弃）。pi-agent 的自解析路径不受影响。
   */
  private bindTopic(es: EventSource, topic: string): void {
    // ★ 监听器函数体无状态（只碰 this.dispatch / this.retry），同一引用可以挂在
    //   多个 EventSource 对象上。换连接（重连）时必须把**已有**监听器挂到新对象——
    //   只判断「有没有」而不重挂，会让监听器随旧对象一起死掉（总线永久哑掉）。
    let listener = this.listeners.get(topic);
    if (!listener) {
      listener = ((ev: MessageEvent) => {
        this.retry = 0; // 有帧 = 连接活着，退避计数归零（原 onmessage 里的死逻辑移到这里才真正生效）
        try {
          const env = JSON.parse(ev.data) as PluginEventEnvelope;
          this.dispatch(topic, env?.payload);
        } catch {
          /* 非法消息忽略（retry 仍归零：连接本身是活的） */
        }
      }) as EventListener;
      this.listeners.set(topic, listener);
    }
    es.addEventListener(topic, listener);
  }

  /** 具名帧之外的兜底：若将来后端发匿名帧（无 `event:` 行），按信封里的 topic 派发。 */
  private onAnonymousFrame(ev: MessageEvent): void {
    this.retry = 0;
    try {
      const env = JSON.parse(ev.data) as PluginEventEnvelope;
      const name = env?.topic ?? (typeof env?.type === "string" ? env.type : null);
      if (name) this.dispatch(name, env.payload);
    } catch {
      /* 忽略 */
    }
  }

  private dispatch(type: string, payload: unknown): void {
    this.handlers.get(type)?.forEach((h) => {
      try {
        h(payload);
      } catch {
        /* 单个订阅者出错不影响其它 */
      }
    });
  }

  /**
   * 换取 SSE 入场券（F2 加固，2026-09-23）。
   *
   * 为什么需要：浏览器原生 `EventSource` **不能带自定义 header**，所以 SSE 的
   * 鉴权只能走 query；而把 access token 直接拼进 URL 会进浏览器历史 / 代理日志 /
   * Referer —— 故先用已鉴权的 HTTP 请求换一张「60 秒 + type=sse」的短时票据。
   *
   * 返回 null 的语义 = 「本次不连」（未登录 / 票据拿不到），调用方静默等待，
   * **不做无谓的退避轰炸** —— 登录后组件重新订阅会自然触发重连。
   */
  private async fetchTicket(): Promise<string | null> {
    try {
      const res = await api.post<{ ticket: string }>("/api/v1/events/ticket", {});
      return res?.ticket ?? null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return null; // 未登录：静默等
      throw e; // 网络/后端异常：交给上层退避重连
    }
  }

  private async connect(): Promise<void> {
    if (this.es || this.connecting || typeof EventSource === "undefined") return;
    this.connecting = true;
    let es: EventSource;
    try {
      const ticket = await this.fetchTicket();
      if (!ticket) return; // 未登录：不发无谓重连
      es = new EventSource(`${SSE_URL}?ticket=${encodeURIComponent(ticket)}`);
    } catch {
      this.scheduleReconnect();
      return;
    } finally {
      this.connecting = false;
    }
    this.es = es;
    // ★ B0：每次重连都是**新的 EventSource 对象**——具名监听器不随对象保留，
    //   必须把现有 topic 全部重新挂上，否则总线随旧对象一起死掉。
    for (const topic of this.handlers.keys()) this.bindTopic(es, topic);
    // 前向兼容：若后端将来发匿名帧（无 event: 行），按信封里的 topic 派发。
    es.onmessage = (ev: MessageEvent) => this.onAnonymousFrame(ev);
    es.onerror = () => {
      this.disconnect();
      this.scheduleReconnect(); // 票据 60s 过期后重连会自动换新票
    };
  }

  private scheduleReconnect(): void {
    if (this.timer) return;
    const delay = Math.min(MAX_BACKOFF, 500 * 2 ** this.retry);
    this.retry += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.connect();
    }, delay);
  }

  private disconnect(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.es) {
      this.es.close();
      this.es = null;
    }
  }
}

export const eventBus = new EventBus();

/** 订阅某类插件事件；组件卸载自动退订。 */
export function usePluginEvent(type: string, handler: Handler): void {
  useEffect(() => eventBus.subscribe(type, handler), [type, handler]);
}
