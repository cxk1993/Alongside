/**
 * 日程表数据请求层（集中在此，不散落组件）。
 *
 * 读请求走内核提供的 `@/shared/api/client`（自带 JWT / trace_id / problem+json 解析）。
 * 写请求额外带 `Idempotency-Key`（内核 IdempotencyMiddleware 据此去重，
 * 见 services/api/core/middleware.py：无该头时直接放行、不缓存，重试无法去重）。
 *
 * ★ 为什么写请求不直接用 `api.post`：共享 client 目前不接受自定义请求头，
 *   而 Idempotency-Key 必须是头字段（雷区 #8：写请求必带）。这里只在写请求上
 *   补一个带头的薄封装，复用共享 client 的 `ApiError` 与同一份 token 来源，
 *   不重造 JWT / 错误处理。建议内核 client 后续支持 `headers` 选项（已记入报告 issue）。
 */

import { api, ApiError } from "@/shared/api/client";

export interface CalendarEvent {
  id: string;
  title: string;
  color: string;
  start_at: string; // 带时区 ISO8601
  end_at: string;
  all_day: boolean;
  span_days: number;
  source: string;
  parent_id: string | null;
  sort: number;
  location: string | null;
  note: string | null;
  children: CalendarEvent[];
}

export interface FreeSlot {
  start: string;
  end: string;
  hours: number;
}

const BASE = "/api/v1/calendar";

function getToken(): string | null {
  try {
    return localStorage.getItem("alongside.token");
  } catch {
    return null;
  }
}

/**
 * 带 Idempotency-Key 的写请求封装。
 * 复用共享 client 的 ApiError，避免重复实现错误解析。
 */
async function idem<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  let payload: string | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
    // 每次写操作生成一次性幂等键；重试（同键）由内核去重。
    headers["Idempotency-Key"] = crypto.randomUUID();
  }

  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (e) {
    throw new ApiError({
      type: "https://alongside/network-error",
      title: "网络不可用",
      status: 0,
      detail: e instanceof Error ? e.message : String(e),
    });
  }

  const ct = res.headers.get("content-type") ?? "";
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      /* 非 JSON 响应体 */
    }
  }
  if (!res.ok) {
    if (ct.includes("application/problem+json") && data && typeof data === "object") {
      const p = data as Record<string, unknown>;
      throw new ApiError({
        type: typeof p.type === "string" ? p.type : "about:blank",
        title: typeof p.title === "string" ? p.title : "请求失败",
        status: typeof p.status === "number" ? p.status : res.status,
        detail: typeof p.detail === "string" ? p.detail : text,
        trace_id: typeof p.trace_id === "string" ? p.trace_id : undefined,
      });
    }
    throw new ApiError({
      type: "about:blank",
      title: `HTTP ${res.status}`,
      status: res.status,
      detail: text,
    });
  }
  return data as T;
}

export type EventPatch = Partial<
  Pick<
    CalendarEvent,
    "title" | "color" | "start_at" | "end_at" | "all_day" | "span_days" | "location" | "note"
  >
>;

export type EventCreate = {
  title: string;
  start_at: string;
  end_at: string;
  color?: string;
  all_day?: boolean;
  parent_id?: string | null;
  span_days?: number;
  location?: string | null;
  note?: string | null;
};

export const calendarApi = {
  listRange: (from: string, to: string, includeChildren = true) =>
    api.get<CalendarEvent[]>(
      `${BASE}/events?from=${encodeURIComponent(from)}&to=${encodeURIComponent(
        to,
      )}&include_children=${includeChildren}`,
    ),
  get: (id: string) => api.get<CalendarEvent>(`${BASE}/events/${id}`),
  create: (body: EventCreate) => idem<CalendarEvent>("POST", `${BASE}/events`, body),
  update: (id: string, body: EventPatch) =>
    idem<CalendarEvent>("PATCH", `${BASE}/events/${id}`, body),
  remove: (id: string) => idem<void>("DELETE", `${BASE}/events/${id}`),
  addChild: (parentId: string, body: EventCreate) =>
    idem<CalendarEvent>("POST", `${BASE}/events/${parentId}/children`, body),
  updateChild: (parentId: string, childId: string, body: EventPatch) =>
    idem<CalendarEvent>("PATCH", `${BASE}/events/${parentId}/children/${childId}`, body),
  removeChild: (parentId: string, childId: string) =>
    idem<void>("DELETE", `${BASE}/events/${parentId}/children/${childId}`),
  freeSlots: (date: string, minHours = 1) =>
    api.get<FreeSlot[]>(
      `${BASE}/free-slots?date=${encodeURIComponent(date)}&min_hours=${minHours}`,
    ),
};
