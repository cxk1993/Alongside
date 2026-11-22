import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
// 本仓库刻意不装 @types/node；node:fs 的形状声明在 src/apps/collab/node-fs.d.ts（全局环境声明）。
import { readFileSync } from "node:fs";

import { MTAB_PERSIST_PREFIX, MTAB_SELECT_EVENT } from "../../kernel/MultitabFrame";
import type { ModuleReg } from "../../kernel/store";
import { useDesktopStore } from "../../kernel/store";
import { useCalendarUI } from "../calendar/state";
import { useTodoUI } from "../todo/state";
import type { ModuleManifest } from "../../kernel/types";
import { HomeTiles, TILE_DESC, TILE_MAX, TILE_W, initialLayout, overlaps, resolveSlot } from "./HomeTiles";
import type { TileLayout } from "./HomeTiles";

/**
 * 区块区（P1 原型）的回归钉子。
 *
 * 三条底线里，jsdom 能钉住两条半：
 *   ① 拖动时 canvas 上有 `inset:0` 遮罩 → 遮罩存在性可测，**「指针真的不丢」只能真浏览器**；
 *   ② 拖把手 24×24 · 只有编辑态渲染 → DOM 存在性 + CSS 规格可测；
 *   ③ 编辑态才可拖 → 浏览态把手不在 DOM 里，从结构上就不可能误拖。
 *
 * jsdom 没有 PointerEvent：用带 clientX/Y/button 的 MouseEvent 模拟（本项目既有手法，
 * 见 apps/calendar/block/drag.test.tsx）。setPointerCapture 不存在 → 组件里 try/catch 兜底。
 */

const css = readFileSync("src/apps/home/homeTiles.css", "utf8");

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

function firePointer(node: Element, type: string, props: Record<string, unknown>) {
  node.dispatchEvent(new MouseEvent(type, { bubbles: true, ...props }));
}
function fireWindow(type: string, props: Record<string, unknown>) {
  window.dispatchEvent(new MouseEvent(type, { ...props }));
}

const SIX = {
  calendar: mod("calendar", "日程表"),
  todo: mod("todo", "待办"),
  habits: mod("habits", "习惯打卡"),
  finance: mod("finance", "理财"),
  notes: mod("notes", "笔记"),
  health: mod("health", "健康"),
};

function tile(i: string, x: number, y: number, w = TILE_W, h = 2): TileLayout {
  return { i, moduleId: i, name: i, x, y, w, h };
}

beforeEach(() => {
  localStorage.clear();
  useDesktopStore.setState({ modules: SIX, openWindow: vi.fn() });
});

afterEach(() => {
  // 先卸载再重置 store：否则在 RTL 自动 cleanup 之前改 store 会让仍挂载的
  // HomeTiles 在 act 外重渲（vitest 的 afterEach 顺序与 RTL 注册顺序相反）。
  cleanup();
  vi.clearAllMocks();
  useDesktopStore.setState({ modules: {}, openWindow: vi.fn() });
  // 子入口会真切这两个模块的 UI store —— 不重置就是跨用例污染。
  useTodoUI.setState({ view: "today", filterTag: null });
  useCalendarUI.setState({ view: "week", selectedId: null });
});

// ── 纯逻辑：落位与初始排布（不依赖 DOM 量测） ───────────────────
describe("区块区 · 落位", () => {
  it("不重叠时 resolveSlot 返回 null（原地不动，不是「挪走」）", () => {
    expect(resolveSlot({ x: 0, y: 0 }, { w: 4, h: 2 }, [tile("b", 4, 0)])).toBeNull();
  });

  it("重叠时挪到曼哈顿距离最近的空位（不是「扫描顺序里第一个」）", () => {
    // 4×2 的块想停在 (0,0)，被 (0,0) 与 (4,0) 占住。
    // 两个空位：(8,0) 距离 8、(0,2) 距离 2 → 必须选 (0,2)。
    const others = [tile("a", 0, 0), tile("b", 4, 0)];
    expect(resolveSlot({ x: 0, y: 0 }, { w: 4, h: 2 }, others)).toEqual({ x: 0, y: 2 });
  });

  it("整张网格都满了 → 返回 null（调用方负责回原位）", () => {
    const others = [
      tile("a", 0, 0),
      tile("b", 4, 0),
      tile("c", 8, 0),
      tile("d", 0, 2),
      tile("e", 4, 2),
      tile("f", 8, 2),
    ];
    expect(resolveSlot({ x: 0, y: 0 }, { w: 4, h: 2 }, others)).toBeNull();
  });

  it("重叠判定是半开区间（贴边不算压住）", () => {
    expect(overlaps(tile("a", 0, 0), tile("b", 4, 0))).toBe(false);
    expect(overlaps(tile("a", 0, 0), tile("b", 3, 0))).toBe(true);
  });

  it("初始排布：一排 3 块，满了换行，且有上限", () => {
    const six = initialLayout(
      ["a", "b", "c", "d", "e", "f"].map((id) => ({ id, name: id })),
    );
    expect(six).toHaveLength(TILE_MAX);
    expect(six[0]).toMatchObject({ x: 0, y: 0 });
    expect(six[2]).toMatchObject({ x: 8, y: 0 });
    expect(six[3]).toMatchObject({ x: 0, y: 2 });
  });
});

