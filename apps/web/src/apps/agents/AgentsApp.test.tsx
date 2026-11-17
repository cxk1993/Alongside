/** AI 编排前端测试。网络全 mock。 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import AgentsApp from "./AgentsApp";
import AgentRow from "./AgentRow";
import DashboardCard from "./slots/DashboardCard";
import type { Agent } from "./api";

const agent: Agent = {
  id: "a1",
  name: "研究员",
  description: "负责调研",
  capabilities: ["search"],
  load: 0,
  enabled: true,
  callback_url: null,
  last_seen: null,
  created_at: "2026-09-17T00:00:00+00:00",
  updated_at: "2026-09-17T00:00:00+00:00",
};

vi.mock("@/shared/api/events", () => ({
  usePluginEvent: () => {},
}));

vi.mock("./api", () => ({
  agentsApi: {
    list: vi.fn().mockResolvedValue([]),
    create: vi.fn().mockResolvedValue({ id: "new", name: "x", enabled: true }),
    update: vi.fn().mockResolvedValue({ id: "a1", enabled: false }),
    remove: vi.fn().mockResolvedValue(undefined),
    listTasks: vi.fn().mockResolvedValue([]),
    summary: vi.fn().mockResolvedValue({
      tasks_total: 0,
      draft: 0,
      queued: 0,
      running: 0,
      done: 0,
      failed: 0,
      cancelled: 0,
      agents_total: 0,
      agents_enabled: 0,
    }),
  },
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function renderRow(a: Agent) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AgentRow agent={a} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AgentsApp 冒烟", () => {
  it("空态显示添加入口", async () => {
    render(<AgentsApp />, { wrapper: makeWrapper() });
    expect(screen.getByLabelText("智能体名称")).toBeTruthy();
    expect(screen.getByLabelText("智能体描述")).toBeTruthy();
    expect(screen.getByRole("button", { name: "添加" })).toBeTruthy();
  });

  it("输入后提交 create", async () => {
    const { agentsApi } = await import("./api");
    render(<AgentsApp />, { wrapper: makeWrapper() });
    fireEvent.change(screen.getByLabelText("智能体名称"), {
      target: { value: "写作助手" },
    });
    fireEvent.change(screen.getByLabelText("智能体描述"), {
      target: { value: "写文案" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => {
      expect(agentsApi.create).toHaveBeenCalledWith({
        name: "写作助手",
        description: "写文案",
      });
    });
  });

  it("列表渲染 agent 与启用进度", async () => {
    const { agentsApi } = await import("./api");
    vi.mocked(agentsApi.list).mockResolvedValue([
      agent,
      { ...agent, id: "a2", name: "写作助手", enabled: false },
    ]);
    render(<AgentsApp />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByText("研究员")).toBeTruthy();
      expect(screen.getByText("写作助手")).toBeTruthy();
    });
    expect(screen.getByText(/启用 1\/2/)).toBeTruthy();
  });
});

describe("AgentRow", () => {
  it("渲染名称、描述、能力与状态", () => {
    renderRow(agent);
    expect(screen.getByText("研究员")).toBeTruthy();
    expect(screen.getByText("负责调研")).toBeTruthy();
    expect(screen.getByText("#search")).toBeTruthy();
    expect(screen.getByText("启用")).toBeTruthy();
  });

  it("停用态显示停用标签", () => {
    renderRow({ ...agent, enabled: false });
    expect(screen.getByText("停用")).toBeTruthy();
  });

  it("点击停用调用 update enabled=false", async () => {
    const { agentsApi } = await import("./api");
    renderRow(agent);
    fireEvent.click(screen.getByRole("button", { name: "停用 研究员" }));
    await waitFor(() => {
      expect(agentsApi.update).toHaveBeenCalledWith("a1", { enabled: false });
    });
  });

  it("停用态点击启用调用 update enabled=true", async () => {
    const { agentsApi } = await import("./api");
    renderRow({ ...agent, enabled: false });
    fireEvent.click(screen.getByRole("button", { name: "启用 研究员" }));
    await waitFor(() => {
      expect(agentsApi.update).toHaveBeenCalledWith("a1", { enabled: true });
    });
  });

  it("删除需 confirm，确认后调用 remove", async () => {
    const { agentsApi } = await import("./api");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRow(agent);
    fireEvent.click(screen.getByRole("button", { name: "删除 研究员" }));
    await waitFor(() => {
      expect(agentsApi.remove).toHaveBeenCalledWith("a1");
    });
    confirmSpy.mockRestore();
  });
});

describe("DashboardCard", () => {
  it("空数据显示占位", async () => {
    render(<DashboardCard />, { wrapper: makeWrapper() });
    expect(screen.getByText("AI 编排")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("还没有智能体 / 任务")).toBeTruthy();
    });
  });

  it("有 summary 时展示启用与任务完成", async () => {
    const { agentsApi } = await import("./api");
    vi.mocked(agentsApi.summary).mockResolvedValue({
      tasks_total: 5,
      draft: 1,
      queued: 1,
      running: 1,
      done: 2,
      failed: 0,
      cancelled: 0,
      agents_total: 3,
      agents_enabled: 2,
    });
    render(<DashboardCard />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByText("2/3")).toBeTruthy();
    });
    expect(screen.getByText("2/5")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
  });
});
