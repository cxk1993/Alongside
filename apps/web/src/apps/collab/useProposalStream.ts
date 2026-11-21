import { useEffect } from "react";

import { api } from "@/shared/api/client";

/**
 * ★ T21：提案实时推送（SSE）。
 *
 * 为什么要有这一层：提案面的数据此前只在**挂载时**拉一次（`useProposals` 的 useEffect）。
 * 于是「AI 刚提了一条建议」在人这边是看不见的——必须手动刷新页面。
 * 而整条产品叙事的第一拍就是「它提了 → 你看见 → 你签字」，看不见这一拍就断了。
 *
 * 鉴权为什么不直接把 token 拼进 URL（内核 `core/app.py` 的 F2 加固口径）：
 *   浏览器的原生 `EventSource` **不能带自定义 header**，SSE 只能靠 query 传凭证；
 *   而把一个 30 分钟的 access token 塞进 URL，会进浏览器历史 / 代理日志 / Referer。
 *   所以先用**已鉴权的 POST** 换一张 60 秒的 `type=sse` 短票，再拼到 SSE URL 上。
 *
 * 失败姿态（不装深度）：
 *   - 换票失败（网络/401）→ 退避重试，**不抛错、不白屏**；列表仍然保留挂载时拉到的那份。
 *   - 连接断了（`onerror`）→ 关掉重连（票会过期，所以每次重连都重新换票）。
 *   - 环境里没有 `EventSource`（SSR/老浏览器/部分测试环境）→ 静默不订阅，
 *     行为退回到「挂载时拉一次」，不假装有实时能力。
 */

/** 订阅的 topic 模式：提案的四种状态变更全覆盖（比丢 67d98f3 真发的就是这四个）。 */
export const PROPOSAL_TOPIC = "proposals.*";

/** 内核真发的事件名（manifest `emits` 声明的那四条）。 */
export const PROPOSAL_EVENTS = [
  "proposals.proposal.created",
  "proposals.proposal.approved",
  "proposals.proposal.rejected",
  "proposals.proposal.undone",
] as const;

export function useProposalStream(onEvent: () => void, retryMs = 3000): void {
  useEffect(() => {
    // 没有原生 EventSource 就不订阅 —— 退回「挂载时拉一次」，不假装实时。
    if (typeof EventSource === "undefined") return undefined;

    let closed = false;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = (ms: number) => {
      if (closed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void connect(), ms);
    };

    const connect = async () => {
      if (closed) return;
      let ticket: string;
      try {
        const t = await api.post<{ ticket: string; expires_in: number }>("/api/v1/events/ticket", {});
        ticket = t.ticket;
      } catch {
        // 换票失败：退避重试。不设上限次数——页面关掉时 cleanup 会停掉它。
        schedule(Math.max(retryMs, 10000));
        return;
      }
      if (closed) return;
      es = new EventSource(
        `/api/v1/events/subscribe?topics=${encodeURIComponent(PROPOSAL_TOPIC)}&ticket=${encodeURIComponent(ticket)}`,
      );
      const hit = () => onEvent();
      for (const name of PROPOSAL_EVENTS) es.addEventListener(name, hit);
      es.onerror = () => {
        // 断线：关掉旧连接、重新换票再连（旧票可能已过期，绝不能复用）。
        es?.close();
        es = null;
        schedule(retryMs);
      };
    };

    void connect();

    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      es?.close();
    };
  }, [onEvent, retryMs]);
}