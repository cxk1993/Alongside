import { create } from "zustand";

import type { ModuleManifest } from "./types";

/**
 * 桌面状态中枢（Zustand）。
 *
 * 管两件事：
 *   1. 模块注册表（来自 modules.json，由 ModuleRegistry 写入）。
 *   2. 窗口生命周期（open/close/focus/min/max/z-index）与几何持久化。
 *
 * 几何存 localStorage，刷新后由 Desktop 调 hydrate() 恢复。
 * 这是「注册表与运行时分离」的最小实现，方便将来热插拔（T14）。
 */

export interface WinGeo {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface WindowState {
  instanceId: string;
  moduleId: string;
  z: number;
  minimized: boolean;
  maximized: boolean;
  geo: WinGeo;
  /** 最大化前的几何，用于还原。 */
  _prev?: WinGeo;
  /**
   * ★ T22 置顶：进「置顶层」，渲染时始终盖过普通层。
   * `z` 仍只在普通层语义内维护 —— 两层计数不混算（见 PIN_BASE）。
   */
  pinned: boolean;
  /** ★ T22 固定几何：位置与大小锁定，不可拖拽、不可缩放。 */
  fixedGeometry: boolean;
  /** ★ 置顶层内的序号（由 topPinZ 分配）。未置顶时为 undefined。 */
  pinZ?: number;
  /**
   * ★ T23 归属工作区。
   * ★ 设计要点：`windows` **保持全量平铺**（不按工作区分成多份数组），
   * 每扇窗只带一个 `workspaceId` —— 这样"切换工作区"才能做成**隐藏**而非卸载，
   * 从而保住窗口内 iframe 的登录态（契约 #4）。
   */
  workspaceId: string;
}

/**
 * ★ T23 工作区。
 * ★ 每个工作区**独立**持有窗口计数器（契约 #2）：在 A 聚焦窗口不会顶起 B 的 z 序。
 * ★ 内核零业务：`name` 默认「工作区 N」，不许预设任何业务名。
 */
export interface WorkspaceState {
  id: string;
  name: string;
  /** 普通层 z 计数器（本工作区独立）。 */
  topZ: number;
  /** 置顶层计数器（本工作区独立）。 */
  topPinZ: number;
  /** 开窗序号（用于默认位置级联，本工作区独立）。 */
  seq: number;
}

export interface ModuleReg {
  manifest: ModuleManifest;
  enabled: boolean;
  error?: string;
}

const TOPBAR = 48;
const DOCK = 64;
const SNAP = 8;
const STORE_KEY = "alongside.windows.v1";

/**
 * ★ T22：置顶层的 zIndex 偏移基数。
 * 取值远大于普通层可能达到的 z（普通层从 10 起、每操作 +1），
 * 从而保证「任何置顶窗的 zIndex > 任何普通窗的 zIndex」，两层**永不混算**。
 */
const PIN_BASE = 10000;

/**
 * ★ T23 持久化形状（v2）。
 *
 * **向后兼容策略（契约 #3）**：
 *   - ★ **不换 localStorage key**（仍是 `STORE_KEY`）—— 换了 key 等于遗弃旧数据，违反"零丢失"；
 *   - v2 新增 `workspaces` / `activeWorkspaceId`，把 `topZ/topPinZ/seq` **下沉进工作区对象**；
 *   - 顶层保留这三个字段为**可选**，**只用于读 v1 旧快照**（迁移后并入「工作区 1」）。
 */
interface PersistedShape {
  windows: WindowState[];
  /** v2：工作区列表 */
  workspaces?: WorkspaceState[];
  /** v2：当前工作区 id */
  activeWorkspaceId?: string;
  /** @deprecated v1 遗留：迁移时并入「工作区 1」 */
  topZ?: number;
  /** @deprecated v1 遗留 */
  topPinZ?: number;
  /** @deprecated v1 遗留 */
  seq?: number;
  enabled: Record<string, boolean>;
  /** ★ U1-2 修复（hermes）：顶栏/底栏收放跨会话持久化。
   *  旧快照缺字段 → hydrate 的 typeof 守卫不误折叠（向后兼容）。 */
  topbarCollapsed?: boolean;
  bottombarCollapsed?: boolean;
}

/** ★ T23 默认工作区 id（旧数据也迁到这里）。 */
export const DEFAULT_WORKSPACE_ID = "ws1";

/** 新建一个工作区。`index` 从 1 起 —— 名字默认「工作区 N」（内核零业务）。 */
function newWorkspace(index: number): WorkspaceState {
  return { id: `ws${index}`, name: `工作区 ${index}`, topZ: 10, topPinZ: 0, seq: 0 };
}

/**
 * ★ T23：造一个工作区（可选覆盖任意字段）。
 * 导出它是为了让 hydrate 与各处测试**共用同一份字段样板** ——
 * 手拼字段的地方越多，"漏一个字段"的概率越大（T22 的教训）。
 */
export function makeWorkspace(index = 1, patch: Partial<WorkspaceState> = {}): WorkspaceState {
  return { ...newWorkspace(index), ...patch };
}

/** 下一个可用的工作区序号（取现有 id 里的最大数字 +1，保证唯一）。 */
function nextWorkspaceIndex(workspaces: WorkspaceState[]): number {
  let max = 0;
  for (const w of workspaces) {
    const n = Number.parseInt(w.id.replace(/^ws/, ""), 10);
    if (Number.isFinite(n) && n > max) max = n;
  }
  return max + 1;
}

function vw(): number {
  return typeof window !== "undefined" && window.innerWidth ? window.innerWidth : 1280;
}
function vh(): number {
  return typeof window !== "undefined" && window.innerHeight ? window.innerHeight : 800;
}

function snap(v: number): number {
  return Math.round(v / SNAP) * SNAP;
}

function clampGeo(g: WinGeo, manifest: ModuleManifest): WinGeo {
  const minW = manifest.window.minW ?? 360;
  const minH = manifest.window.minH ?? 240;
  const w = Math.max(minW, Math.round(g.w));
  const h = Math.max(minH, Math.round(g.h));
  // ★ 主人：仅保留顶端回弹（y>=0 防跑出屏幕顶）；左右/底部不钳制，
  //   允许窗口移出屏幕边缘（一键桌面整理可找回，无需强制弹回）。
  const x = Math.round(g.x);
  const y = Math.max(0, Math.round(g.y));
  return { x, y, w, h };
}

function fullGeo(): WinGeo {
  return { x: 0, y: TOPBAR, w: vw(), h: Math.max(240, vh() - TOPBAR - DOCK) };
}

function defaultGeo(manifest: ModuleManifest, seq: number): WinGeo {
  const spec = manifest.window;
  // ★ T30（顺修 T02 疑点②）：视口比 manifest 要的窗还窄/矮时，把**新窗**钳进视口 ——
  //   否则窗口右缘/下缘永远在屏幕外，够不着（ACCEPT-T02 实测）。
  //   只钳"新窗"的初始尺寸，不动用户自己缩放出来的几何。
  const minW = spec.minW ?? 360;
  const minH = spec.minH ?? 240;
  const w = Math.min(spec.w, Math.max(minW, vw() - 2 * SNAP));
  const h = Math.min(spec.h, Math.max(minH, vh() - TOPBAR - DOCK));
  if (typeof spec.x === "number" && typeof spec.y === "number") {
    return clampGeo({ x: spec.x, y: spec.y, w, h }, manifest);
  }
  const off = 48 + (seq % 6) * 28;
  return clampGeo({ x: off, y: TOPBAR + off, w, h }, manifest);
}

function loadPersisted(): PersistedShape | null {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    return raw ? (JSON.parse(raw) as PersistedShape) : null;
  } catch {
    return null;
  }
}

