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
import {
  HomeTiles,
  TILE_CAPACITY,
  TILE_DESC,
  TILE_MAX,
  TILE_MIN_H,
  TILE_MIN_W,
  TILE_W,
  firstFreeSlot,
  initialLayout,
  overlaps,
  resolveSize,
  resolveSlot,
} from "./HomeTiles";
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
    const tiles = screen.getAllByTestId(/^tile-(?!open-|grip-|resize-|entries-)/);
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

// ── 落位预览：预览与落位必须是同一套算法 ─────────────────────────
describe("区块区 · 落位预览", () => {
  it("压着别人时出现虚线预览框，且位置 = 松手后真正落到的格子", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.queryByTestId("home-tiles-ghost")).toBeNull();

    const grip = screen.getByTestId("tile-grip-calendar");
    act(() => firePointer(grip, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    // +4 格 -> 整块压在 todo(4,0) 上
    act(() => fireWindow("pointermove", { clientX: 410, clientY: 10 }));
    const ghost = screen.getByTestId("home-tiles-ghost");
    const gx = ghost.getAttribute("data-gx");
    const gy = ghost.getAttribute("data-gy");
    expect(Number(gx)).toBe(0);
    expect(Number(gy)).toBe(0);

    act(() => fireWindow("pointerup", { clientX: 410, clientY: 10 }));
    // 松手后的实际坐标必须与预览一致（0,0），否则预览就是在骗人
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-x"))).toBe(Number(gx));
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-y"))).toBe(Number(gy));
    expect(screen.queryByTestId("home-tiles-ghost")).toBeNull();
  });

  it("没压着别人时不画预览框（不重复叙述当前位置）", () => {
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    act(() =>
      firePointer(screen.getByTestId("tile-grip-calendar"), "pointerdown", {
        clientX: 10,
        clientY: 10,
        button: 0,
      }),
    );
    act(() => fireWindow("pointermove", { clientX: 110, clientY: 10 }));
    expect(screen.queryByTestId("home-tiles-ghost")).toBeNull();
    act(() => fireWindow("pointerup", { clientX: 110, clientY: 10 }));
  });

  it("预览框是虚线 + accent 底（视觉上区别于真块）", () => {
    const m = /\.home-tiles__ghost\s*\{([^}]*)\}/.exec(css);
    expect(m).toBeTruthy();
    const body = m ? m[1] : "";
    expect(body).toMatch(/border:\s*1px dashed var\(--accent\)/);
    expect(body).toMatch(/pointer-events:\s*none/);
  });
});

