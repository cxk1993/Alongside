import { useEffect, useRef, useState } from "react";

import { api } from "@/shared/api/client";

/**
 * ★ P0-c（AgentStream）：跨模块事件流 —— 「它正在做什么」。
 *
 * 和 `useProposalStream` 的分工：
 *   - `useProposalStream`：只关心提案四态，收到就重拉列表（驱动**提案卡**）。
 *   - 本 hook：订阅**所有模块声明过的事件**，只读留痕（驱动**活动条**）。
 *     它是「机器活着」的证据：AI 刚刚碰了哪块业务，不必等提案。
 *
 * 三条纪律：
 *   1. **凭证不进 URL 的例外照抄内核口径**：原生 EventSource 不能带 header，
 *      先 POST `/api/v1/events/ticket` 换 60 秒短票，再拼到 SSE URL。
 *   2. **命名事件必须逐个挂**：服务端 `event: <topic>` 是精确事件名，
 *      `onmessage` 只收 `event: message`。所以客户端用 manifest.emits 的并集挂监听。
 *   3. **失败姿态 = 安静降级**：换票失败重试（退避），没有 EventSource 就不订阅、
 *      报 `offline`；界面写「实时未连接」，**绝不假装有实时能力**。
 */

export interface ActivityEvent {
  /** 事件序号（服务端自增，用于去重与排序）。 */
  id: number;
  /** 唯一 id（服务端 eid）；缺失时退回 `id`。 */
  eid: string;
  topic: string;
  /** ISO 时间（服务端 `at`）。 */
  at: string;
  source: string;
  /** 摘要：payload 里的 title/summary/name/label 之一；都没有就 null（不编）。 */
  summary: string | null;
}

export type ActivityStatus = "connecting" | "live" | "offline";

/** 服务端一帧的形状（只取用到的字段；其余原样丢弃）。 */
interface RawEvent {
  id?: unknown;
  eid?: unknown;
  topic?: unknown;
  at?: unknown;
  source?: unknown;
  payload?: unknown;
}

const SUMMARY_KEYS = ["title", "summary", "name", "label"] as const;

/**
 * 从 payload 里取一句人能读的摘要。
 * 只认这四个键（都必须是字符串），其余一律 null —— 猜字段名等于编数据。
 */
export function summarizePayload(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const rec = payload as Record<string, unknown>;
  for (const k of SUMMARY_KEYS) {
    const v = rec[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}

/** 把服务端一帧解析成 ActivityEvent；缺关键字段就返回 null（丢弃而不是补假值）。 */
export function parseEvent(raw: unknown): ActivityEvent | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as RawEvent;
  const topic = typeof r.topic === "string" ? r.topic : "";
  if (!topic) return null;
  const id = typeof r.id === "number" ? r.id : Number.NaN;
  if (!Number.isFinite(id)) return null;
  return {
    id,
    eid: typeof r.eid === "string" && r.eid ? r.eid : String(id),
    topic,
    at: typeof r.at === "string" ? r.at : "",
    source: typeof r.source === "string" ? r.source : "",
    summary: summarizePayload(r.payload),
  };
}

/** 服务端历史补发 + 实时推送，按 id 去重、倒序保留最近 max 条。 */
export function mergeEvents(prev: ActivityEvent[], next: ActivityEvent, max: number): ActivityEvent[] {
  if (prev.some((e) => e.eid === next.eid || e.id === next.id)) return prev;
  const out = [next, ...prev].sort((a, b) => b.id - a.id);
  return out.slice(0, max);
}

export interface UseEventStreamResult {
  events: ActivityEvent[];
  status: ActivityStatus;
}

export function useEventStream(
  topics: readonly string[],
  max = 20,
  retryMs = 3000,
): UseEventStreamResult {
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [status, setStatus] = useState<ActivityStatus>("connecting");
  // topics 是数组：调用方每次渲染都新建数组，用 join 做依赖键避免无限重连。
  const topicKey = topics.join(",");
  const maxRef = useRef(max);
  maxRef.current = max;

  useEffect(() => {
    const list = topicKey ? topicKey.split(",") : [];
    // 没有原生 EventSource（SSR / 老浏览器 / 部分测试环境）：不订阅，如实说离线。
    if (typeof EventSource === "undefined") {
      setStatus("offline");
      return undefined;
    }

    let closed = false;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (ms: number) => {
      if (closed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void connect(), ms);
    };

    const onFrame = (ev: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(String(ev.data));
      } catch {
        return; // 非 JSON 帧（keep-alive 注释等）直接忽略，不制造假事件
      }
      const item = parseEvent(parsed);
      if (!item) return;
      setEvents((prev) => mergeEvents(prev, item, maxRef.current));
    };

    const connect = async () => {
      if (closed) return;
      let ticket: string;
      try {
        const t = await api.post<{ ticket: string; expires_in: number }>("/api/v1/events/ticket", {});
        ticket = t.ticket;
      } catch {
        // 换票失败：退避重试；状态保持 connecting（没连上，不谎报 live）
        schedule(Math.max(retryMs, 10000));
        return;
      }
      if (closed) return;
      const qs = new URLSearchParams({ ticket });
      if (list.length) qs.set("topics", list.join(","));
      const src = new EventSource(`/api/v1/events/subscribe?${qs.toString()}`);
      es = src;
      src.onopen = () => setStatus("live");
      // 命名事件逐个挂：topic 名就是事件名。
      for (const t of list) src.addEventListener(t, onFrame as EventListener);
      // 兜底：万一服务端用了默认 event 名
      src.addEventListener("message", onFrame as EventListener);
      src.onerror = () => {
        // 票是一次性的：断线一律重新换票重连，不复用旧票。
        try {
          src.close();
        } catch {
          /* 已关闭 */
        }
        if (es === src) es = null;
        setStatus("connecting");
        schedule(retryMs);
      };
    };

    setStatus("connecting");
    void connect();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      if (es) {
        try {
          es.close();
        } catch {
          /* 已关闭 */
        }
      }
    };
  }, [topicKey, retryMs]);

  return { events, status };
}