function savePersisted(s: DesktopState): void {
  try {
    const enabled: Record<string, boolean> = {};
    for (const id in s.modules) enabled[id] = s.modules[id].enabled;
    // ★ T23：计数器已**下沉进工作区对象**，顶层不再写 topZ/topPinZ/seq
    //（顶层那三个字段留着只为读 v1 旧快照）
    const data: PersistedShape = {
      windows: s.windows,
      workspaces: s.workspaces,
      activeWorkspaceId: s.activeWorkspaceId,
      enabled,
      // ★ U1-2 修复（hermes）：折叠字段此前被白名单丢弃 → toggle 写了快照但
      //   序列化没带上，hydrate 永远读 undefined（实测 reload 弹回 48px）。
      topbarCollapsed: s.topbarCollapsed,
      bottombarCollapsed: s.bottombarCollapsed,
    };
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
  } catch {
    /* 存储不可用时忽略 */
  }
}

export interface DesktopState {
  modules: Record<string, ModuleReg>;
  /** ★ T23：**全量平铺**（跨全部工作区），每窗自带 `workspaceId`。 */
  windows: WindowState[];
  /** ★ T23：工作区列表（每个自带独立计数器）。 */
  workspaces: WorkspaceState[];
  /** ★ T23：当前工作区 id。 */
  activeWorkspaceId: string;
  /** ★ U1（2026-09-24）：顶栏/底栏收放。跨会话持久化（判据 U1-2，与窗口几何同 STORE_KEY）。 */
  topbarCollapsed: boolean;
  bottombarCollapsed: boolean;
  toggleTopbar: () => void;
  toggleBottombar: () => void;

