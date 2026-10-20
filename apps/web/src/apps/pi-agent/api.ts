/**
 * pi-agent 模块数据请求层。
 *
 * 后端契约（plugins/pi-agent/api/router.py）：
 *   POST /api/v1/pi-agent/chat          {session_id, message} → {reply, level, settled, degraded, detail}
 *   POST /api/v1/pi-agent/chat/stream   同上，SSE：delta / settled / error
 *   GET  /api/v1/pi-agent/sessions      → {sessions: [...], pool: {...}}
 *   POST /api/v1/pi-agent/sessions      {action, session} → new|lock|release|stats
 *   GET  /api/v1/pi-agent/status        → {level, level_text, pool, model, ...}
 */
import { api } from "@/shared/api/client";

const BASE = "/api/v1/pi-agent";

export interface ChatOut {
  session_id: string;
  reply: string;
  level: string;
  settled: boolean;
  degraded: boolean;
  detail: string | null;
}

export interface SessionInfo {
  session: string;
  alive: boolean;
  busy: boolean;
  age_seconds: number;
  idle_seconds: number;
}

export interface PoolStatus {
  sessions: number;
  max_sessions: number;
  busy: number;
  alive: number;
  names: string[];
}

/** ★ 会话历史条目（后端 /sessions/history 归一化后）。 */
export interface HistoryMsg {
  role: "user" | "assistant" | "tool";
  text: string;
  ts?: string | number;
  tool?: string;
  error?: boolean;
}

export interface HistoryOut {
  session: string | null;
  session_id: string | null;
  count: number;
  total: number;
  messages: HistoryMsg[];
  detail?: string;
}

export interface AgentStatus {
  id?: string;
  stage?: string;
  level: string;
  level_text?: string;
  model?: string;
  provider?: string;
  alive?: boolean;
  pool?: PoolStatus;
  detail?: string;
}

export const piAgentApi = {
  status: () => api.get<AgentStatus>(`${BASE}/status`),

  chat: (message: string, sessionId: string) =>
    api.post<ChatOut>(`${BASE}/chat`, { message, session_id: sessionId }),

  listSessions: () =>
    api.get<{ sessions: SessionInfo[]; pool: PoolStatus }>(`${BASE}/sessions`),

  /**
   * ★ 2026-09-27（主人候办③「找不到历史」补刀）：**读取会话历史消息**。
   * 后端 `GET /sessions/history` 读 pi 原生 JSONL 并归一化 → [{role, text, ts}]。
   * session 空/`default` = 最新会话。
   */
  history: (session: string, limit = 200) =>
    api.get<HistoryOut>(
      `${BASE}/sessions/history?session=${encodeURIComponent(session)}&limit=${limit}`,
    ),

  sessionAction: (action: string, session: string) =>
    api.post<Record<string, unknown>>(`${BASE}/sessions`, { action, session }),

  resetCircuit: () => api.post<Record<string, unknown>>(`${BASE}/reset-circuit`),
};

/** ★ 流式对话：SSE 逐字回调。浏览器原生 EventSource 不能带 header，故用 fetch + ReadableStream。 */
export async function chatStream(
  message: string,
  sessionId: string,
  handlers: {
    onDelta?: (text: string) => void;
    onSettled?: () => void;
    onError?: (detail: string, level?: string) => void;
  },
  signal?: AbortSignal,
): Promise<void> {
  const token =
    typeof localStorage !== "undefined" ? localStorage.getItem("alongside.token") : null;
  const res = await fetch(`${BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok || !res.body) {
    handlers.onError?.(`HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  // 逐块解析 SSE：事件以空行分隔，data: 行承载 JSON
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() ?? "";
    for (const chunk of chunks) {
      let event = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      try {
        const obj = JSON.parse(data);
        if (event === "delta") handlers.onDelta?.(obj.text ?? "");
        else if (event === "settled") handlers.onSettled?.();
        else if (event === "error") handlers.onError?.(obj.detail ?? "未知错误", obj.level);
      } catch {
        /* 半包/坏包忽略，等下一块 */
      }
    }
  }
}
