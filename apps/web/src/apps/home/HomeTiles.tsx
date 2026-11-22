import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import { GRID_COLS, type TileState } from "../../kernel/tile";
import { useDesktopStore } from "../../kernel/store";
import { requestMtabPage } from "../../kernel/MultitabFrame";
import { useCalendarUI } from "../calendar/state";
import type { ViewMode } from "../calendar/state";
import { useTodoUI } from "../todo/state";
import { loadFavs } from "./homeFavs";
import "./homeTiles.css";

/**
 * 区块区（P1 原型）—— 主墙内部的**独立容器**。
 *
 * 边界（团队裁定 2026-11-22 20:40）：
 *   · **不替换**「今天 / 欠的 / 该注意的」三块卡——它们是现在唯一能用的内容；
 *   · 布局先走**内存态**（不做持久化），形态定下来再接 store；
 *   · 自写 pointer 拖拽（不上 RGL：2.3.0 根导出缺 WidthProvider + 行为面变动 + rowHeight 反算）。
 *
 * 三条底线（spike 实测，缺一条就是「拖不动但看不出为什么」）：
 *   ① 拖动时整块 canvas 盖 `inset:0` 遮罩——否则指针一进 iframe/子内容，父文档再也收不到
 *      pointermove（实测三次 move 只跟到第 1 次，移出才恢复）；
 *   ② **拖把手**（24×24）而不是整卡拖——整卡拖时 press 落在 iframe/canvas 上就进不了父文档；
 *   ③ **编辑态才可拖**——浏览态把手根本不在 DOM 里，没有误触的可能。
 */

/** 网格列数复用 `kernel/tile.ts` 的单一真源（区块模型基石，5fdc8d7）。 */
export const TILE_COLS = GRID_COLS;
/** 一行高度（px）。canvas 高 = 行数 × 它；格↔像素只在这一个常量上换算。 */
export const TILE_ROW_H = 76;
/** canvas 固定 4 行（初始 3 块占 2 行）。 */
export const TILE_ROWS = 4;
/** 一块默认占 4 列 × 2 行（12 列 → 一排 3 块）。 */
export const TILE_W = 4;
export const TILE_H = 2;
/** **初始**铺几块：5 块 = 2 行 × 3 列留一格（那一格是「＋ 添加区块」槽，团队 2026-11-22 选 B）。
 *  刻意留空位——没有空位的网格「怎么拖都弹回」，用户会读成「拖拽坏了」。
 *  ⚠️ 这是**初始**数量，不是上限：上限见 `TILE_CAPACITY`（网格容量）。 */
export const TILE_MAX = 5;
/** 网格容量：默认 4×2 的块在 12 列 × 4 行的网格里最多 6 块（2 行 × 3 列）。 */
export const TILE_CAPACITY = Math.floor(TILE_ROWS / TILE_H) * Math.floor(TILE_COLS / TILE_W);
/** 缩放的最小尺寸：1 行 × 3 列（再小就装不下「名字 + 一句能力」）。 */
export const TILE_MIN_W = 3;
export const TILE_MIN_H = 1;

/**
 * 能力说明（一句话「这块能干什么」）。
 *
 * 为什么需要：只有 `名字 + moduleId` 两块字时，整片区块会被读成「占位符」（视觉复核判定
 * 40-50% 完成度）。内容层最便宜的补法是**说清能力**——不是放假数据：这里不出现条数、
 * 金额、日期，那些必须等真数据接进来。
 *
 * 口径：只写该模块**当眼能做的事**（按各自的 App 实现核对过），不写规划中的功能。
 * 未登记 id（第三方插件、注册顺序补位的内核模块）→ 不渲染说明行，不编。
 */
export const TILE_DESC: Record<string, string> = {
  calendar: "按天排日程与安排",
  todo: "收拢散落的待办",
  habits: "打卡与连续天数",
  finance: "BeeCount 账本 + 本地流水",
  notes: "随手记与按词检索",
  schedule: "日程 + 待办，一窗两页",
  course: "一周课表",
  knowledge: "复盘 / 笔记 / 文档 合一",
  diary: "月历 + 当天记录",
  growth: "习惯 / 人格 / 健康 多页",
  health: "症状、用药与体检记录",
  review: "按日复盘与时长",
  docs: "文档树与正文",
  dashboard: "各模块卡片汇总",
};