  registerModule: (m: ModuleManifest) => void;
  unregisterModule: (id: string) => void;
  enableModule: (id: string) => void;
  disableModule: (id: string) => void;

  openWindow: (moduleId: string) => void;
  closeWindow: (instanceId: string) => void;
  focusWindow: (instanceId: string) => void;
  minimizeWindow: (instanceId: string) => void;
  /** ★ 主人④（2026-09-24）：一键最小化所有未固定窗口（固定窗/已最小化/最大化窗不动）。 */
  minimizeAllUnpinned: () => void;
  restoreWindow: (instanceId: string) => void;
  toggleMaximize: (instanceId: string) => void;
  setGeo: (instanceId: string, patch: Partial<WinGeo>) => void;
  tidyDesktop: () => void;
  /** ★ T22：置顶开关（进/出置顶层）。 */
  setPinned: (instanceId: string, pinned: boolean) => void;
  /** ★ T22：固定几何开关（锁定位置与大小）。 */
  setFixedGeometry: (instanceId: string, fixed: boolean) => void;

  /** ★ T23：新建工作区并切过去，返回新工作区 id。 */
  createWorkspace: () => string;
  /** ★ T23：切换工作区。 */
  switchWorkspace: (workspaceId: string) => void;
  /** ★ T23：把某扇窗移到另一工作区（几何与置顶状态原样带走）。 */
  moveWindowToWorkspace: (instanceId: string, workspaceId: string) => void;

  hydrate: () => void;
}

/** ★ T23：取当前工作区（找不到就退回第一个，绝不返回 undefined）。 */
export function activeWorkspace(s: DesktopState): WorkspaceState {
  return s.workspaces.find((w) => w.id === s.activeWorkspaceId) ?? s.workspaces[0];
}

/** ★ T23：不可变地给某个工作区打补丁（计数器自增用）。 */
function patchWorkspace(
  workspaces: WorkspaceState[],
  id: string,
  patch: Partial<WorkspaceState>,
): WorkspaceState[] {
  return workspaces.map((w) => (w.id === id ? { ...w, ...patch } : w));
}

/** ★ T23：某扇窗属于哪个工作区（渲染与 active 判定都要用）。 */
export function workspaceOf(
  s: DesktopState,
  instanceId: string | null,
): WorkspaceState | undefined {
  if (!instanceId) return undefined;
  const w = s.windows.find((x) => x.instanceId === instanceId);
  return w ? s.workspaces.find((k) => k.id === w.workspaceId) : undefined;
}

