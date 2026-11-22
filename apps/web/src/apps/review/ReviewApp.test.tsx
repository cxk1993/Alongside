/** 复盘前端测试。网络层全部 mock，不打真实后端。 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import ReviewApp from "./ReviewApp";
import { formatSeconds } from "./api";
import type { DayDetail, DaysOut, SourceInfo, TrendOut, WeeklyOut } from "./api";

const source: SourceInfo = {
  mode: "mock",
  path: "direct",
  bridge: false,
  bridge_online: null,
  upstream_online: true,
  upstream_version: "1.0.56",
  last_sync_at: new Date().toISOString(),
  message: "ok",
};

const dayItem = {
  date: "2026-09-13",
  is_empty: false,
  total_seconds: 11632,
  category_count: 3,
  app_count: 4,
  has_ai: true,
  has_raw: true,
  raw_path: "work-review:2026-09-13",
  top_categories: [{ name: "编码", seconds: 11632, duration_text: "3小时13分52秒" }],
  synced_at: new Date().toISOString(),
};

const daysOut: DaysOut = {
  items: [dayItem],
  total: 1,
  page: 1,
  size: 10,
  has_more: false,
};

const dayDetail: DayDetail = {
  date: "2026-09-13",
  is_empty: false,
  empty_hint: "",
  categories: [{ name: "编码", seconds: 11632, duration_text: "3小时13分52秒" }],
  apps: [{ name: "Code.exe", seconds: 7200, duration_text: "2小时0分0秒" }],
  domains: [{ name: "github.com", seconds: 1800, duration_text: "30分0秒" }],
  hourly: [
    { hour: 9, seconds: 3600, duration_text: "1小时0分0秒" },
    { hour: 10, seconds: 0, duration_text: "0秒" },
  ],
  ai_analysis_md: "## AI 分析\n今日编码较多",
  total_seconds: 11632,
  raw_path: "work-review:2026-09-13",
  has_raw: true,
  synced_at: new Date().toISOString(),
  notes: [
    { id: "n1", date: "2026-09-13", content_md: "手写批注", created_at: new Date().toISOString() },
  ],
  source,
};

const trend: TrendOut = {
  metric: "total",
  days: 7,
  points: [
    { date: "2026-09-12", total_seconds: 8000, values: {} },
    { date: "2026-09-13", total_seconds: 11632, values: {} },
  ],
  labels: ["2026-09-12", "2026-09-13"],
  conclusion: "近 2 天有记录，日均 2小时44分，最近一日 3小时13分52秒",
};

const weekly: WeeklyOut = {
  date: "2026-09-13",
  available: true,
  offline_hint: "",
  week_start: "2026-09-07",
  week_end: "2026-09-13",
  total_seconds: 40000,
  days: [],
  summary: "本周合计 11小时6分40秒",
  source: "mock",
  cached: false,
};

vi.mock("@/shared/api/events", () => ({
  usePluginEvent: () => {},
}));

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    reviewApi: {
      health: vi.fn().mockResolvedValue({
        ok: true,
        upstream_mode: "mock",
        path: "mock",
        bridge: false,
        version: "1.0.56",
        status: "ok",
        message: "ok",
      }),
      source: vi.fn().mockResolvedValue({
        mode: "mock",
        path: "direct",
        bridge: false,
        bridge_online: null,
        upstream_online: true,
        upstream_version: "1.0.56",
        last_sync_at: new Date().toISOString(),
        message: "ok",
      }),
      days: vi.fn().mockResolvedValue({
        items: [],
        total: 0,
        page: 1,
        size: 10,
        has_more: false,
      }),
      day: vi.fn(),
      trend: vi.fn().mockResolvedValue({
        metric: "total",
        days: 7,
        points: [],
        labels: [],
        conclusion: "",
      }),
      weekly: vi.fn(),
      raw: vi.fn(),
      ingest: vi.fn().mockResolvedValue({
        date: "2026-09-13",
        id: "x",
        raw_path: "work-review:2026-09-13",
        is_empty: false,
        category_count: 3,
        app_count: 4,
        has_ai: true,
        has_raw: true,
        synced_at: new Date().toISOString(),
        mode: "mock",
        path: "mock",
        event: "review.day.ingested",
      }),
      notes: vi.fn().mockResolvedValue({ date: null, items: [] }),
      addNote: vi.fn().mockResolvedValue({
        id: "n2",
        date: "2026-09-13",
        content_md: "新批注",
        created_at: new Date().toISOString(),
      }),
    },
  };
});

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("formatSeconds", () => {
  it("格式化时分秒", () => {
    expect(formatSeconds(3 * 3600 + 13 * 60 + 52)).toBe("3小时13分52秒");
    expect(formatSeconds(41 * 60 + 13)).toBe("41分13秒");
    expect(formatSeconds(29)).toBe("29秒");
  });
});

describe("ReviewApp", () => {
  it("空列表显示引导文案与 source", async () => {
    render(<ReviewApp />, { wrapper: makeWrapper() });
    expect(await screen.findByText(/还没有日报/)).toBeTruthy();
    expect(screen.getByTestId("review-source").textContent).toMatch(/Work-Review/);
  });

  it("渲染日列表与单日摘要、AI、批注", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue(dayDetail);
    vi.mocked(reviewApi.weekly).mockResolvedValue(weekly);
    vi.mocked(reviewApi.trend).mockResolvedValue(trend);

    render(<ReviewApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByText("2026-09-13")).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText("编码")).toBeTruthy();
    });
    expect(screen.getByTestId("ai-analysis").textContent).toContain("今日编码较多");
    expect(screen.getByText("手写批注")).toBeTruthy();
    expect(screen.getByText(/本周合计/)).toBeTruthy();
  });

  /* ★ 2026-11-22（汐瑶 · 三态收口 9c73b4f）：`isLoading || !day` 把 error 也说成「加载中…」；
     weekly 的 `week ? … : 加载中…` 同理 —— 接口挂了永远转不完。 */
  it("单日/周报接口失败 → 说「没读到」，不再永远「加载中…」", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.source).mockResolvedValue(source);
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockRejectedValue(new Error("boom"));
    vi.mocked(reviewApi.weekly).mockRejectedValue(new Error("boom"));
    vi.mocked(reviewApi.trend).mockResolvedValue(trend);

    render(<ReviewApp />, { wrapper: makeWrapper() });
    const hits = await screen.findAllByText(/没读到 —— 接口异常/);
    expect(hits.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText("加载中…")).toBeNull();
  });

  it("手动同步按钮调用 ingest", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue(dayDetail);
    vi.mocked(reviewApi.weekly).mockResolvedValue(weekly);

    render(<ReviewApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "手动同步日报" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "手动同步日报" }));
    await waitFor(() => {
      expect(reviewApi.ingest).toHaveBeenCalled();
    });
  });

  it("打开原文抽屉显示 markdown", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue(dayDetail);
    vi.mocked(reviewApi.weekly).mockResolvedValue(weekly);
    vi.mocked(reviewApi.raw).mockResolvedValue({
      date: "2026-09-13",
      markdown: "# 工作日报\n<!-- WR_BLOCK_START:CATEGORY_TABLE -->",
      raw_path: "work-review:2026-09-13",
      found: true,
    });

    render(<ReviewApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "查看原始日报" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "查看原始日报" }));
    await waitFor(() => {
      expect(screen.getByTestId("raw-markdown").textContent).toContain("WR_BLOCK_START");
    });
  });

  it("桥离线时 source 显示桥离线且不转圈", async () => {
    const { reviewApi } = await import("./api");
    const offlineSrc: SourceInfo = {
      ...source,
      path: "bridge",
      bridge: true,
      bridge_online: false,
      message: "桥离线",
    };
    vi.mocked(reviewApi.source).mockResolvedValue(offlineSrc);
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue({ ...dayDetail, source: offlineSrc });
    vi.mocked(reviewApi.weekly).mockResolvedValue({
      ...weekly,
      available: false,
      offline_hint: "桥离线",
    });

    render(<ReviewApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByTestId("review-source").textContent).toBe("桥离线");
    });
    // 已缓存日期仍可看
    expect(screen.getByText("2026-09-13")).toBeTruthy();
  });

  it("提交批注调用 addNote", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue(dayDetail);
    vi.mocked(reviewApi.weekly).mockResolvedValue(weekly);

    render(<ReviewApp />, { wrapper: makeWrapper() });
    const input = await screen.findByLabelText("批注内容");
    fireEvent.change(input, { target: { value: "新批注" } });
    fireEvent.click(screen.getByRole("button", { name: "保存批注" }));
    await waitFor(() => {
      expect(reviewApi.addNote).toHaveBeenCalledWith("2026-09-13", "新批注");
    });
  });

  it("趋势切换 7/30", async () => {
    const { reviewApi } = await import("./api");
    vi.mocked(reviewApi.days).mockResolvedValue(daysOut);
    vi.mocked(reviewApi.day).mockResolvedValue(dayDetail);
    vi.mocked(reviewApi.weekly).mockResolvedValue(weekly);
    vi.mocked(reviewApi.trend).mockResolvedValue(trend);

    render(<ReviewApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "30日" })).toBeTruthy();
    });
    fireEvent.click(screen.getByRole("button", { name: "30日" }));
    await waitFor(() => {
      expect(reviewApi.trend).toHaveBeenCalledWith("total", 30);
    });
  });
});