// ── 渲染 ───────────────────────────────────────────────────────
describe("区块区 · 渲染", () => {
  it("模块就绪后铺出区块（点开即用）", () => {
    render(<HomeTiles />);
    expect(screen.getByTestId("home-tiles")).toBeTruthy();
    for (const id of ["calendar", "todo", "habits", "finance", "notes"]) {
      expect(screen.getByTestId(`tile-${id}`)).toBeTruthy();
    }
    // 第六个不铺（刻意留空位）
    expect(screen.queryByTestId("tile-health")).toBeNull();
    expect(screen.getByTestId("home-tiles-hint").textContent).toContain("5 块");
  });

  it("点块 = 开对应模块的窗（传 id，不传名字）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("tile-open-todo"));
    expect(useDesktopStore.getState().openWindow).toHaveBeenCalledWith("todo");
  });

  it("还没有模块时给空态，不假装有区块", () => {
    useDesktopStore.setState({ modules: {} });
    render(<HomeTiles />);
    expect(screen.getByTestId("home-tiles-empty").textContent).toContain("还没有可放的模块");
  });

  // 内容层：只有「名字 + id」会被读成占位符；说明行是能力，不是数据。
  it("每块有一句能力说明（默认 5 块全有，且是「能做什么」不是数字）", () => {
    render(<HomeTiles />);
    for (const id of ["calendar", "todo", "habits", "finance", "notes"]) {
      const desc = screen.getByTestId(`tile-${id}`).querySelector(".home-tile__desc");
      expect(desc, `${id} 缺能力说明`).toBeTruthy();
      expect(desc!.textContent!.trim().length).toBeGreaterThan(1);
      // 真实数据未接入前，说明行里出现数字 = 假数据
      expect(desc!.textContent).not.toMatch(/\d/);
    }
  });

  it("未登记的模块 id → 不渲染说明行（宁缺不编）", () => {
    const unknown = mod("widget-x", "某插件");
    useDesktopStore.setState({ modules: { ...SIX, "widget-x": unknown } });
    // 默认 5 块里没有它，但它可能作为补位出现：用初始排布直接钉住渲染层的行为
    render(<HomeTiles />);
    const tiles = screen.getAllByTestId(/^tile-(?!open-|grip-)/);
    for (const el of tiles) {
      const id = el.getAttribute("data-testid")!.replace(/^tile-/, "");
      const desc = el.querySelector(".home-tile__desc");
      if (TILE_DESC[id]) expect(desc).toBeTruthy();
      else expect(desc, `${id} 不该有说明（未登记）`).toBeNull();
    }
  });
});

// ── 底线②③：把手只在编辑态、且是 24×24 的唯一拖拽入口 ──────────
describe("区块区 · 编辑态与把手", () => {
  it("浏览态没有把手（结构上不可能误拖）", () => {
    render(<HomeTiles />);
    expect(screen.queryByTestId("tile-grip-calendar")).toBeNull();
    expect(screen.getByTestId("home-tiles-edit").getAttribute("aria-pressed")).toBe("false");
  });

  it("点「编辑」→ 每块出现把手；点「完成」→ 全部消失", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.getByTestId("home-tiles-edit").getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("tile-grip-calendar")).toBeTruthy();
    expect(screen.getByTestId("tile-grip-notes")).toBeTruthy();
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.queryByTestId("tile-grip-calendar")).toBeNull();
  });

  it("把手是 24×24 且 touch-action:none（规格钉在 CSS 上）", () => {
    const grip = /\.home-tile__grip\s*\{([^}]*)\}/.exec(css);
    expect(grip).toBeTruthy();
    const body = grip ? grip[1] : "";
    expect(body).toMatch(/width:\s*24px/);
    expect(body).toMatch(/height:\s*24px/);
    expect(body).toMatch(/touch-action:\s*none/);
  });
});

