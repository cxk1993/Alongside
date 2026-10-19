/**
 * 理财模块数据请求层。鉴权 / RFC7807 由 @/shared/api/client 统一处理。
 * 金额一律整数分（amount_cents），禁止浮点。
 */
import { api } from "@/shared/api/client";

export type Direction = "expense" | "income";

export interface FinanceEntry {
  id: string;
  amount_cents: number;
  direction: Direction;
  category: string;
  account: string;
  occurred_at: string;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceList {
  items: FinanceEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface CategoryTotal {
  category: string;
  expense_cents: number;
  income_cents: number;
  count: number;
}

export interface FinanceSummary {
  date_from: string | null;
  date_to: string | null;
  expense_cents: number;
  income_cents: number;
  net_cents: number;
  count: number;
  by_category: CategoryTotal[];
}

export interface EntryCreate {
  direction: Direction;
  amount_cents?: number;
  amount?: string;
  category?: string;
  account?: string;
  occurred_at: string;
  note?: string | null;
}

export interface ListParams {
  category?: string;
  account?: string;
  direction?: Direction;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

/** BeeCount 只读快照（T08B）。金额整数分；meta 存 MCP 原始摘要。 */
export interface FinanceSnapshot {
  id: string;
  date: string;
  total_asset: number;
  cash: number;
  invest: number;
  debt: number;
  meta: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface FinanceSnapshotList {
  items: FinanceSnapshot[];
  total: number;
  limit: number;
  offset: number;
}

export interface SnapshotSyncResult {
  ok: boolean;
  upstream: string;
  snapshot: FinanceSnapshot | null;
}

/**
 * 上游状态。**绝不包含 token**；token_present 仅布尔。
 * write_enabled 本轮恒为 false（T08B 只读）。
 */
export interface BeeCountSource {
  upstream: string;
  configured: boolean;
  base_url_configured: boolean;
  token_present: boolean;
  last_sync: string | null;
  last_snapshot_date: string | null;
  snapshot_count: number;
  write_enabled: boolean;
  read_tools: string[];
  mcp_path: string;
}

const BASE = "/api/v1/finance";

export const financeApi = {
  list: (params: ListParams = {}) => {
    const q = new URLSearchParams();
    if (params.category) q.set("category", params.category);
    if (params.account) q.set("account", params.account);
    if (params.direction) q.set("direction", params.direction);
    if (params.date_from) q.set("date_from", params.date_from);
    if (params.date_to) q.set("date_to", params.date_to);
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    const qs = q.toString();
    return api.get<FinanceList>(`${BASE}/entries${qs ? `?${qs}` : ""}`);
  },
  create: (body: EntryCreate) => api.post<FinanceEntry>(`${BASE}/entries`, body),
  update: (id: string, body: Partial<EntryCreate>) =>
    api.patch<FinanceEntry>(`${BASE}/entries/${id}`, body),
  remove: (id: string) => api.delete<void>(`${BASE}/entries/${id}`),
  summary: (params: { date_from?: string; date_to?: string } = {}) => {
    const q = new URLSearchParams();
    if (params.date_from) q.set("date_from", params.date_from);
    if (params.date_to) q.set("date_to", params.date_to);
    const qs = q.toString();
    return api.get<FinanceSummary>(`${BASE}/summary${qs ? `?${qs}` : ""}`);
  },
  /** T08B：快照列表（date 倒序）。只走 Alongside 后端，前端不直连 BeeCount。 */
  snapshots: (params: { limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.limit != null) q.set("limit", String(params.limit));
    if (params.offset != null) q.set("offset", String(params.offset));
    const qs = q.toString();
    return api.get<FinanceSnapshotList>(`${BASE}/snapshots${qs ? `?${qs}` : ""}`);
  },
  /** T08B：手动触发只读同步（调 Alongside 后端，由后端走 BeeCount MCP）。 */
  syncSnapshots: () => api.post<SnapshotSyncResult>(`${BASE}/snapshots/sync`, {}),
  /** T08B：上游状态（mock|mcp / configured / last_sync）。 */
  beeCountSource: () => api.get<BeeCountSource>(`${BASE}/beecount/source`),
};

/** 分 → 人读金额字符串（精确，不用 toFixed 舍入到分以外）。 */
export function formatCents(cents: number, direction?: Direction): string {
  const sign = cents < 0 ? "-" : direction === "income" ? "+" : "";
  const abs = Math.abs(cents);
  const yuan = Math.floor(abs / 100);
  const fen = abs % 100;
  return `${sign}${yuan}.${String(fen).padStart(2, "0")}`;
}