export const useDesktopStore = create<DesktopState>((set, get) => ({
  modules: {},
  windows: [],
  // ★ T23：默认一个「工作区 1」；v1 旧数据 hydrate 时也整体迁到这里
  workspaces: [newWorkspace(1)],
  activeWorkspaceId: DEFAULT_WORKSPACE_ID,
  topbarCollapsed: false,
  bottombarCollapsed: false,

  registerModule: (m) => {
    const persisted = loadPersisted();
    const enabled = persisted?.enabled[m.id] ?? true;
    set((s) => ({
      modules: { ...s.modules, [m.id]: { manifest: m, enabled } },
    }));
  },

  unregisterModule: (id) => {
    set((s) => {
      const next = { ...s.modules };
      delete next[id];
      return {
        modules: next,
        windows: s.windows.filter((w) => w.moduleId !== id),
      };
    });
    savePersisted(get());
  },

  enableModule: (id) => {
    set((s) => {
      const reg = s.modules[id];
      if (!reg) return {};
      return { modules: { ...s.modules, [id]: { ...reg, enabled: true, error: undefined } } };
    });
    savePersisted(get());
  },

  disableModule: (id) => {
    set((s) => {
      const reg = s.modules[id];
      if (!reg) return {};
      return { modules: { ...s.modules, [id]: { ...reg, enabled: false } } };
    });
    savePersisted(get());
  },

  openWindow: (moduleId) => {
    const s = get();
    const reg = s.modules[moduleId];
    if (!reg || !reg.enabled) return;
    const manifest = reg.manifest;
    const ws = activeWorkspace(s);

    if (manifest.window.singleton) {
      // ★ T23：singleton 只在**当前工作区**内去重 —— 不同工作区可以各开一扇
      const existing = s.windows.find((w) => w.moduleId === moduleId && w.workspaceId === ws.id);
      if (existing) {
        get().restoreWindow(existing.instanceId);
        get().focusWindow(existing.instanceId);
        return;
      }
    }

    const seq = ws.seq + 1;
    const topZ = ws.topZ + 1;
    const geo = defaultGeo(manifest, seq);
    const inst: WindowState = {
      // ★ T23：instanceId 必须**全局唯一**——带上工作区前缀，
      //   否则两个工作区各自的 `m1#1` 会撞 key、撞查找。
      instanceId: `${ws.id}:${moduleId}#${seq}`,
      moduleId,
      z: topZ,
      minimized: false,
      maximized: false,
      geo,
      pinned: false,
      fixedGeometry: false,
      workspaceId: ws.id,
    };
    set((st) => ({
      windows: [...st.windows, inst],
      workspaces: patchWorkspace(st.workspaces, ws.id, { topZ, seq }),
    }));
    savePersisted(get());
  },

  closeWindow: (instanceId) => {
    set((s) => ({ windows: s.windows.filter((w) => w.instanceId !== instanceId) }));
    savePersisted(get());
  },

  focusWindow: (instanceId) => {
    set((s) => {
      const w = s.windows.find((x) => x.instanceId === instanceId);
      if (!w) return {};
      // ★ T23：自增的是**该窗所属工作区**的计数器 —— 在 A 聚焦不会顶起 B 的 z 序（验收 #3）
      const ws = s.workspaces.find((k) => k.id === w.workspaceId) ?? activeWorkspace(s);
      const topZ = ws.topZ + 1;
      return {
        workspaces: patchWorkspace(s.workspaces, ws.id, { topZ }),
        windows: s.windows.map((x) =>
          x.instanceId === instanceId ? { ...x, z: topZ, minimized: false } : x,
        ),
      };
    });
    savePersisted(get());
  },

  minimizeWindow: (instanceId) => {
    set((s) => ({
      windows: s.windows.map((x) => (x.instanceId === instanceId ? { ...x, minimized: true } : x)),
    }));
    savePersisted(get());
  },

  restoreWindow: (instanceId) => {
    set((s) => ({
      windows: s.windows.map((x) => (x.instanceId === instanceId ? { ...x, minimized: false } : x)),
    }));
    savePersisted(get());
  },

  toggleMaximize: (instanceId) => {
    set((s) => {
      const w = s.windows.find((x) => x.instanceId === instanceId);
      if (!w) return {};
      // ★ T23：同 focusWindow —— 只动**该窗所属工作区**的计数器
      const ws = s.workspaces.find((k) => k.id === w.workspaceId) ?? activeWorkspace(s);
      const topZ = ws.topZ + 1;
      const bump = patchWorkspace(s.workspaces, ws.id, { topZ });
      if (w.maximized && w._prev) {
        return {
          workspaces: bump,
          windows: s.windows.map((x) =>
            x.instanceId === instanceId ? { ...x, maximized: false, geo: w._prev!, z: topZ } : x,
          ),
        };
      }
      return {
        workspaces: bump,
        windows: s.windows.map((x) =>
          x.instanceId === instanceId
            ? { ...x, maximized: true, _prev: x.geo, geo: fullGeo(), z: topZ }
            : x,
        ),
      };
    });
    savePersisted(get());
  },

  setGeo: (instanceId, patch) => {
    set((s) => {
      const w = s.windows.find((x) => x.instanceId === instanceId);
      if (!w) return {};
      const merged = { ...w.geo, ...patch };
      const geo = w.maximized ? merged : clampGeo(merged, fallbackManifest(w.moduleId, s));
      return {
        windows: s.windows.map((x) => (x.instanceId === instanceId ? { ...x, geo } : x)),
      };
    });
    savePersisted(get());
  },

  // ★ U1（2026-09-24）：顶栏/底栏收放（切换即持久化，判据 U1-2 跨会话保持）。
  toggleTopbar: () => {
    set((s) => {
      const topbarCollapsed = !s.topbarCollapsed;
      savePersisted({ ...s, topbarCollapsed });
      return { topbarCollapsed };
    });
  },
  toggleBottombar: () => {
    set((s) => {
      const bottombarCollapsed = !s.bottombarCollapsed;
      savePersisted({ ...s, bottombarCollapsed });
      return { bottombarCollapsed };
    });
  },
  // ★ 主人④（2026-09-24）：一键最小化所有**未固定**窗口（固定窗/已最小化窗不动）。
  minimizeAllUnpinned: () => {
    set((s) => ({
      windows: s.windows.map((w) =>
        w.pinned || w.minimized || w.maximized ? w : { ...w, minimized: true },
      ),
    }));
  },
  tidyDesktop: () => {
    set((s) => {
      // ★ T23：只整理**当前工作区**的窗口 —— 其它工作区的窗不可见，
      //   动它们只会制造"切过去发现布局被莫名其妙改过"的意外。
      const ws = activeWorkspace(s);
      let seq = ws.seq;
      const windows = s.windows.map((w) => {
        if (w.workspaceId !== ws.id) return w;
        seq += 1;
        const manifest = s.modules[w.moduleId]?.manifest;
        const geo = manifest ? defaultGeo(manifest, seq) : w.geo;
        return { ...w, minimized: false, maximized: false, _prev: undefined, geo };
      });
      return { windows, workspaces: patchWorkspace(s.workspaces, ws.id, { seq }) };
    });
    savePersisted(get());
  },

  /**
   * ★ T22 置顶开关。
   * - **置顶**：进置顶层，取一个新的 `pinZ`（`topPinZ + 1`）→ 置顶层内部「后钉的在上」，
   *   且这个相对顺序**不会被后续点击聚焦打乱**（验收 #5）。
   * - **取消置顶**：回普通层。★ 同时把它在普通层的 `z` 提到最前 ——
   *   否则它按旧 `z` 沉到别的窗后面，"取消钉住"一按窗口当场消失，很困惑。
   */
  setPinned: (instanceId, pinned) => {
    set((s) => {
      const w = s.windows.find((x) => x.instanceId === instanceId);
      if (!w) return {};
      // ★ T23：置顶层计数器也是 **per-workspace**（契约 #2）
      const ws = s.workspaces.find((k) => k.id === w.workspaceId) ?? activeWorkspace(s);
      if (pinned) {
        const topPinZ = ws.topPinZ + 1;
        return {
          workspaces: patchWorkspace(s.workspaces, ws.id, { topPinZ }),
          windows: s.windows.map((x) =>
            x.instanceId === instanceId ? { ...x, pinned: true, pinZ: topPinZ } : x,
          ),
        };
      }
      const topZ = ws.topZ + 1;
      return {
        workspaces: patchWorkspace(s.workspaces, ws.id, { topZ }),
        windows: s.windows.map((x) =>
          x.instanceId === instanceId ? { ...x, pinned: false, pinZ: undefined, z: topZ } : x,
        ),
      };
    });
    savePersisted(get());
  },

  /**
   * ★ T22 固定几何开关。只改标记 ——
   * 拖拽/缩放由 `WindowFrame` 据此**条件不挂**（手柄不渲染、onPointerDown 不给）。
   */
  setFixedGeometry: (instanceId, fixed) => {
    set((s) => ({
      windows: s.windows.map((x) =>
        x.instanceId === instanceId ? { ...x, fixedGeometry: fixed } : x,
      ),
    }));
    savePersisted(get());
  },

  /** ★ T23：新建工作区并切过去（名字「工作区 N」，内核零业务）。 */
  createWorkspace: () => {
    const ws = newWorkspace(nextWorkspaceIndex(get().workspaces));
    set((s) => ({ workspaces: [...s.workspaces, ws], activeWorkspaceId: ws.id }));
    savePersisted(get());
    return ws.id;
  },

  switchWorkspace: (workspaceId) => {
    if (!get().workspaces.some((w) => w.id === workspaceId)) return;
    set({ activeWorkspaceId: workspaceId });
    savePersisted(get());
  },

  /**
   * ★ T23：把某扇窗移到另一工作区。
   *
   * ★ 窗口对象**原样搬走**（几何 / 置顶 / 固定几何 / z 全不动）——
   *   这正是"隐藏而非卸载"的前提：只改归属，组件不卸载，**iframe 不重载**。
   */
  moveWindowToWorkspace: (instanceId, workspaceId) => {
    set((s) => {
      const w = s.windows.find((x) => x.instanceId === instanceId);
      if (!w || w.workspaceId === workspaceId) return {};
      if (!s.workspaces.some((k) => k.id === workspaceId)) return {};
      return {
        windows: s.windows.map((x) => (x.instanceId === instanceId ? { ...x, workspaceId } : x)),
      };
    });
    savePersisted(get());
  },

  hydrate: () => {
    const persisted = loadPersisted();
    if (!persisted) return;
    // ★ U1（2026-09-24）：恢复顶栏/底栏收放状态（判据 U1-2 跨会话保持）。
    //   字段缺失（旧快照）→ 保持当前值不动，不误折叠。
    const ui = persisted as PersistedShape & Partial<DesktopState>;
    if (typeof ui.topbarCollapsed === "boolean" || typeof ui.bottombarCollapsed === "boolean") {
      set({
        ...(typeof ui.topbarCollapsed === "boolean" ? { topbarCollapsed: ui.topbarCollapsed } : {}),
        ...(typeof ui.bottombarCollapsed === "boolean"
          ? { bottombarCollapsed: ui.bottombarCollapsed }
          : {}),
      });
    }
    // 快照损坏（windows 不是数组）→ 当作没有，不要崩
    if (!Array.isArray(persisted.windows)) return;
    const s = get();

    // ── ★ T23 迁移：v1（单工作区）→ v2（多工作区）──────────────────────
    // ★ 判据：快照里有没有 `workspaces`。没有 = v1。
    //   策略：**整体迁进「工作区 1」，一个窗口都不许丢**（契约 #3 / 验收 #5）。
    //   v1 的三个计数器原样带过来，保证 z 序与开窗级联接着排，不出现"重开一片"。
    const rawWs =
      Array.isArray(persisted.workspaces) && persisted.workspaces.length > 0
        ? persisted.workspaces
        : null;
    const workspaces: WorkspaceState[] =
      rawWs === null
        ? [
            {
              ...newWorkspace(1),
              topZ: persisted.topZ ?? 10,
              topPinZ: persisted.topPinZ ?? 0,
              seq: persisted.seq ?? 0,
            },
          ]
        : rawWs.map((w) => ({
            id: typeof w.id === "string" && w.id ? w.id : DEFAULT_WORKSPACE_ID,
            name: typeof w.name === "string" && w.name ? w.name : "工作区 1",
            topZ: typeof w.topZ === "number" ? w.topZ : 10,
            topPinZ: typeof w.topPinZ === "number" ? w.topPinZ : 0,
            seq: typeof w.seq === "number" ? w.seq : 0,
          }));

    const fallbackWsId = workspaces[0].id;
    const knownIds = new Set(workspaces.map((w) => w.id));

    // 只恢复仍注册且启用的模块的窗口
    // ★ 向后兼容（T22 验收 #4 / T23 验收 #5）：旧快照缺字段一律安全降级，
    //   **绝不因缺字段丢窗口或抛错**。
    const windows = persisted.windows
      .filter((w) => {
        const reg = s.modules[w.moduleId];
        return reg && reg.enabled;
      })
      .map((w) => {
        // ★ v1 窗口没有 workspaceId → 归「工作区 1」；
        //   若指向一个不存在的工作区（快照被手改过）→ 也退回第一个，不让窗口悬空。
        const wid =
          typeof w.workspaceId === "string" && knownIds.has(w.workspaceId)
            ? w.workspaceId
            : fallbackWsId;
        return {
          ...w,
          workspaceId: wid,
          pinned: w.pinned === true,
          fixedGeometry: w.fixedGeometry === true,
          pinZ: typeof w.pinZ === "number" ? w.pinZ : undefined,
        };
      });

    // ★ 每个工作区的计数器至少要 ≥ 它名下窗口的极值，否则 z 序 / 置顶层顺序会错乱
    const patched = workspaces.map((ws) => {
      let maxPin = 0;
      let maxZ = 0;
      for (const w of windows) {
        if (w.workspaceId !== ws.id) continue;
        if (typeof w.pinZ === "number" && w.pinZ > maxPin) maxPin = w.pinZ;
        if (typeof w.z === "number" && w.z > maxZ) maxZ = w.z;
      }
      return { ...ws, topPinZ: Math.max(ws.topPinZ, maxPin), topZ: Math.max(ws.topZ, maxZ) };
    });

    const activeWorkspaceId =
      typeof persisted.activeWorkspaceId === "string" && knownIds.has(persisted.activeWorkspaceId)
        ? persisted.activeWorkspaceId
        : fallbackWsId;

    set({ windows, workspaces: patched, activeWorkspaceId });
  },
}));