// ── 底线①：拖动时整块 canvas 盖遮罩 ────────────────────────────
describe("区块区 · 拖动", () => {
  it("按住把手 → 出现 inset:0 遮罩；松手 → 遮罩消失", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.queryByTestId("home-tiles-veil")).toBeNull();

    const grip = screen.getByTestId("tile-grip-calendar");
    act(() => firePointer(grip, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    expect(screen.getByTestId("home-tiles-veil")).toBeTruthy();
    expect(screen.getByTestId("home-tiles").className).toContain("is-dragging");

    act(() => fireWindow("pointerup", { clientX: 10, clientY: 10 }));
    expect(screen.queryByTestId("home-tiles-veil")).toBeNull();
  });

  it("遮罩规格：inset:0 + z-index 高过块（否则挡不住 iframe）", () => {
    const veil = /\.home-tiles__veil\s*\{([^}]*)\}/.exec(css);
    expect(veil).toBeTruthy();
    const body = veil ? veil[1] : "";
    expect(body).toMatch(/inset:\s*0/);
    expect(body).toMatch(/z-index:\s*50/);
    expect(css).toMatch(/is-dragging[^{]*iframe[^{]*\{[^}]*pointer-events:\s*none/);
  });

  it("单块时拖动 → 格坐标随手移动，松手保持（无冲突不弹回）", () => {
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    const grip = screen.getByTestId("tile-grip-calendar");
    act(() => firePointer(grip, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    act(() => fireWindow("pointermove", { clientX: 310, clientY: 90 }));
    const el = screen.getByTestId("tile-calendar");
    // jsdom clientWidth=0 → 组件按 100px/格兜底：dx=300 → 3 格；dy=80 → 1 行
    expect(Number(el.getAttribute("data-x"))).toBe(3);
    expect(Number(el.getAttribute("data-y"))).toBe(1);
    act(() => fireWindow("pointerup", { clientX: 310, clientY: 90 }));
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-x"))).toBe(3);
  });

  it("拖到别人身上 → 松手挪到最近空位（不叠在一起）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    const grip = screen.getByTestId("tile-grip-calendar");
    act(() => firePointer(grip, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    // dx=400 → x=4，与 todo(x=4) 完全重叠
    act(() => fireWindow("pointermove", { clientX: 410, clientY: 10 }));
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-x"))).toBe(4);
    act(() => fireWindow("pointerup", { clientX: 410, clientY: 10 }));
    // others 占 (4,0)(8,0)(0,2)(4,2)；最近空位 = (0,0)（d=4，其他候选更远或冲突）
    const after = screen.getByTestId("tile-calendar");
    expect(Number(after.getAttribute("data-x"))).toBe(0);
    expect(Number(after.getAttribute("data-y"))).toBe(0);
  });

  it("键盘等价物：编辑态 Alt+→ 挪一格；浏览态 Alt+→ 不动", () => {
    render(<HomeTiles />);
    const open = screen.getByTestId("tile-open-calendar");
    fireEvent.keyDown(open, { key: "ArrowRight", altKey: true });
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-x"))).toBe(0);

    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    fireEvent.keyDown(screen.getByTestId("tile-open-calendar"), { key: "ArrowRight", altKey: true });
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-x"))).toBe(1);
  });
});

// ── 子入口：钉「点了传什么」，不是「渲染出什么」 ─────────────────
// 每条都要真切模块状态（store）或真写深链接，再开窗；只开窗不切页 = 假入口。
describe("区块区 · 子入口", () => {
  it("只给有真页签 / 真筛选的块挂入口（habits / notes 不编）", () => {
    render(<HomeTiles />);
    for (const id of ["finance", "todo", "calendar"]) {
      expect(screen.getByTestId(`tile-entries-${id}`), `${id} 缺子入口`).toBeTruthy();
    }
    // habits 的 prefs 只在挂载时读一次、notes 的筛选是组件内 useState —— 都没有真入口
    expect(screen.queryByTestId("tile-entries-habits")).toBeNull();
    expect(screen.queryByTestId("tile-entries-notes")).toBeNull();
  });

  it("todo「只看学业」：把 store 筛选切成学业，并开窗（不是只开窗）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("tile-entry-todo-study"));
    expect(useTodoUI.getState().filterTag).toBe("学业");
    expect(useDesktopStore.getState().openWindow).toHaveBeenCalledWith("todo");
  });

  it("todo「全部」：筛选清回 null —— 同一块上两个都是真状态位", () => {
    useTodoUI.setState({ filterTag: "学业" });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("tile-entry-todo-all"));
    expect(useTodoUI.getState().filterTag).toBeNull();
    expect(useDesktopStore.getState().openWindow).toHaveBeenCalledWith("todo");
  });

  it("calendar「月」：视图切成 month（默认是 week，所以这一步必然是真切换）", () => {
    render(<HomeTiles />);
    expect(useCalendarUI.getState().view).toBe("week");
    fireEvent.click(screen.getByTestId("tile-entry-calendar-month"));
    expect(useCalendarUI.getState().view).toBe("month");
    expect(useDesktopStore.getState().openWindow).toHaveBeenCalledWith("calendar");
  });

  it("finance「本地流水」：写持久化（窗没开也落该页）+ 广播（窗已开当场切页）", () => {
    const heard: unknown[] = [];
    const onSelect = (e: Event) => heard.push((e as CustomEvent).detail);
    window.addEventListener(MTAB_SELECT_EVENT, onSelect);
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("tile-entry-finance-local"));
    window.removeEventListener(MTAB_SELECT_EVENT, onSelect);

    expect(localStorage.getItem(MTAB_PERSIST_PREFIX + "finance")).toBe("local");
    expect(heard).toEqual([{ persistKey: "finance", page: "local" }]);
    expect(useDesktopStore.getState().openWindow).toHaveBeenCalledWith("finance");
  });

  it("子入口不嵌在「打开」按钮里（button 不能套 button，否则点它同时触发开窗）", () => {
    render(<HomeTiles />);
    const open = screen.getByTestId("tile-open-todo");
    expect(open.querySelector(".home-tile__entry")).toBeNull();
    const entry = screen.getByTestId("tile-entry-todo-study");
    expect(entry.closest("button")).toBe(entry);
  });
});
