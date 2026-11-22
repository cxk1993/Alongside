/**
 * 学习软件聚合入口 —— 只验「看见 / 启用 / 打开 / 同步」四件事，
 * 不 mock 内部实现（PluginContext 与 pluginHost 都是边界）。
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const usePlugins = vi.fn();
vi.mock("@/kernel/plugins/PluginContext", () => ({
  usePlugins: () => usePlugins(),
}));
const open = vi.fn();
vi.mock("@/kernel/ModuleRegistry", () => ({
  pluginHost: { open: (...args: unknown[]) => open(...args) },
}));
vi.mock("@/shared/api/client", () => ({
  api: { post: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

import { api } from "@/shared/api/client";
import { StudyHubApp } from "./StudyHubApp";

const basePlugin = (id: string, name: string, enabled: boolean) => ({
  id,
  name,
  version: "0.1.0",
  kind: "third-party",
  enabled,
  source: "third-party",
  manifest: { entry: "x" },
});

const allPlugins = [
  basePlugin("mooc", "中国大学MOOC", true),
  basePlugin("ucampus", "U校园", false),
  basePlugin("zhidao", "知到", true),
  basePlugin("welearn", "WE Learn", true),
  basePlugin("yuketang", "雨课堂", true),
  basePlugin("sdu-feeds", "山大通知聚合", true),
  basePlugin("pi-agent", "Pi 智能体", true),
];

describe("学习软件聚合入口", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePlugins.mockReturnValue({
      plugins: allPlugins,
      loading: false,
      enable: vi.fn().mockResolvedValue(undefined),
      refresh: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("7 个学习入口全部出现，启用态如实显示", () => {
    render(<StudyHubApp />);
    expect(screen.getAllByTestId(/^study-card-/)).toHaveLength(7);
    expect(screen.getByTestId("study-card-ucampus").textContent).toContain("未启用");
    expect(screen.getByTestId("study-card-mooc").textContent).toContain("已启用");
  });

  it("内嵌站点默认新标签打开；「窗口内」才走插件窗口", () => {
    const win = vi.spyOn(window, "open").mockReturnValue(null);
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-mooc").querySelector("button") as HTMLButtonElement);
    expect(win).toHaveBeenCalledWith("https://www.icourse163.org/", "_blank", "noopener,noreferrer");
    expect(open).not.toHaveBeenCalledWith("mooc");
    fireEvent.click(within(screen.getByTestId("study-card-mooc")).getByText("窗口内"));
    expect(open).toHaveBeenCalledWith("mooc");
  });

  it("需要会话的插件：配置表单保存到 /session（凭据不进消息）", async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true, courses: 3, first_course: "大学英语" });
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-welearn").querySelectorAll("button")[1] as HTMLButtonElement);
    const input = screen.getByPlaceholderText(/DevTools → Cookies/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: ".AspNet.Cookies=abc; ASP.NET_SessionId=def" } });
    fireEvent.click(screen.getByText("保存并校验"));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/api/v1/welearn/session", {
        cookie: ".AspNet.Cookies=abc; ASP.NET_SessionId=def",
      }),
    );
    await waitFor(() => expect(screen.getByTestId("study-card-welearn").textContent).toContain("课程 3"));
  });

  it("纯 API 的已启用 → 调各自 /sync，并把结果如实回显", async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ created: 3, skipped: 1 });
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-sdu-feeds").querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/sdu-feeds/sources/seed", {}));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/sdu-feeds/sync", {}));
    await waitFor(() => expect(screen.getByTestId("study-card-sdu-feeds").textContent).toContain("created 3"));
  });

  it("未启用的同步插件先启用（不直接同步）", async () => {
    const enable = vi.fn().mockResolvedValue(undefined);
    usePlugins.mockReturnValue({
      plugins: allPlugins.map((p) => (p.id === "welearn" ? { ...p, enabled: false } : p)),
      loading: false,
      enable,
      refresh: vi.fn().mockResolvedValue(undefined),
    });
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-welearn").querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(enable).toHaveBeenCalledWith("welearn"));
    // 一键启用后继续尝试同步（未配会话时由服务端如实报错），这是预期流程。
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/welearn/sync", {}));
  });
});