/**
 * ★ T22 渲染用 zIndex。
 * 置顶窗落在 `[PIN_BASE, …)`，普通窗仍是原来的 `z` —— **两层永不混算**：
 * 任何置顶窗的 zIndex 都大于任何普通窗的 zIndex。
 */
export function zIndexOf(w: WindowState): number {
  return w.pinned ? PIN_BASE + (w.pinZ ?? 0) : w.z;
}

/**
 * ★ T22：「当前窗」判定 —— **普通层**里 z 最高的那扇（置顶窗不参与）。
 *
 * `closeTopmost()` 与 `cycleWindow()` 共用这一条规则，避免两处各写一份而走偏。
 * 置顶窗常驻最前，若参与"当前窗"判定，快捷键会永远命中置顶那个而不是用户在看的那扇。
 */
export function topmostNormalId(windows: WindowState[]): string | null {
  let topId: string | null = null;
  let topZ = -Infinity;
  for (const w of windows) {
    if (w.pinned) continue;
    if (w.z > topZ) {
      topZ = w.z;
      topId = w.instanceId;
    }
  }
  return topId;
}

/** ★ T22：普通层按 z 升序（最底在前）。供 `cycleWindow()` 用。 */
export function normalStack(windows: WindowState[]): WindowState[] {
  return windows
    .filter((w) => !w.pinned)
    .slice()
    .sort((a, b) => a.z - b.z);
}

function fallbackManifest(moduleId: string, s: DesktopState): ModuleManifest {
  const reg = s.modules[moduleId];
  if (reg) return reg.manifest;
  return {
    id: moduleId,
    name: moduleId,
    version: "0",
    kind: "builtin",
    entry: "",
    window: { w: 480, h: 320 },
  };
}

export { snap, TOPBAR, DOCK, PIN_BASE };
