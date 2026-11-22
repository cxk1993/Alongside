/**
 * 学习软件聚合入口 —— 只验「看见 / 启用 / 打开 / 同步」四件事，
 * 不 mock 内部实现（PluginContext 与 pluginHost 都是边界）。
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  it("有窗口的已启用 → 直接打开对应插件", () => {
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-mooc").querySelector("button") as HTMLButtonElement);
    expect(open).toHaveBeenCalledWith("mooc");
  });

  it("纯 API 的已启用 → 调各自 /sync，并把结果如实回显", async () => {
    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ created: 3, skipped: 1 });
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-sdu-feeds").querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/sdu-feeds/sources/seed", {}));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/sdu-feeds/sync", {}));
    await waitFor(() => expect(screen.getByTestId("study-card-sdu-feeds").textContent).toContain("created 3"));
  });

  it("未启用的插件先启用（不直接打开）", async () => {
    const enable = vi.fn().mockResolvedValue(undefined);
    usePlugins.mockReturnValue({
      plugins: allPlugins,
      loading: false,
      enable,
      refresh: vi.fn().mockResolvedValue(undefined),
    });
    render(<StudyHubApp />);
    fireEvent.click(screen.getByTestId("study-card-ucampus").querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(enable).toHaveBeenCalledWith("ucampus"));
    expect(open).not.toHaveBeenCalledWith("ucampus");
  });
});