/**
 * 子入口（深链接）—— 区块上直接给出「这块里最常用的那一页」。
 *
 * 三条口径（对应团队 2026-11-22 的四条约束）：
 *   ① 只登记代码里**确实存在**的页签 / 筛选器，不登记规划中的功能，不造数据；
 *   ② 点了必须真切模块状态（开窗 + 切页 / 切筛选），不做「开同一个窗了事」；
 *   ③ 没有真入口的模块不登记 —— 说明行（TILE_DESC）已经说了它能干什么，宁缺不编。
 *
 * 现状核对（2026-11-22）：
 *   · finance：`FinanceTabbedApp` 的 MultitabFrame 两页（beecount / local），persistKey=`finance`；
 *   · todo：`useTodoUI.setFilterTag`（`学业` 是 `TodoApp.tsx` 里的层级标签，那边未导出）；
 *   · calendar：`useCalendarUI.setView` 的 day / week / month 三视图，按钮就在顶上；
 *   · habits：prefs 只在挂载时读一次（外面改 localStorage 不重读）→ 没有真入口，不登记；
 *   · notes：书库 / 搜索是组件内 `useState`，外部设不到 → 不登记。
 */
export interface TileEntry {
  /** 稳定 id：既是 data-testid 后缀，也是分发器的入参。 */
  key: string;
  label: string;
}

export const TILE_ENTRIES: Record<string, TileEntry[]> = {
  finance: [
    { key: "beecount", label: "BeeCount 账本" },
    { key: "local", label: "本地流水" },
  ],
  todo: [
    { key: "study", label: "只看学业" },
    { key: "all", label: "全部" },
  ],
  calendar: [
    { key: "day", label: "日" },
    { key: "week", label: "周" },
    { key: "month", label: "月" },
  ],
};

/** 待办的学业层级标签。与 `apps/todo/TodoApp.tsx` 的 STUDY_TAG 同值（那边未导出）。
 *  将来抽 intent 总线时，这里换成模块自己注册的语义 action 即可，调用面不变。 */
const TODO_STUDY_TAG = "学业";

/**
 * 点一条子入口 = 把模块内部状态切到目标页 / 筛选，再开窗。
 *
 * 顺序有意义：todo / calendar 的 store 是模块级的，先切状态 → 窗还没开也照样生效，
 * 开窗后第一帧就是目标视图（不会闪一下默认页）。
 * finance 走「写持久化 + 广播」两条腿：窗没开 → 新实例读持久化落页；已开 → 事件让现有实例切页。
 */
export function applyTileEntry(moduleId: string, key: string): void {
  switch (moduleId) {
    case "todo":
      useTodoUI.getState().setFilterTag(key === "study" ? TODO_STUDY_TAG : null);
      break;
    case "calendar":
      useCalendarUI.getState().setView(key as ViewMode);
      break;
    case "finance":
      requestMtabPage("finance", key);
      break;
    default:
      /* 未登记子入口的模块：不猜语义 */
      break;
  }
  useDesktopStore.getState().openWindow(moduleId);
}

/** 区块 = `TileState`（网格坐标，单一真源）+ 展示用的名字。
 *  不另造一套坐标形状：P2 接 store 时 `windowToTile()` 的输出能直接喂进来。 */
export interface TileLayout extends TileState {
  name: string;
}

