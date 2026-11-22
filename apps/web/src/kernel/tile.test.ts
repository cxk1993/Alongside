/**
 * `kernel/tile.ts` 单测 —— 只钉「投影规则」与「兜底」两件，不绑实现细节。
 */
import { describe, expect, it } from "vitest";
import type { WindowState } from "./store";
import { GRID_COLS, GRID_ROWS, MIN_TILE_W, defaultLayout, windowToTile, windowsToLayout } from "./tile";

/** 造一个最小可用的窗口状态（只填投影用得到的字段）。 */
function win(over: Partial<WindowState> & Pick<WindowState, "instanceId" | "moduleId">): WindowState {
  return {
    z: 1,
    minimized: false,
    maximized: false,
    geo: { x: 0, y: 0, w: 400, h: 300 },
    pinned: false,
    fixedGeometry: false,
    workspaceId: "ws-1",
    ...over,
  } as WindowState;
}

describe("windowToTile", () => {
  const vp = { w: 1360, h: 720 }; // 1366 视口去掉边距的典型值

  it("整屏窗口 → 铺满 12 列 6 行", () => {
    const t = windowToTile(win({ instanceId: "a", moduleId: "m", geo: { x: 0, y: 0, w: 1360, h: 720 } }), vp);
    expect(t).toMatchObject({ i: "a", moduleId: "m", x: 0, y: 0, w: GRID_COLS, h: GRID_ROWS });
  });

  it("宽度至少 MIN_TILE_W：再窄也不会变成一条缝", () => {
    const t = windowToTile(win({ instanceId: "b", moduleId: "m", geo: { x: 0, y: 0, w: 8, h: 8 } }), vp);
    expect(t.w).toBeGreaterThanOrEqual(MIN_TILE_W);
    expect(t.h).toBeGreaterThanOrEqual(1);
  });

  it("超出右下边界会被夹回网格内（x + w ≤ 12）", () => {
    const t = windowToTile(win({ instanceId: "c", moduleId: "m", geo: { x: 1300, y: 700, w: 600, h: 400 } }), vp);
    expect(t.x + t.w).toBeLessThanOrEqual(GRID_COLS);
    expect(t.y + t.h).toBeLessThanOrEqual(GRID_ROWS);
  });

  it("视口为 0 时不抛错（拿不到容器尺寸的兜底）", () => {
    expect(() => windowToTile(win({ instanceId: "d", moduleId: "m" }), { w: 0, h: 0 })).not.toThrow();
  });

  it("i 沿用 instanceId（迁移时不用另建映射表）", () => {
    const t = windowToTile(win({ instanceId: "keep-me", moduleId: "m" }), vp);
    expect(t.i).toBe("keep-me");
  });
});

describe("windowsToLayout", () => {
  const vp = { w: 1360, h: 720 };
  const list = [
    win({ instanceId: "a", moduleId: "todo", workspaceId: "ws-1" }),
    win({ instanceId: "b", moduleId: "calendar", workspaceId: "ws-2" }),
    win({ instanceId: "c", moduleId: "notes", workspaceId: "ws-1" }),
  ];

  it("给 workspaceId 时只投影那个工作区", () => {
    expect(windowsToLayout(list, vp, "ws-1").map((t) => t.i)).toEqual(["a", "c"]);
  });

  it("不给 workspaceId 时全量投影（保持输入顺序）", () => {
    expect(windowsToLayout(list, vp).map((t) => t.i)).toEqual(["a", "b", "c"]);
  });
});

describe("defaultLayout", () => {
  it("恰好一块，且铺满整张网格（主屏不被删成没有主屏）", () => {
    const l = defaultLayout("home");
    expect(l).toHaveLength(1);
    expect(l[0]).toMatchObject({ moduleId: "home", x: 0, y: 0, w: GRID_COLS, h: GRID_ROWS });
  });

  it("不同模块给不同的 i（避免跨模块撞 id）", () => {
    expect(defaultLayout("home")[0].i).not.toBe(defaultLayout("collab")[0].i);
  });
});