// ── 添加槽（2026-11-22 团队裁定选 B）：空格 = 显式入口 ─────────────
describe("区块区 · 添加槽", () => {
  const FIVE = [
    tile("a", 0, 0),
    tile("b", 4, 0),
    tile("c", 8, 0),
    tile("d", 0, 2),
    tile("e", 4, 2),
  ];

  it("firstFreeSlot：5 块占满前两行 → 空格在第三列第二行 (8,2)", () => {
    expect(firstFreeSlot(FIVE)).toEqual({ x: 8, y: 2 });
  });

  it("firstFreeSlot：网格满（6 块 = 容量）→ null，不硬造一个放不下的槽", () => {
    expect(TILE_CAPACITY).toBe(6);
    expect(firstFreeSlot([...FIVE, tile("f", 8, 2)])).toBeNull();
  });

  it("firstFreeSlot：别人挪出更靠前的空格时，槽跟着走（不是写死 8,2）", () => {
    const moved = [tile("a", 8, 0), tile("b", 4, 0), tile("c", 0, 2), tile("d", 4, 2)];
    expect(firstFreeSlot(moved)).toEqual({ x: 0, y: 0 });
  });

  it("浏览态在空格画添加槽，位置与格坐标一致（不是装饰图）", () => {
    render(<HomeTiles />);
    const slot = screen.getByTestId("home-tiles-add-slot");
    expect(slot.dataset.gx).toBe("8");
    expect(slot.dataset.gy).toBe("2");
    expect(screen.getByTestId("home-tiles-add").textContent).toContain("添加区块");
    // 列表默认收起：先给一个入口，不先给一整块面板。
    expect(screen.queryByTestId("home-tiles-add-picker")).toBeNull();
  });

  it("点开列表：只列没上墙的模块（模块 id 不重复放第二块）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    expect(screen.getByTestId("home-tiles-add-picker")).toBeTruthy();
    expect(screen.getByTestId("add-tile-health")).toBeTruthy();
    expect(screen.queryByTestId("add-tile-calendar")).toBeNull();
    expect(screen.queryByTestId("add-tile-notes")).toBeNull();
  });

  it("列表分两段：能当墙面块的在前，内核/管理类在后（不隐藏）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    const list = screen.getByTestId("home-tiles-add-picker");
    const order = [...list.querySelectorAll("[data-testid^=add-tile-]")].map(
      (el) => el.getAttribute("data-testid"),
    );
    // SIX 里的 6 个模块都在 TILE_DESC 里 → 全是第一段，排序按注册顺序。
    expect(order).toEqual(["add-tile-health"]);
    expect(list.textContent).not.toContain("其它模块");
  });

  it("内核模块排在「其它模块」段里，不被隐藏（宁散勿丢）", () => {
    useDesktopStore.setState({
      modules: { ...SIX, agents: mod("agents", "AI 编排"), auth: mod("auth", "账户与鉴权") },
    });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    const list = screen.getByTestId("home-tiles-add-picker");
    expect(list.textContent).toContain("其它模块（内核 / 管理类）");
    const order = [...list.querySelectorAll("[data-testid^=add-tile-]")].map(
      (el) => el.getAttribute("data-testid"),
    );
    // 有说明的 health 在前；无说明的 agents / auth 在后。
    expect(order).toEqual(["add-tile-health", "add-tile-agents", "add-tile-auth"]);
  });

  it("面板自带关闭出口（它铺满整格、盖住了入口按钮）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    fireEvent.click(screen.getByTestId("home-tiles-add-close"));
    expect(screen.queryByTestId("home-tiles-add-picker")).toBeNull();
    expect(screen.getByTestId("home-tiles-add")).toBeTruthy();
  });

  it("选一块 → 落到空格、槽与列表一起消失（网格满了就没有添加槽）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    fireEvent.click(screen.getByTestId("add-tile-health"));
    expect(screen.queryByTestId("home-tiles-add-slot")).toBeNull();
    expect(screen.queryByTestId("home-tiles-add-picker")).toBeNull();
    const el = screen.getByTestId("tile-health");
    expect(el.dataset.x).toBe("8");
    expect(el.dataset.y).toBe("2");
    expect(screen.getAllByTestId(/^tile-(calendar|todo|habits|finance|notes|health)$/)).toHaveLength(6);
  });

  it("Esc 收起列表（键盘路径不只剩鼠标）", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-add"));
    expect(screen.getByTestId("home-tiles-add-picker")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("home-tiles-add-picker")).toBeNull();
  });

  it("添加槽规格钉在 CSS 上：虚线 + 列表在槽内自滚", () => {
    const btn = /\.home-tiles__add-btn\s*\{([^}]*)\}/.exec(css);
    expect(btn?.[1] ?? "").toMatch(/border:\s*1px dashed var\(--line-strong\)/);
    const list = /\.home-tiles__pick-list\s*\{([^}]*)\}/.exec(css);
    expect(list?.[1] ?? "").toMatch(/overflow-y:\s*auto/);
  });

  it("画布左右各补回 --sp-1：卡片外缘与上方摘要卡对齐（实测 16 / 1350）", () => {
    const canvas = /\.home-tiles__canvas\s*\{([^}]*)\}/.exec(css);
    const body = canvas?.[1] ?? "";
    expect(body).toMatch(/width:\s*calc\(100% \+ var\(--sp-1\) \* 2\)/);
    expect(body).toMatch(/margin-inline:\s*calc\(-1 \* var\(--sp-1\)\)/);
  });
});