export interface Slot {
  x: number;
  y: number;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export function overlaps(a: TileLayout, b: TileLayout): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/**
 * 落位：`at` 与 `others` 重叠时返回曼哈顿距离最近的空位；无冲突返回 `null`（原地不动）。
 * 纯函数——jsdom 能测，不依赖任何 DOM 量测。
 */
export function resolveSlot(
  at: Slot,
  size: { w: number; h: number },
  others: TileLayout[],
  rows = TILE_ROWS,
): Slot | null {
  const probe = (x: number, y: number): TileLayout => ({
    i: "__probe__",
    moduleId: "",
    name: "",
    x,
    y,
    w: size.w,
    h: size.h,
  });
  if (!others.some((o) => overlaps(probe(at.x, at.y), o))) return null;
  let best: Slot | null = null;
  let bestD = Number.POSITIVE_INFINITY;
  for (let y = 0; y <= rows - size.h; y++) {
    for (let x = 0; x <= TILE_COLS - size.w; x++) {
      const cand = probe(x, y);
      if (others.some((o) => overlaps(cand, o))) continue;
      const d = Math.abs(x - at.x) + Math.abs(y - at.y);
      if (d < bestD) {
        bestD = d;
        best = { x, y };
      }
    }
  }
  return best;
}

/**
 * 缩放落位：请求的尺寸与别人冲突时，退到**曼哈顿距离最近**的合法尺寸（同距离取面积大的）；
 * 一个都放不下 -> null，调用方回原尺寸。纯函数，jsdom 可测。
 *
 * 与 resolveSlot 的分工：拖动改 x/y、缩放改 w/h，两件事不互相代劳。
 * 自己不在 others 里 => 原尺寸永远是候选，所以「长到别人身上」的可预期结果是「长不进去」，
 * 而不是整块弹到别的位置（那是拖动该做的事，缩放时位置不该动）。
 */
export function resolveSize(
  at: Slot,
  req: { w: number; h: number },
  others: TileLayout[],
  rows = TILE_ROWS,
): { w: number; h: number } | null {
  const wMax = TILE_COLS - at.x;
  const hMax = rows - at.y;
  const wantW = clamp(req.w, TILE_MIN_W, wMax);
  const wantH = clamp(req.h, TILE_MIN_H, hMax);
  const probe = (w: number, h: number): TileLayout => ({
    i: "__probe__",
    moduleId: "",
    name: "",
    x: at.x,
    y: at.y,
    w,
    h,
  });
  let best: { w: number; h: number } | null = null;
  let bestD = Number.POSITIVE_INFINITY;
  for (let w = TILE_MIN_W; w <= wMax; w++) {
    for (let h = TILE_MIN_H; h <= hMax; h++) {
      const cand = probe(w, h);
      if (others.some((o) => overlaps(cand, o))) continue;
      const d = Math.abs(w - wantW) + Math.abs(h - wantH);
      const area = w * h;
      if (d < bestD || (d === bestD && area > (best ? best.w * best.h : 0))) {
        bestD = d;
        best = { w, h };
      }
    }
  }
  return best;
}

/** 初始排布：一排 3 块，满了换行。 */
export function initialLayout(mods: { id: string; name: string }[]): TileLayout[] {
  return mods.slice(0, TILE_MAX).map((m, idx) => ({
    i: m.id,
    moduleId: m.id,
    name: m.name,
    x: (idx % 3) * TILE_W,
    y: Math.floor(idx / 3) * TILE_H,
    w: TILE_W,
    h: TILE_H,
  }));
}

/**
 * 空格位：按默认块尺寸（4×2）逐格试放，返回第一个放得下的格子。
 * 添加槽就画在这儿——它是「这块地方还能长东西」的显式信号，不是装饰。
 * 纯函数、jsdom 可测；占位判定与 `resolveSlot` 同口径（同一个 `overlaps`）。
 */
export function firstFreeSlot(tiles: TileLayout[], rows = TILE_ROWS): Slot | null {
  for (let y = 0; y + TILE_H <= rows; y += 1) {
    for (let x = 0; x + TILE_W <= TILE_COLS; x += TILE_W) {
      const probe: TileLayout = {
        i: "__free__",
        moduleId: "",
        name: "",
        x,
        y,
        w: TILE_W,
        h: TILE_H,
      };
      if (!tiles.some((t) => overlaps(probe, t))) return { x, y };
    }
  }
  return null;
}

interface DragState {
  i: string;
  pointerId: number;
  startX: number;
  startY: number;
  origX: number;
  origY: number;
  w: number;
  h: number;
  cellW: number;
}

interface ResizeState {
  i: string;
  pointerId: number;
  startX: number;
  startY: number;
  origX: number;
  origY: number;
  origW: number;
  origH: number;
  cellW: number;
}

export function HomeTiles() {
  const modules = useDesktopStore((s) => s.modules);
  const openWindow = useDesktopStore((s) => s.openWindow);

  /** 已启用模块，顺序 = 注册顺序（与「我的常用」同口径）。 */
  const all = useMemo(() => {
    const out: { id: string; name: string }[] = [];
    for (const reg of Object.values(modules)) {
      if (!reg?.enabled) continue;
      const m = reg.manifest;
      if (!m?.id) continue;
      out.push({ id: m.id, name: m.name || m.id });
    }
    return out;
  }, [modules]);

  /**
   * 铺哪几个模块：**「我的常用」优先**，其余按注册顺序补。
   * 不这么做的话，铺出来的是注册顺序最前的那几个内核类模块（`agents` / `assets` / `auth`），
   * 主墙上摆一排「账户与鉴权」没人想用。
   */
  const candidates = useMemo(() => {
    const favs = loadFavs();
    const byId = new Map(all.map((m) => [m.id, m]));
    const out: { id: string; name: string }[] = [];
    for (const id of favs) {
      const m = byId.get(id);
      if (!m) continue;
      out.push(m);
      byId.delete(id);
    }
    for (const m of byId.values()) out.push(m);
    return out;
  }, [all]);

  const [tiles, setTiles] = useState<TileLayout[]>([]);
  const [editing, setEditing] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const [resizeId, setResizeId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const seeded = useRef(false);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const resizeRef = useRef<ResizeState | null>(null);

  // 模块是异步来的：第一次非空时铺一次，之后不再重置（同一份教训：挂载即跑会被空列表洗掉）。
  useEffect(() => {
    if (seeded.current || candidates.length === 0) return;
    seeded.current = true;
    setTiles(initialLayout(candidates));
  }, [candidates]);

  /** 已上墙的模块 id —— 添加列表里不再出现（同一模块不放两块）。 */
  const placedIds = useMemo(() => new Set(tiles.map((t) => t.moduleId)), [tiles]);
  /** 添加槽位置：网格里第一个还放得下默认块的格子；满了就是 null（不硬造槽）。 */
  const freeSlot = useMemo(() => firstFreeSlot(tiles), [tiles]);
  /**
   * 可添加的模块，**分两段**：先「能当墙面块」的（有 `TILE_DESC` = 我们核对过它能干什么），
   * 再「其它模块」（内核 / 管理类：账户与鉴权、系统、外观……）。
   * 为什么只排序不隐藏：真去掉了就等于替用户做主（宁散勿丢）；但注册顺序在最前面的
   * 恰恰是 `agents` / `assets` / `auth` 这批内核模块，39 条平铺一屏全是它们，
   * 用户会读成「这个入口是给人添乱的」。
   */
  const addable = useMemo(() => {
    const rest = candidates.filter((m) => !placedIds.has(m.id));
    return {
      skill: rest.filter((m) => TILE_DESC[m.id]),
      others: rest.filter((m) => !TILE_DESC[m.id]),
      total: rest.length,
    };
  }, [candidates, placedIds]);

  /** 加一块到空格位。满了 / 重复模块 -> 不动（不做静默替换，也不编一个位置）。 */
  const addTile = (m: { id: string; name: string }) => {
    setTiles((list) => {
      if (list.length >= TILE_CAPACITY) return list;
      if (list.some((t) => t.moduleId === m.id)) return list;
      const slot = firstFreeSlot(list);
      if (!slot) return list;
      return [
        ...list,
        { i: m.id, moduleId: m.id, name: m.name, x: slot.x, y: slot.y, w: TILE_W, h: TILE_H },
      ];
    });
    setAdding(false);
  };

  const pickRow = (list: { id: string; name: string }[]) =>
    list.map((m) => (
      <li key={m.id}>
        <button
          type="button"
          className="home-tiles__pick-item"
          onClick={() => addTile(m)}
          data-testid={`add-tile-${m.id}`}
        >
          <span className="home-tiles__pick-name">{m.name}</span>
          {TILE_DESC[m.id] ? <span className="home-tiles__pick-desc">{TILE_DESC[m.id]}</span> : null}
        </button>
      </li>
    ));

  // Esc 收起模块列表——键盘路径不能只剩鼠标一条。
  useEffect(() => {
    if (!adding) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAdding(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [adding]);

  const onGripDown = (e: ReactPointerEvent<HTMLButtonElement>, t: TileLayout) => {
    if (!editing || e.button > 0) return;
    e.preventDefault();
    e.stopPropagation();
    const canvas = canvasRef.current;
    // jsdom 里 clientWidth = 0：给个兜底值，让纯逻辑可测（真浏览器永远走第一条）。
    const cellW = canvas && canvas.clientWidth > 0 ? canvas.clientWidth / TILE_COLS : 100;
    dragRef.current = {
      i: t.i,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      origX: t.x,
      origY: t.y,
      w: t.w,
      h: t.h,
      cellW,
    };
    setDragId(t.i);
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* jsdom 未实现 —— 鼠标路径由 window 监听兜底 */
    }
  };

  /** 缩放入口：与拖把手同一条规矩（独立 24x24 按钮、编辑态才存在、touch-action:none）。 */
  const onResizeDown = (e: ReactPointerEvent<HTMLButtonElement>, t: TileLayout) => {
    if (!editing || e.button > 0) return;
    e.preventDefault();
    e.stopPropagation();
    const canvas = canvasRef.current;
    const cellW = canvas && canvas.clientWidth > 0 ? canvas.clientWidth / TILE_COLS : 100;
    resizeRef.current = {
      i: t.i,
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      origX: t.x,
      origY: t.y,
      origW: t.w,
      origH: t.h,
      cellW,
    };
    setResizeId(t.i);
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* jsdom 未实现 —— 鼠标路径由 window 监听兜底 */
    }
  };

  // 拖动：监听挂 window（capture 只是保险），落位在 pointerup 时一次性解冲突。
  useEffect(() => {
    if (!dragId) return;
    const onMove = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      const nx = clamp(d.origX + Math.round((e.clientX - d.startX) / d.cellW), 0, TILE_COLS - d.w);
      const ny = clamp(
        d.origY + Math.round((e.clientY - d.startY) / TILE_ROW_H),
        0,
        TILE_ROWS - d.h,
      );
      setTiles((list) => list.map((t) => (t.i === d.i ? { ...t, x: nx, y: ny } : t)));
    };
    const onUp = (e: PointerEvent) => {
      const d = dragRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      dragRef.current = null;
      setDragId(null);
      setTiles((list) => {
        const me = list.find((t) => t.i === d.i);
        if (!me) return list;
        const others = list.filter((t) => t.i !== d.i);
        // 先判「真冲突」：resolveSlot 的 null 有两种含义（无冲突 / 找不到空位），必须分开。
        const collide = others.some((o) => overlaps(me, o));
        if (!collide) return list;
        const slot = resolveSlot({ x: me.x, y: me.y }, { w: me.w, h: me.h }, others);
        const target = slot ?? { x: d.origX, y: d.origY }; // 整张网格都没有空位 → 回原位
        return list.map((t) => (t.i === d.i ? { ...t, x: target.x, y: target.y } : t));
      });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [dragId]);

  // 缩放：同一套 window 监听与同一块遮罩；拖动中允许视觉上压别人，只在松手时解冲突。
  useEffect(() => {
    if (!resizeId) return;
    const onMove = (e: PointerEvent) => {
      const d = resizeRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      const nw = clamp(
        d.origW + Math.round((e.clientX - d.startX) / d.cellW),
        TILE_MIN_W,
        TILE_COLS - d.origX,
      );
      const nh = clamp(
        d.origH + Math.round((e.clientY - d.startY) / TILE_ROW_H),
        TILE_MIN_H,
        TILE_ROWS - d.origY,
      );
      setTiles((list) => list.map((t) => (t.i === d.i ? { ...t, w: nw, h: nh } : t)));
    };
    const onUp = (e: PointerEvent) => {
      const d = resizeRef.current;
      if (!d || e.pointerId !== d.pointerId) return;
      resizeRef.current = null;
      setResizeId(null);
      setTiles((list) => {
        const me = list.find((t) => t.i === d.i);
        if (!me) return list;
        const others = list.filter((t) => t.i !== d.i);
        // 长到别人身上 -> 退到最近合法尺寸；一个都放不下 -> 回原尺寸。
        // 位置（x/y）不归缩放管，所以这里绝不调用 resolveSlot。
        const size = resolveSize({ x: me.x, y: me.y }, { w: me.w, h: me.h }, others);
        const target = size ?? { w: d.origW, h: d.origH };
        return list.map((t) => (t.i === d.i ? { ...t, w: target.w, h: target.h } : t));
      });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [resizeId]);

  /** 键盘等价物：编辑态下 Alt+←/→/↑/↓ 挪一格（不能只有鼠标能排）。 */
  const nudge = (t: TileLayout, dx: number, dy: number) => {
    setTiles((list) =>
      list.map((o) =>
        o.i === t.i
          ? {
              ...o,
              x: clamp(o.x + dx, 0, TILE_COLS - o.w),
              y: clamp(o.y + dy, 0, TILE_ROWS - o.h),
            }
          : o,
      ),
    );
  };

  /** 键盘等价物 · 缩放：Alt+Shift+方向键改一格尺寸（同样走 resolveSize，不会压别人）。 */
  const resizeNudge = (t: TileLayout, dw: number, dh: number) => {
    setTiles((list) => {
      const others = list.filter((o) => o.i !== t.i);
      return list.map((o) => {
        if (o.i !== t.i) return o;
        const want = {
          w: clamp(o.w + dw, TILE_MIN_W, TILE_COLS - o.x),
          h: clamp(o.h + dh, TILE_MIN_H, TILE_ROWS - o.y),
        };
        const size = resolveSize({ x: o.x, y: o.y }, want, others);
        return size ? { ...o, w: size.w, h: size.h } : o;
      });
    });
  };

  /**
   * 拖动中的落位预览：与松手时**同一个** `resolveSlot`，不是另写一套「看起来差不多」的算法。
   * 预览与落位不一致，比没有预览更坏——用户会记住那一帧。
   */
  const ghost = useMemo(() => {
    if (!dragId) return null;
    const me = tiles.find((t) => t.i === dragId);
    if (!me) return null;
    const others = tiles.filter((t) => t.i !== dragId);
    if (!others.some((o) => overlaps(me, o))) return null;
    return resolveSlot({ x: me.x, y: me.y }, { w: me.w, h: me.h }, others);
  }, [dragId, tiles]);

  const dragName = dragId ? tiles.find((t) => t.i === dragId)?.name : undefined;
  const resizeName = resizeId ? tiles.find((t) => t.i === resizeId)?.name : undefined;
  const busy = Boolean(dragId || resizeId);
  const hint = editing
    ? "拖右上角移动、拖右下角缩放；Alt / Alt+Shift + 方向键也能挪 / 缩"
    : tiles.length
      ? `${tiles.length} 块 · 点开即用`
      : "";

  return (
    <section
      className={`home-tiles${editing ? " is-editing" : ""}${dragId ? " is-dragging" : ""}${
        resizeId ? " is-resizing" : ""
      }`}
      aria-label="区块区"
      data-testid="home-tiles"
    >
      <header className="home-tiles__head">
        <h2 className="home-tiles__title">区块</h2>
        <span className="home-tiles__hint" data-testid="home-tiles-hint">
          {hint}
        </span>
        <button
          type="button"
          className="home-tiles__edit"
          aria-pressed={editing}
          onClick={() => setEditing((v) => !v)}
          data-testid="home-tiles-edit"
        >
          {editing ? "完成" : "编辑"}
        </button>
      </header>

      <div
        className="home-tiles__canvas"
        ref={canvasRef}
        data-testid="home-tiles-canvas"
        style={
          {
            height: TILE_ROWS * TILE_ROW_H,
            "--home-tile-row": `${TILE_ROW_H}px`,
          } as CSSProperties
        }
      >
        {tiles.map((t) => {
          const isDrag = t.i === dragId;
          const isResize = t.i === resizeId;
          // 拖动中是否压到别人（松手会被挪到最近空位）——诚实提示，不是装饰。
          const colliding =
            isDrag && tiles.some((o) => o.i !== t.i && overlaps(t, o));
          const entries = TILE_ENTRIES[t.moduleId] ?? [];
          return (
            <article
              key={t.i}
              className={`home-tile${isDrag ? " is-dragging" : ""}${isResize ? " is-resizing" : ""}`}
              style={{
                left: `${(t.x / TILE_COLS) * 100}%`,
                width: `${(t.w / TILE_COLS) * 100}%`,
                top: t.y * TILE_ROW_H,
                height: t.h * TILE_ROW_H,
              }}
              data-testid={`tile-${t.moduleId}`}
              data-x={t.x}
              data-y={t.y}
              data-w={t.w}
              data-h={t.h}
            >
              <div
                className={`home-tile__card${entries.length ? " has-entries" : ""}${
                  colliding ? " is-colliding" : ""
                }`}
              >
                {/* 状态芯片：拖动报坐标、缩放报网格尺寸。演示与读屏都要能说清「现在在做什么」。 */}
                {isDrag || isResize ? (
                  <span
                    className="home-tile__chip"
                    data-testid={`tile-chip-${t.moduleId}`}
                    aria-hidden
                  >
                    {isResize
                      ? `${t.w}×${t.h} 格`
                      : colliding
                        ? `重叠 · 松手落到 ${ghost ? `${ghost.x},${ghost.y}` : "原处"}`
                        : `${t.x},${t.y}`}
                  </span>
                ) : null}
                <button
                  type="button"
                  className="home-tile__open"
                  onClick={() => openWindow(t.moduleId)}
                  onKeyDown={(e) => {
                    if (!editing || !e.altKey) return;
                    const dx = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
                    const dy = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
                    if (!dx && !dy) return;
                    e.preventDefault();
                    if (e.shiftKey) resizeNudge(t, dx, dy);
                    else nudge(t, dx, dy);
                  }}
                  title={`打开${t.name}`}
                  data-testid={`tile-open-${t.moduleId}`}
                >
                  <span className="home-tile__name">{t.name}</span>
                  <span className="home-tile__kind">{t.moduleId}</span>
                  {TILE_DESC[t.moduleId] ? (
                    <span className="home-tile__desc">{TILE_DESC[t.moduleId]}</span>
                  ) : null}
                </button>
                {/* 子入口：每条都真切状态 + 开窗；不嵌在 open 按钮里（button 不能套 button） */}
                {entries.length ? (
                  <div className="home-tile__entries" data-testid={`tile-entries-${t.moduleId}`}>
                    {entries.map((e) => (
                      <button
                        key={e.key}
                        type="button"
                        className="home-tile__entry"
                        onClick={() => applyTileEntry(t.moduleId, e.key)}
                        data-testid={`tile-entry-${t.moduleId}-${e.key}`}
                      >
                        {e.label}
                      </button>
                    ))}
                  </div>
                ) : null}
                {/* 底线②：只有这个 24×24 的把手能起拖；底线③：编辑态才渲染 */}
                {editing ? (
                  <button
                    type="button"
                    className="home-tile__grip"
                    aria-label={`拖动${t.name}`}
                    title="拖动（Alt+方向键也可以）"
                    onPointerDown={(e) => onGripDown(e, t)}
                    data-testid={`tile-grip-${t.moduleId}`}
                  >
                    ⠿
                  </button>
                ) : null}
                {/* 缩放手柄：与拖把手同规矩 —— 24x24、编辑态才在 DOM 里、独立入口。
                    为什么必须独立：press 一旦落在卡片内容上，父文档收不到 pointerdown。 */}
                {editing ? (
                  <button
                    type="button"
                    className="home-tile__resize"
                    aria-label={`调整${t.name}大小`}
                    title="拖动缩放（Alt+Shift+方向键也可以）"
                    onPointerDown={(e) => onResizeDown(e, t)}
                    onKeyDown={(e) => {
                      if (!e.altKey) return;
                      const dw = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
                      const dh = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
                      if (!dw && !dh) return;
                      e.preventDefault();
                      resizeNudge(t, dw, dh);
                    }}
                    data-testid={`tile-resize-${t.moduleId}`}
                  >
                    ◢
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}

        {/* 添加槽（团队 2026-11-22 裁定选 B）：网格里最后一个空格 = 「＋ 添加区块」。
            交互刻意保持最小——虚线槽 + 点开一个模块列表，先不做「拖进格子」。
            有空格且还有没上墙的模块才出现；拖动/缩放期间让位（那时在做排布，不掺加块）。 */}
        {freeSlot && addable.total > 0 && !busy ? (
          <div
            className="home-tiles__add"
            data-testid="home-tiles-add-slot"
            data-gx={freeSlot.x}
            data-gy={freeSlot.y}
            style={{
              left: `${(freeSlot.x / TILE_COLS) * 100}%`,
              width: `${(TILE_W / TILE_COLS) * 100}%`,
              top: freeSlot.y * TILE_ROW_H,
              height: TILE_H * TILE_ROW_H,
            }}
          >
            <button
              type="button"
              className="home-tiles__add-btn"
              aria-expanded={adding}
              aria-label="添加区块"
              onClick={() => setAdding((v) => !v)}
              data-testid="home-tiles-add"
            >
              ＋ 添加区块
            </button>
            {adding ? (
              <div
                className="home-tiles__pick"
                role="group"
                aria-label="选择要添加的区块"
                data-testid="home-tiles-add-picker"
              >
                <div className="home-tiles__pick-head">
                  <p className="home-tiles__pick-hint">放哪一块到这一格</p>
                  {/* 面板铺满整格、盖住了入口按钮 —— 鼠标路径必须有自己的出口，不能只靠 Esc。 */}
                  <button
                    type="button"
                    className="home-tiles__pick-close"
                    aria-label="收起模块列表"
                    onClick={() => setAdding(false)}
                    data-testid="home-tiles-add-close"
                  >
                    ×
                  </button>
                </div>
                <ul className="home-tiles__pick-list">
                  {pickRow(addable.skill)}
                  {addable.others.length ? (
                    <>
                      <li className="home-tiles__pick-sect">其它模块（内核 / 管理类）</li>
                      {pickRow(addable.others)}
                    </>
                  ) : null}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* 落位预览：虚线框标出松手会去的格子。只在「当前压着别人」时出现。 */}
        {ghost ? (
          <div
            className="home-tiles__ghost"
            aria-hidden
            data-testid="home-tiles-ghost"
            data-gx={ghost.x}
            data-gy={ghost.y}
            style={{
              left: `${(ghost.x / TILE_COLS) * 100}%`,
              width: `${((tiles.find((t) => t.i === dragId)?.w ?? TILE_W) / TILE_COLS) * 100}%`,
              top: ghost.y * TILE_ROW_H,
              height: (tiles.find((t) => t.i === dragId)?.h ?? TILE_H) * TILE_ROW_H,
            }}
          />
        ) : null}

        {/* 底线①：拖动时盖住整个 canvas。pointer capture 在把手上，遮罩不会抢事件，
            但它挡住了下面所有内容（含将来会有的 iframe）——mousemove 才不会丢。 */}
        {busy ? <div className="home-tiles__veil" aria-hidden data-testid="home-tiles-veil" /> : null}

        {tiles.length === 0 ? (
          <p className="home-tiles__empty" data-testid="home-tiles-empty">
            还没有可放的模块（模块加载中或尚未启用）
          </p>
        ) : null}
      </div>

      <span className="home-tiles__sr" aria-live="polite" data-testid="home-tiles-live">
        {dragName ? `正在拖动 ${dragName}` : resizeName ? `正在调整 ${resizeName} 的大小` : ""}
      </span>
    </section>
  );
}
