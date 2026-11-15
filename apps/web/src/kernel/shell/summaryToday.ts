/** 今日摘要 BFF 共享形状与单请求 hook（r6 起各组件共用）。
 *
 * 裁决令61 丙案：前端只调 GET /api/v1/summary/today，杜绝双份。
 * providers[] 由后端按 manifest 声明（provides 含 x.summary.today）动态发现——
 * 新插件声明该能力即自动进今日面板，前端零改动、零名单。
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/shared/api/client";

export interface SummaryProvider {
  id: string;
  status: "ok" | "not-implemented" | "unavailable";
  /** 后端随 manifest 带来的显示名（_fetch_one 有值才放）；缺省时前端回退模块 id。 */
  label?: string;
  data?: { title?: string; items?: unknown[]; link?: string };
}

export interface SummaryTodayResp {
  providers: SummaryProvider[];
}

const SUMMARY_KEY = ["summary-today"] as const;

/** 共享 BFF 单请求。queryKey 固定 → 多组件挂载也只发一次（React Query 去重）。 */
export function useSummaryToday() {
  return useQuery({
    queryKey: SUMMARY_KEY,
    queryFn: (): Promise<SummaryTodayResp> =>
      api.get<SummaryTodayResp>("/api/v1/summary/today"),
    retry: 1,
    refetchOnWindowFocus: false,
  });
}

/** provider id → 显示名（BFF label 优先，回退模块 id）。 */
export function providerLabels(
  providers: SummaryProvider[] | undefined,
): Record<string, string> {
  return Object.fromEntries((providers ?? []).map((p) => [p.id, p.label ?? p.id]));
}
