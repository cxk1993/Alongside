/**
 * 区块模型（Tile）—— 从「浮窗」迁到「网格区块」的第一块基石。
 *
 * ★ 这一版**只做纯加法**：
 *   · 定义 `TileState`（网格坐标）
 *   · 提供 `windowToTile()` 的单向投影（WindowState → TileState）
 *   · **不动 store.ts 的任何字段、不改任何渲染** —— 随时可以整文件丢弃而不影响现有行为
 *
 * ★ 为什么新开文件而不写进 store.ts：store 是共享核心（凝析 + 比丢都在盯着），
 *   这一版要能被单独 revert，不牵连任何现有代码。
 *
 * ★ 关于「像素 → 网格」：**不做数据迁移**（凝析 17:1x 的结论）。
 *   像素 geo 依赖当时的视口宽（x=1240 在 1366 与 2560 下语义相反），换算都是猜。
 *   这个投影函数只用于**运行时**（②阶段 TileState 从 WindowState 派生），不写回磁盘。
 *
 * 设计依据：`team/26-store改造·分步实施-20261122.md`
 */
import type { WindowState } from "./store";

/** 网格坐标（单位 = 格，不是像素）。 */
export interface TileState {
  /** 稳定 id：沿用窗口的 `instanceId`，迁移时不用另建映射表。 */
  i: string;
  moduleId: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 12 列（RGL 默认；1366 下每格约 100px）。 */
export const GRID_COLS = 12;

/** 行数：RGL 的 `rowHeight` 由容器高反算（凝析 spike：固定 rowHeight 会空出 22.5%）。 */
export const GRID_ROWS = 6;

/** 最小块尺寸（格）：防止缩成一条缝。 */
export const MIN_TILE_W = 2;
export const MIN_TILE_H = 1;

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

/**
 * 把一个「像素浮窗」投影成「网格区块」。
 *
 * @param w        窗口状态（只读 `geo` / `instanceId` / `moduleId`）
 * @param viewport 当前可用视口（一般是 `.wall` 的 `clientWidth/clientHeight`，不是 `innerWidth`）
 *
 * ★ 只用于运行时投影；**不要拿它做数据迁移**（见文件头注）。
 */
export function windowToTile(w: WindowState, viewport: { w: number; h: number }): TileState {
  const vw = Math.max(1, viewport.w);
  const vh = Math.max(1, viewport.h);
  const cellW = vw / GRID_COLS;

  const wCells = clamp(Math.round(w.geo.w / cellW), MIN_TILE_W, GRID_COLS);
  const xCells = clamp(Math.round(w.geo.x / cellW), 0, GRID_COLS - wCells);

  // 行高由容器高反算：与正式实现（RGL rowHeight = floor((H - pad*2 - margin*(rows-1)) / rows)）同口径
  const cellH = vh / GRID_ROWS;
  const hCells = clamp(Math.round(w.geo.h / cellH), MIN_TILE_H, GRID_ROWS);
  const yCells = clamp(Math.round(w.geo.y / cellH), 0, GRID_ROWS - hCells);

  return {
    i: w.instanceId,
    moduleId: w.moduleId,
    x: xCells,
    y: yCells,
    w: wCells,
    h: hCells,
  };
}

/**
 * 把一组窗口投影成布局（保持输入顺序，便于 RGL 的 `layout` 数组）。
 * 只投影指定工作区的窗口（`workspaceId` 缺省 = 不过滤）。
 */
export function windowsToLayout(
  windows: readonly WindowState[],
  viewport: { w: number; h: number },
  workspaceId?: string,
): TileState[] {
  return windows
    .filter((w) => (workspaceId ? w.workspaceId === workspaceId : true))
    .map((w) => windowToTile(w, viewport));
}

/**
 * 默认布局：没有任何窗口时，给一块「主区」。
 *
 * ★ 这是 P1 的兜底 —— 凝析的约束：「布局独立 ≠ 主屏可以被删成没有主屏」。
 * 所以任何工作区最少有这一块。
 */
export function defaultLayout(primaryModuleId: string): TileState[] {
  return [
    { i: `default:${primaryModuleId}`, moduleId: primaryModuleId, x: 0, y: 0, w: GRID_COLS, h: GRID_ROWS },
  ];
}