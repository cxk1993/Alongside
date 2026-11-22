import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/shared/api/now", () => ({ useSightNow: vi.fn() }));

import { useSightNow } from "@/shared/api/now";
import type { ModuleReg } from "../../kernel/store";
import { useDesktopStore } from "../../kernel/store";
import type { ModuleManifest } from "../../kernel/types";
import { HomeMyToday } from "./HomeMyToday";
import { FAVS_KEY } from "./homeFavs";

/**
 * 「我的今天」= 工作区 1（人的主屏）。对应用户验收的 5 条里的 ①②③。
 *
 * jsdom 只能验行为（渲染出什么、点了传什么），「一排常用真的能点开窗」还要真浏览器补。
 * 数据一律走**真响应形状**（`/sight/now` 实拉过一份，time/attention/tensions/degraded 都在）。
 */

const sight = useSightNow as unknown as ReturnType<typeof vi.fn>;

function mod(id: string, name: string): ModuleReg {
  const manifest: ModuleManifest = {
    id,
    name,
    kind: "builtin",
    entry: `@/apps/${id}`,
    icon: "grid",
    window: { w: 640, h: 420 },
  } as ModuleManifest;
  return { manifest, enabled: true };
}

const READY = {
  status: "ready" as const,
  data: {
    head: { now_local: "2026-11-22T13:50:38+08:00", weekday: "sun", part_of_day: "noon" },
    attention: [],
    tensions: [
      {
        id: "t1",
        kind: "schedule_conflict",
        summary: "「化学实验课（补做）」与「课题组组会」重叠约 30 分钟",
        severity: "high",
        suggested_action: "把其中一件挪开，或确认是否可以不参加",
      },
    ],
    degraded: [{ source: "finance", reason: "not_implemented" }],
    time: {
      busy_until: null,
      next_event: {
        title: "化学实验课（补做）",
        start_at: "2026-11-22T19:00:00+08:00",
        minutes_until: 309,
        location: null,
      },
      free_slots: [
        { start: "2026-11-22T13:50:43+08:00", end: "2026-11-22T19:00:00+08:00", minutes: 309 },
      ],
    },
  },
};

beforeEach(() => {
  localStorage.clear();
  sight.mockReturnValue(READY);
  useDesktopStore.setState({
    modules: {
      calendar: mod("calendar", "日程表"),
      todo: mod("todo", "待办"),
      habits: mod("habits", "习惯打卡"),
      finance: mod("finance", "理财"),
      notes: mod("notes", "笔记"),
      health: mod("health", "健康"),
    },
  });
});

afterEach(() => {
  // 先卸载再重置 store：HomeTiles（区块区）也订阅了 modules，顺序反了会在 act 外重渲。
  cleanup();
  vi.clearAllMocks();
  useDesktopStore.setState({ modules: {} });
});

describe("我的今天 · 首屏", () => {
  it("打开就有内容：默认 5 个常用 + 下一件事 + 空档（不是两个空态）", () => {
    render(<HomeMyToday />);
    // ★ 默认 5 个：空框子等于没做
    for (const id of ["calendar", "todo", "habits", "finance", "notes"]) {
      expect(screen.getByTestId(`fav-${id}`)).toBeTruthy();
    }
    expect(screen.getByTestId("home-next-title").textContent).toBe("化学实验课（补做）");
    expect(screen.getByTestId("home-slots").textContent).toContain("309");
    expect(screen.getByTestId("home-when").textContent).toContain("周日");
  });

  it("冲突带上「建议怎么办」；降级源如实折叠（不假装已接入）", () => {
    render(<HomeMyToday />);
    expect(screen.getByTestId("home-conflicts").textContent).toContain("重叠约 30 分钟");
    expect(screen.getByTestId("home-conflicts").textContent).toContain("挪开");
    expect(screen.getByText(/1 个数据源未接入/)).toBeTruthy();
  });

  it("404 时说「未接线」，不冒充「今天没事」", () => {
    sight.mockReturnValue({ status: "missing", data: null });
    render(<HomeMyToday />);
    // 状态条与「今天」卡都会说这句话（一处说时间、一处说数据），出现两次是对的
expect(screen.getAllByText(/日程层未接线/).length).toBeGreaterThan(0);
  });
});

describe("我的今天 · 常用", () => {
  it("点常用 = 开对应模块的窗（传 id，不传名字）", () => {
    const open = vi.fn();
    useDesktopStore.setState({ openWindow: open });
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("fav-calendar"));
    expect(open).toHaveBeenCalledWith("calendar");
  });

  it("＋ 勾选可以加，且写进 alongside.favs.v1", () => {
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("fav-add"));
    fireEvent.click(screen.getByTestId("pick-health"));
    expect(screen.getByTestId("fav-health")).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(FAVS_KEY) as string)).toContain("health");
  });

  it("× 移除后不再出现，存储同步", () => {
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("fav-remove-todo"));
    expect(screen.queryByTestId("fav-todo")).toBeNull();
    expect(JSON.parse(localStorage.getItem(FAVS_KEY) as string)).not.toContain("todo");
  });

  it("已停用/已卸载的常用 id 不显示（点不开的按钮比没有更糟）", () => {
    localStorage.setItem(FAVS_KEY, JSON.stringify(["calendar", "ghost"]));
    render(<HomeMyToday />);
    expect(screen.getByTestId("fav-calendar")).toBeTruthy();
    expect(screen.queryByTestId("fav-ghost")).toBeNull();
  });

  it("上限 8 之后不能再勾（多了就是第二个 Dock）", () => {
    localStorage.setItem(
      FAVS_KEY,
      JSON.stringify(["calendar", "todo", "habits", "finance", "notes", "health"].concat(["a", "b"])),
    );
    useDesktopStore.setState({
      modules: {
        ...useDesktopStore.getState().modules,
        a: mod("a", "甲"),
        b: mod("b", "乙"),
        stats: mod("stats", "统计"),
      },
    });
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("fav-add"));
    expect((screen.getByTestId("pick-stats") as HTMLInputElement).disabled).toBe(true);
  });
});

describe("我的今天 · 副屏入口", () => {
  it("只有一个工作区时，「AI 工作面」会新建并切过去", () => {
    const create = vi.fn();
    useDesktopStore.setState({ workspaces: [{ id: "ws1", name: "工作区 1", topZ: 10, topPinZ: 1000, seq: 0 }], createWorkspace: create });
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("home-to-ai"));
    expect(create).toHaveBeenCalled();
  });

  it("已有第二个工作区时，直接切过去（不重复新建）", () => {
    const sw = vi.fn();
    const create = vi.fn();
    useDesktopStore.setState({
      workspaces: [
        { id: "ws1", name: "工作区 1", topZ: 10, topPinZ: 1000, seq: 0 },
        { id: "ws2", name: "工作区 2", topZ: 10, topPinZ: 1000, seq: 0 },
      ],
      switchWorkspace: sw,
      createWorkspace: create,
    });
    render(<HomeMyToday />);
    fireEvent.click(screen.getByTestId("home-to-ai"));
    expect(sw).toHaveBeenCalledWith("ws2");
    expect(create).not.toHaveBeenCalled();
  });
});