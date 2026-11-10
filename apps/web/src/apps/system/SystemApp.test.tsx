/**
 * 系统窗判据测试（V6·主人⑫）：七页签编排 + 懒挂载 + auth 薄壳语义。
 *
 * ★ 2026-11-09 起页签**只从 manifest.tabs 来**（`DEFAULT_TABS` 已删）：
 *   夹具直接把 `services/api/modules/system/manifest.json` 的 tabs 抄进来。
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";

import SystemApp from "./SystemApp";
import { useDesktopStore } from "@/kernel/store";

/** 与 `services/api/modules/system/manifest.json` 的 tabs 同形同序。 */
const SYSTEM_TABS = [
  { key: "catalog", label: "能力目录", entry: "catalog" },
  { key: "mcp", label: "MCP Server", entry: "mcp" },
  { key: "push", label: "推送", entry: "push" },
  { key: "export", label: "导出中心", entry: "export" },
  { key: "query", label: "跨区块查询", entry: "query" },
  { key: "plugins", label: "插件管理", entry: "plugins" },
  { key: "auth", label: "账户与鉴权" },
];

function setSystemTabs(tabs: typeof SYSTEM_TABS): void {
  useDesktopStore.setState({
    modules: {
      system: {
        manifest: {
          id: "system",
          name: "系统",
          version: "0.1.0",
          kind: "container",
          entry: "@/apps/system",
          tabs,
        },
        enabled: true,
      },
    } as unknown as ReturnType<typeof useDesktopStore.getState>["modules"],
  });
}

describe("SystemApp（V6 系统窗）", () => {
  beforeEach(() => {
    // 注册表带 system manifest（页签唯一真相源）；子模块不注册 ⇒ 有 entry 但无 App 页落薄壳。
    setSystemTabs(SYSTEM_TABS);
    // persistKey 持久化跨用例泄漏防护（每用例从干净 localStorage 起步）
    localStorage.clear();
  });
  it("渲染七个一级页签：目录/MCP/推送/导出/跨查询/插件管理/鉴权", () => {
    render(<SystemApp />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(7);
    expect(screen.getByTestId("mtab-tab-catalog")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-mcp")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-push")).toBeTruthy();
    // ★ 主人 2026-09-25：「导出中心」并入系统窗多页
    expect(screen.getByTestId("mtab-tab-export")).toBeTruthy();
    // ★ 主人 2026-09-25：「跨区块查询」「插件管理」并入系统窗多页
    expect(screen.getByTestId("mtab-tab-query")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-plugins")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-auth")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-catalog").getAttribute("aria-selected")).toBe("true");
  });

  it("auth 页为「已接入（后端服务）」薄壳语义（V5 口径，非「尚未接入」）", async () => {
    render(<SystemApp />);
    fireEvent.click(screen.getByTestId("mtab-tab-auth"));
    const page = await screen.findByTestId("system-backend-page-auth");
    expect(page.textContent).toContain("已接入（后端服务）");
    expect(page.textContent).not.toContain("尚未接入");
  });

  it("keep-alive：切到 auth 后切回 catalog，auth 面板仍挂载（hidden）", async () => {
    render(<SystemApp />);
    expect(screen.queryByTestId("mtab-panel-auth")).toBeNull();
    fireEvent.click(screen.getByTestId("mtab-tab-auth"));
    await screen.findByTestId("system-backend-page-auth");
    fireEvent.click(screen.getByTestId("mtab-tab-catalog"));
    await waitFor(() => {
      expect(screen.getByTestId("mtab-panel-catalog").hidden).toBe(false);
    });
    // auth 面板仍挂载（hidden），keep-alive 生效
    expect(screen.getByTestId("mtab-panel-auth").hidden).toBe(true);
  });

  it("tabs 配置面：manifest 带 tabs 声明时按配置渲染，无 entry 页落薄壳语义", () => {
    useDesktopStore.setState({
      modules: {
        system: {
          manifest: {
            id: "system",
            name: "系统",
            version: "0.1.0",
            kind: "builtin",
            // 容器型模块 schema 落地后的声明形状（最小面）：
            tabs: [
              { key: "mcp", label: "MCP Server", entry: "mcp" },
              { key: "auth", label: "账户与鉴权" },
            ],
          },
          enabled: true,
        },
      } as unknown as ReturnType<typeof useDesktopStore.getState>["modules"],
    });
    render(<SystemApp />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(2);
    expect(screen.getByTestId("mtab-tab-mcp")).toBeTruthy();
    expect(screen.getByTestId("mtab-tab-auth")).toBeTruthy();
    // 无 entry 页 = 薄壳语义，不是「尚未接入」
    fireEvent.click(screen.getByTestId("mtab-tab-auth"));
    expect(screen.getByTestId("system-backend-page-auth").textContent).toContain("已接入（后端服务）");
  });
});