// ── 缩放：与拖动同三条底线，只是把 w/h 交给它 ─────────────────────
describe("区块区 · 缩放", () => {
  it("resolveSize：不冲突时给请求尺寸", () => {
    expect(resolveSize({ x: 0, y: 0 }, { w: 6, h: 3 }, [tile("b", 8, 0)])).toEqual({ w: 6, h: 3 });
  });

  it("resolveSize：长到别人身上 -> 退到最近合法尺寸（位置不动）", () => {
    // (0,0) 想长成 8x3，右边被 (4,0,4x2) 占住；w<=4 才不压人。
    // 最近的是 4x3（|8-4|+|3-3| = 4），不是 4x2（5）也不是 3x3（5）。
    expect(resolveSize({ x: 0, y: 0 }, { w: 8, h: 3 }, [tile("b", 4, 0, 4, 2)])).toEqual({
      w: 4,
      h: 3,
    });
  });

  it("resolveSize：缩到 0 也是最小尺寸（不会变成看不见的小块）", () => {
    expect(resolveSize({ x: 0, y: 0 }, { w: 0, h: 0 }, [])).toEqual({
      w: TILE_MIN_W,
      h: TILE_MIN_H,
    });
  });

  it("浏览态没有缩放手柄；编辑态每块一个（与拖把手同一套纪律）", () => {
    render(<HomeTiles />);
    expect(screen.queryByTestId("tile-resize-calendar")).toBeNull();
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.getByTestId("tile-resize-calendar")).toBeTruthy();
    expect(screen.getByTestId("tile-resize-notes")).toBeTruthy();
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.queryByTestId("tile-resize-calendar")).toBeNull();
  });

  it("缩放手柄 24x24 + touch-action:none + cursor:nwse-resize（规格钉在 CSS 上）", () => {
    // 同一 class 有多条规则（基础尺寸 / 编辑态配色）：取含尺寸的那条，别取第一条。
    const blocks = [...css.matchAll(/\.home-tile__resize[^{}]*\{([^}]*)\}/g)].map((m) => m[1]);
    const body = blocks.find((b) => /width:\s*24px/.test(b)) ?? "";
    expect(body).toMatch(/width:\s*24px/);
    expect(body).toMatch(/height:\s*24px/);
    expect(body).toMatch(/touch-action:\s*none/);
    expect(body).toMatch(/cursor:\s*nwse-resize/);
    // 编辑态必须给 accent 底/描边：暗灰把手在深色卡上「凑近才看得见」，等于没有可供性。
    expect(css).toMatch(/\.home-tiles\.is-editing[^{]*\.home-tile__resize[^{]*\{[^}]*border-color:\s*var\(--accent\)/);
  });

  it("缩放中有尺寸芯片（报网格尺寸，不是像素）", () => {
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    expect(screen.queryByTestId("tile-chip-calendar")).toBeNull();
    act(() =>
      firePointer(screen.getByTestId("tile-resize-calendar"), "pointerdown", {
        clientX: 10,
        clientY: 10,
        button: 0,
      }),
    );
    act(() => fireWindow("pointermove", { clientX: 210, clientY: 10 }));
    expect(screen.getByTestId("tile-chip-calendar").textContent).toBe("6×2 格");
    act(() => fireWindow("pointerup", { clientX: 210, clientY: 10 }));
    expect(screen.queryByTestId("tile-chip-calendar")).toBeNull();
  });

  it("按住缩放手柄 -> 出遮罩；拖动尺寸跟手；松手保持", () => {
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    const rz = screen.getByTestId("tile-resize-calendar");
    act(() => firePointer(rz, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    expect(screen.getByTestId("home-tiles-veil")).toBeTruthy();
    expect(screen.getByTestId("home-tiles").className).toContain("is-resizing");

    // jsdom clientWidth=0 -> 100px/格、76px/行：dx=200 -> +2 列，dy=76 -> +1 行
    act(() => fireWindow("pointermove", { clientX: 210, clientY: 86 }));
    const el = screen.getByTestId("tile-calendar");
    expect(Number(el.getAttribute("data-w"))).toBe(6);
    expect(Number(el.getAttribute("data-h"))).toBe(3);

    act(() => fireWindow("pointerup", { clientX: 210, clientY: 86 }));
    const after = screen.getByTestId("tile-calendar");
    expect(Number(after.getAttribute("data-w"))).toBe(6);
    expect(Number(after.getAttribute("data-h"))).toBe(3);
    expect(screen.queryByTestId("home-tiles-veil")).toBeNull();
  });

  it("长到邻居身上 -> 松手退回合法尺寸，邻居一动不动", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    const rz = screen.getByTestId("tile-resize-calendar");
    act(() => firePointer(rz, "pointerdown", { clientX: 10, clientY: 10, button: 0 }));
    // +4 格 -> w=8，正压住 todo(4,0)
    act(() => fireWindow("pointermove", { clientX: 410, clientY: 10 }));
    expect(Number(screen.getByTestId("tile-calendar").getAttribute("data-w"))).toBe(8);
    act(() => fireWindow("pointerup", { clientX: 410, clientY: 10 }));

    // 退回最近合法尺寸 = 原尺寸 4x2（位置不动，不是弹到空位）
    const cal = screen.getByTestId("tile-calendar");
    expect(Number(cal.getAttribute("data-w"))).toBe(4);
    expect(Number(cal.getAttribute("data-h"))).toBe(2);
    expect(Number(cal.getAttribute("data-x"))).toBe(0);
    expect(Number(cal.getAttribute("data-y"))).toBe(0);
    expect(Number(screen.getByTestId("tile-todo").getAttribute("data-w"))).toBe(4);
  });

  it("键盘等价物：编辑态 Alt+Shift+↓ 长一行；浏览态不动", () => {
    // 单块：下方没有邻居，才能验「键盘缩放本身生效」（五块时 (0,2) 被占，长下去本该被拦）。
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    const h0 = () => Number(screen.getByTestId("tile-calendar").getAttribute("data-h"));
    expect(h0()).toBe(2);

    fireEvent.keyDown(screen.getByTestId("tile-open-calendar"), {
      key: "ArrowDown",
      altKey: true,
      shiftKey: true,
    });
    expect(h0()).toBe(2);

    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    // 往右长会被 todo(4,0) 挡住 —— 那正是 resolveSize 该拦的；往下长（第 3 行是空的）才该成。
    fireEvent.keyDown(screen.getByTestId("tile-open-calendar"), {
      key: "ArrowDown",
      altKey: true,
      shiftKey: true,
    });
    expect(h0()).toBe(3);
  });

  it("键盘缩放也受「不压别人」约束：往右长被邻居挡住 -> 尺寸不变", () => {
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    const w0 = () => Number(screen.getByTestId("tile-calendar").getAttribute("data-w"));
    expect(w0()).toBe(4);
    fireEvent.keyDown(screen.getByTestId("tile-open-calendar"), {
      key: "ArrowRight",
      altKey: true,
      shiftKey: true,
    });
    expect(w0()).toBe(4);
  });

  it("读屏播报：缩放时说「正在调整 … 的大小」（与拖动区分开）", () => {
    useDesktopStore.setState({ modules: { calendar: mod("calendar", "日程表") } });
    render(<HomeTiles />);
    fireEvent.click(screen.getByTestId("home-tiles-edit"));
    act(() =>
      firePointer(screen.getByTestId("tile-resize-calendar"), "pointerdown", {
        clientX: 10,
        clientY: 10,
        button: 0,
      }),
    );
    expect(screen.getByTestId("home-tiles-live").textContent).toContain("正在调整");
    act(() => fireWindow("pointerup", { clientX: 10, clientY: 10 }));
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
