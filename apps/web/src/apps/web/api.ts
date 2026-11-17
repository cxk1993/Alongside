/**
 * 网页工作台数据请求层（集中在此，不散落各处）。
 * 跨插件拿数据一律走 HTTP（这里只用 web 自己的 /api/v1/web）。
 * 鉴权 / trace_id / RFC7807 解析由 @/shared/api/client 统一处理。
 */
import { api } from "@/shared/api/client";

export type WebKind = "web" | "web+rest" | "web+mcp";

export const WEB_KINDS: WebKind[] = ["web", "web+rest", "web+mcp"];

/** 入口类型枚举 → 用户可见中文（与 CatalogApp 的 KIND_LABEL 同口径）。 */
export const WEB_KIND_LABEL: Record<WebKind, string> = {
  web: "网页",
  "web+rest": "REST API",
  "web+mcp": "MCP",
};

/**
 * ★ 能力条目形状 —— 与后端 schema.CapabilityEntry 及 T20 能力目录**同一份字段**。
 *   source：web_entry（本插件产出）| plugin（T20 自动）| kernel（T20 内核基础）
 */
export interface CapabilityEntry {
  id: string;
  name: string;
  kind: WebKind;
  url: string | null;
  endpoint: string | null;
  auth_ref: string | null;
  capabilities: string[];
  enabled: boolean;
  note: string | null;
  source: string;
}

export interface WebEntry {
  id: string;
  slug: string;
  title: string;
  url: string;
  icon: string | null;
  order: number;
  enabled: boolean;
  kind: WebKind;
  endpoint: string | null;
  auth_ref: string | null;
  capabilities: string[];
  note: string | null;
  created_at: string;
  updated_at: string;
  /** 该条目进入 catalog 时的形状（只读预览；由后端给出，前端不重复拼装） */
  capability: CapabilityEntry | null;
}

export interface WebEntryList {
  items: WebEntry[];
  total: number;
}

export interface WebEntryInput {
  slug: string;
  title: string;
  url: string;
  icon?: string | null;
  order?: number;
  enabled?: boolean;
  kind?: WebKind;
  endpoint?: string | null;
  auth_ref?: string | null;
  capabilities?: string[];
  note?: string | null;
}

export type WebEntryPatch = Partial<Omit<WebEntryInput, "slug">>;

export interface TouchResult {
  id: string;
  opened_at: string;
}

/**
 * 内嵌用的真实 URL（后端 `frame-url` 端点）。
 * ★ 收录条目时凭据只存 `auth_ref` 引用（如 `token:env:PI_WEB_TOKEN`），
 *   真值由后端运行时从环境变量取出拼进 URL —— 前端永远看不到密码本体。
 */
export interface FrameUrl {
  id: string;
  slug: string;
  title: string;
  url: string;
  auth_ref: string | null;
  /** true = 已从 env 解析出凭据并拼进 url；false = 无凭据，url 原样返回 */
  parsed: boolean;
}

const BASE = "/api/v1/web";

export const webApi = {
  list: (enabled?: boolean) => {
    const q = enabled === undefined ? "" : `?enabled=${enabled ? "true" : "false"}`;
    return api.get<WebEntryList>(`${BASE}/entries${q}`);
  },

  create: (body: WebEntryInput) => api.post<WebEntry>(`${BASE}/entries`, body),

  update: (id: string, body: WebEntryPatch) =>
    api.patch<WebEntry>(`${BASE}/entries/${encodeURIComponent(id)}`, body),

  remove: (id: string) => api.delete<void>(`${BASE}/entries/${encodeURIComponent(id)}`),

  touch: (id: string) => api.post<TouchResult>(`${BASE}/entries/${encodeURIComponent(id)}/touch`),

  /**
   * 取 iframe 真正要加载的地址。
   * ★ 条目没填 auth_ref 时后端原样回 url（parsed=false），行为与"直接用 entry.url"一致；
   *   填了 auth_ref 才会注入凭据 —— 所以这个调用对老条目**零副作用**。
   */
  frameUrl: (id: string) =>
    api.get<FrameUrl>(`${BASE}/entries/${encodeURIComponent(id)}/frame-url`),
};

/** 查询键集中管理（避免各处手写字符串拼错）。 */
export const webKeys = {
  all: ["web"] as const,
  entries: (enabled?: boolean) => ["web", "entries", enabled ?? "all"] as const,
};
