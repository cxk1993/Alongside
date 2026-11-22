import { useEffect, useMemo, useRef, useState } from "react";

import { ToastHost } from "@/shared/components/Toast";
import { initTheme, useTheme } from "@/shared/styles/theme";
import { pluginHost } from "./ModuleRegistry";
import { useDesktopStore, normalStack, topmostNormalId, type WindowState } from "./store";
import { AuthGate } from "./AuthGate";
import { PluginProvider } from "./plugins/PluginContext";
import { logout } from "@/shared/api/auth";
import { Dock } from "./Dock";
import {
  buildCommands,
  filterCommands,
  groupCommands,
  isRunnable,
  type Command,
} from "./commands";
import { TimeWidget } from "./shell/TimeWidget";
import { useShortcuts, type ShortcutHandlers } from "./Shortcuts";
import { TopBar } from "./TopBar";
// ★ 两屏（主人 2026-11-22 13:48）：人的主屏组件。壳只按工作区决定「渲染谁」。
import { HomeMyToday } from "@/apps/home/HomeMyToday";
import { WindowManager } from "./WindowManager";
import { DesktopSidebar, readSideCollapsed } from "./shell/DesktopSidebar";
import { SlotHost } from "./slots/SlotHost";

/**
 * ★ T23：只取**当前工作区**的窗口 —— 快捷键作用于"你正在看的那个桌面"。
 * 否则快捷键会命中隐藏工作区里的窗，出现"关了个看不见的东西"。
 */
function activeWindows(): WindowState[] {
  const s = useDesktopStore.getState();
  return s.windows.filter((w) => w.workspaceId === s.activeWorkspaceId);
}

/**
 * 关闭 z-index 最高的窗口（即当前聚焦窗口）。
 *
 * ★ T22：「当前窗」判定**按层**做 —— 置顶窗不参与（契约：「置顶窗不参与'当前窗'判定」）。
 * 否则置顶窗的 zIndex 恒高于普通窗，这个快捷键会永远关掉置顶那个，
 * 而不是用户正在看的那一个（症状："关窗口关错人"）。
 * ★ T23：再加上"只在本工作区内"。
 * ★ 规则抽到 `store.topmostNormalId()`，与 `cycleWindow` 共用同一条判定。
 */
function closeTopmost(): void {
  const s = useDesktopStore.getState();
  const topId = topmostNormalId(activeWindows());
  if (topId) s.closeWindow(topId);
}

/**
 * 在打开的窗口间循环聚焦（把最顶层下面的那扇提到最前）。
 *
 * ★ T22：与 `closeTopmost` 是**同一条判定的两处应用** ——
 * "把某一扇提到最前"是**普通层**的叠放操作；置顶层顺序由 `pinZ` 决定，
 * 聚焦改不动它，所以置顶层不参与循环。卡里只点名了 `closeTopmost`，此处按同一判定处理。
 * ★ T23：同样限定在当前工作区内。
 */
function cycleWindow(): void {
  const s = useDesktopStore.getState();
  const stack = normalStack(activeWindows());
  if (stack.length === 0) return;
  const below = stack.length > 1 ? stack[stack.length - 2] : stack[0];
  s.focusWindow(below.instanceId);
}

/**
 * 桌面容器：顶栏 + 窗口层 + 坞 + 全局搜索 + Toast。
 *
 * ★ 2026-11-22（ISSUE-017）：不再拉构建期 `modules.json` —— 注册源只剩后端插件清单。
 *   窗口几何仍从 localStorage 恢复，但 hydrate 必须等「模块首次非空」后跑一次（见下）。
 */
/**
 * ★ 2026-11-22（汐瑶 · 收壳）：主墙空态。
 * 没有任何插件贡献 desktop.widget 时，主区**不能**留成一片空桌面 ——
 * 那正是「换了壳但看起来还是旧桌面」的观感来源。这里给一句「去哪儿」的话。
 */
function WallEmpty() {
  return (
    <div className="wall__empty">
      <div className="wall__empty-title">共处工作台</div>
      <p className="wall__empty-hint">
        工作台上还没有面板。按 Ctrl/⌘ + K 打开能力面板；AI 的提案、它看见的处境和做过的动作都会出现在这里。
      </p>
    </div>
  );
}

export function Desktop() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const tidy = useDesktopStore((s) => s.tidyDesktop);
  const modules = useDesktopStore((s) => s.modules);

  useEffect(() => {
    initTheme();
  }, []);

  /**
   * ★ ISSUE-017（2026-11-22）：hydrate 的两个硬前提。
   *   ① **必须等 modules 非空**：`store.hydrate` 的 filter 是 `reg && reg.enabled`，
   *      挂载瞬间 modules 还是空的（后端清单登录后才回来）⇒ 所有窗口被过滤掉 = 刷新丢布局。
   *   ② **必须只跑一次**：插件启用/停用/安装都会让 `modules` 换引用；无守卫的话
   *      每次 refresh 都把桌面重置回 localStorage 快照 —— 用户刚拖的窗会被弹回去。
   *   （比丢 2026-11-22 复核 ISSUE-017 抓到的正是这两条。）
   */
  const hydrated = useRef(false);
  useEffect(() => {
    if (hydrated.current || Object.keys(modules).length === 0) return;
    hydrated.current = true;
    useDesktopStore.getState().hydrate();
  }, [modules]);

  const openSearch = () => {
    setQuery("");
    setActiveIdx(0);
    setSearchOpen(true);
  };
  const closeSearch = () => setSearchOpen(false);

  const handlers: ShortcutHandlers = {
    onToggleSearch: openSearch,
    onEscape: () => {
      if (searchOpen) closeSearch();
      else closeTopmost();
    },
    onCloseWindow: closeTopmost,
    onCycleWindow: cycleWindow,
  };
  useShortcuts(handlers);

  // ★ T17：命令面板的数据源在下方（依赖顶/底栏状态与工作区，须在本组 hooks 之后计算）。

  // ★ U1（2026-09-24）：顶栏/底栏收放（状态与持久化在 desktop store；判据 U1-1/U1-2）
  const topbarCollapsed = useDesktopStore((s) => s.topbarCollapsed);
  const bottombarCollapsed = useDesktopStore((s) => s.bottombarCollapsed);
  const toggleTopbar = useDesktopStore((s) => s.toggleTopbar);
  const toggleBottombar = useDesktopStore((s) => s.toggleBottombar);
  // ★ 主人④（2026-09-24）：一键最小化所有未固定窗口
  const minimizeAllUnpinned = useDesktopStore((s) => s.minimizeAllUnpinned);

  // ═══ ★ T17：命令面板（Dock 隐藏后的主入口）═══
  // 数据源与检索规则全在 `kernel/commands.ts`（纯函数、可单测）；
  // 这里只负责把 store 的既有方法与**当前状态**喂进去（标题要随状态变：
  // Dock 收起时显示「显示 Dock」而不是「隐藏 Dock」——按钮文案不许与事实相反）。
  const workspaces = useDesktopStore((s) => s.workspaces);
  const activeWorkspaceId = useDesktopStore((s) => s.activeWorkspaceId);
  const createWorkspace = useDesktopStore((s) => s.createWorkspace);
  const switchWorkspace = useDesktopStore((s) => s.switchWorkspace);
  /** ★ 两屏：**第 1 个工作区 = 人的主屏**（我的今天），其余工作区 = AI 工作面（collab 原样）。 */
  const isHomeWorkspace = workspaces[0]?.id === activeWorkspaceId;
  const toggleTheme = useTheme((s) => s.toggle);

  // 「插件管理」的落点用**能力发现**（与 WindowFrame.tsx:56 同一口径）：
  // 内核不认识任何具体插件 id，没有提供者就不出这条命令。
  const managerId = useMemo(() => {
    for (const reg of Object.values(modules)) {
      if (!reg.enabled) continue;
      const caps = (reg.manifest.provides ?? []) as readonly string[];
      if (caps.includes("plugin.manage")) return reg.manifest.id;
    }
    return undefined;
  }, [modules]);

  const commands = useMemo(
    () =>
      buildCommands({
        modules,
        workspaces,
        activeWorkspaceId,
        topbarCollapsed,
        dockCollapsed: bottombarCollapsed,
        openModule: (id) => pluginHost.open(id),
        openPluginManager: managerId ? () => pluginHost.open(managerId) : undefined,
        toggleTopbar,
        toggleBottombar,
        tidyDesktop: tidy,
        minimizeAllUnpinned,
        createWorkspace,
        switchWorkspace,
        toggleTheme,
      }),
    [
      modules,
      workspaces,
      activeWorkspaceId,
      topbarCollapsed,
      bottombarCollapsed,
      managerId,
      tidy,
      toggleTopbar,
      toggleBottombar,
      minimizeAllUnpinned,
      createWorkspace,
      switchWorkspace,
      toggleTheme,
    ],
  );
  /** 键盘上下移动的下标作用在**过滤后的扁平列表**上 —— 与视觉顺序一致。 */
  const items = useMemo(() => filterCommands(commands, query), [commands, query]);
  const blocks = useMemo(() => groupCommands(items), [items]);

  /** 执行一条命令：先关面板再跑（跑的过程中面板还盖着桌面 = 看不见自己做了什么）。 */
  const runCommand = (cmd: Command | undefined) => {
    if (!isRunnable(cmd)) return;
    closeSearch();
    cmd.run();
  };

  // ═══ ★ 收放罗盘（主人三板拍板）═══
  // 顶/底栏状态走 store（U1 既有）；左右栏状态在 DesktopSidebar 内部 localStorage
  //（键 `alongside.dock-{left,right}.collapsed`）→ 罗盘经 CustomEvent 通知其同步。
  const [compassOpen, setCompassOpen] = useState(false);
  const [leftCollapsed, setLeftCollapsed] = useState(() => readSideCollapsed("left"));
  const [rightCollapsed, setRightCollapsed] = useState(() => readSideCollapsed("right"));
  const setSideCollapsed = (side: "left" | "right", collapsed: boolean) => {
    try {
      localStorage.setItem(`alongside.dock-${side}.collapsed`, collapsed ? "1" : "0");
    } catch {
      /* 存储不可用忽略 */
    }
    window.dispatchEvent(
      new CustomEvent("alongside:sidebar-collapse", { detail: { side, collapsed } }),
    );
    if (side === "left") setLeftCollapsed(collapsed);
    else setRightCollapsed(collapsed);
  };
  const allCollapsed =
    topbarCollapsed && bottombarCollapsed && leftCollapsed && rightCollapsed;
  const toggleAllCollapsed = () => {
    const target = !allCollapsed; // true=全收 false=全展
    if (topbarCollapsed !== target) toggleTopbar();
    if (bottombarCollapsed !== target) toggleBottombar();
    setSideCollapsed("left", target);
    setSideCollapsed("right", target);
  };

  // ★（主人现场令，2026-09-25）：**罗盘按钮可随意拖动** ——
  //   位置入 localStorage（`alongside.compass.pos`），刷新保持；null = 默认右上角。
  //   拖拽与点击区分：位移超 4px 视为拖动（不触发开关）。
  const [compassPos, setCompassPos] = useState<{ x: number; y: number } | null>(() => {
    try {
      const raw = localStorage.getItem("alongside.compass.pos");
      return raw ? (JSON.parse(raw) as { x: number; y: number }) : null;
    } catch {
      return null;
    }
  });
  const compassDrag = useRef({ moved: false });
  const onCompassPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    const el = e.currentTarget;
    const rect = el.getBoundingClientRect();
    const offX = e.clientX - rect.left;
    const offY = e.clientY - rect.top;
    compassDrag.current.moved = false;
    const move = (ev: PointerEvent) => {
      const dx = Math.abs(ev.clientX - (rect.left + offX));
      const dy = Math.abs(ev.clientY - (rect.top + offY));
      if (dx > 4 || dy > 4) compassDrag.current.moved = true;
      const x = Math.max(0, Math.min(window.innerWidth - rect.width, ev.clientX - offX));
      const y = Math.max(0, Math.min(window.innerHeight - rect.height, ev.clientY - offY));
      setCompassPos({ x, y });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setCompassPos((p) => {
        if (p) {
          try {
            localStorage.setItem("alongside.compass.pos", JSON.stringify(p));
          } catch {
            /* 存储不可用忽略 */
          }
        }
        return p;
      });
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onCompassClick = () => {
    // 拖动过就不算点击（避免"拖完顺手弹开/收起"）
    if (compassDrag.current.moved) {
      compassDrag.current.moved = false;
      return;
    }
    setCompassOpen((v) => !v);
  };

  return (
    <AuthGate>
      {/* ★ T02 预留集成缝正式接线（2026-09-26 hermes）：挂载后拉 /api/v1/plugins
          → syncPluginsToStore → Dock 出现后端注册模块（ai-chat/query/export 等），
          并加载各插件入口贡献 E5 sidecar / U3 dock-right 等 slot。
          必须在 AuthGate 之内：登录后才 refresh，避免登录前 401 空跑。 */}
      <PluginProvider>
        <div
          className={
            "desktop" +
            (topbarCollapsed ? " desktop--top-collapsed" : "") +
            (bottombarCollapsed ? " desktop--bottom-collapsed" : "")
          }
        >
        {topbarCollapsed ? (
          // ★ U1-4 + 顶栏收起后时钟仍可达（悬浮胶囊，全收态幸存物之一）
          <div className="desktop__clock-pill">
            <TimeWidget />
          </div>
        ) : (
          <TopBar
            onOpenSearch={openSearch}
            onOpenQuery={() => pluginHost.open("query")}
            onTidy={tidy}
            onLogout={() => void logout()}
            onMinimizeAll={minimizeAllUnpinned}
          />
        )}
        {/* ★ 收放罗盘（主人三板）：右上角常驻「罗盘」按钮；
            全收态它是唯一入口，其余三栏把手已撤（主人：只留罗盘）。 */}
        <button
          type="button"
          className="desktop__compass-btn"
          style={compassPos ? { left: compassPos.x, top: compassPos.y, right: "auto" } : undefined}
          aria-expanded={compassOpen}
          aria-haspopup="menu"
          title="收放罗盘（可拖动位置；点击展开选择性收放四栏）"
          onPointerDown={onCompassPointerDown}
          onClick={onCompassClick}
        >
          ◉ 罗盘
        </button>
        {compassOpen && (
          <div
            className="desktop__compass"
            role="menu"
            aria-label="收放罗盘"
            style={
              compassPos
                ? { left: compassPos.x, top: compassPos.y + 46, right: "auto" }
                : undefined
            }
            onKeyDown={(e) => {
              // ★ 八维升级（2026-09-25）：「功能/无障碍」—— WAI-ARIA menu 键盘导航。
              //   方向键 = 罗盘四向（↑顶栏 / →右栏 / ↓底栏 / ←左栏，与视觉方位一致）；
              //   Enter/Space = 中心（全收/全展）；Esc = 关闭；Tab = 常规焦点循环。
              switch (e.key) {
                case "ArrowUp":
                  e.preventDefault();
                  toggleTopbar();
                  break;
                case "ArrowRight":
                  e.preventDefault();
                  setSideCollapsed("right", !rightCollapsed);
                  break;
                case "ArrowDown":
                  e.preventDefault();
                  toggleBottombar();
                  break;
                case "ArrowLeft":
                  e.preventDefault();
                  setSideCollapsed("left", !leftCollapsed);
                  break;
                case "Enter":
                case " ":
                  // 仅在焦点不在具体扇区按钮上时，才由容器接管为「全收/全展」
                  if (e.target === e.currentTarget) {
                    e.preventDefault();
                    toggleAllCollapsed();
                  }
                  break;
                case "Escape":
                  setCompassOpen(false);
                  break;
                default:
                  break;
              }
            }}
          >
            <button
              type="button"
              role="menuitem"
              className={`desktop__compass-slice desktop__compass-slice--n${topbarCollapsed ? " is-collapsed" : ""}`}
              aria-label={topbarCollapsed ? "展开顶栏" : "收起顶栏"}
              onClick={toggleTopbar}
            >
              顶栏
            </button>
            <button
              type="button"
              role="menuitem"
              className={`desktop__compass-slice desktop__compass-slice--e${rightCollapsed ? " is-collapsed" : ""}`}
              aria-label={rightCollapsed ? "展开右栏" : "收起右栏"}
              onClick={() => setSideCollapsed("right", !rightCollapsed)}
            >
              右栏
            </button>
            <button
              type="button"
              role="menuitem"
              className={`desktop__compass-slice desktop__compass-slice--s${bottombarCollapsed ? " is-collapsed" : ""}`}
              aria-label={bottombarCollapsed ? "展开底栏" : "收起底栏"}
              onClick={toggleBottombar}
            >
              底栏
            </button>
            <button
              type="button"
              role="menuitem"
              className={`desktop__compass-slice desktop__compass-slice--w${leftCollapsed ? " is-collapsed" : ""}`}
              aria-label={leftCollapsed ? "展开左栏" : "收起左栏"}
              onClick={() => setSideCollapsed("left", !leftCollapsed)}
            >
              左栏
            </button>
            <button
              type="button"
              role="menuitem"
              className="desktop__compass-center"
              aria-label={allCollapsed ? "全部展开" : "全部收起"}
              onClick={toggleAllCollapsed}
            >
              {allCollapsed ? "全展" : "全收"}
            </button>
          </div>
        )}
        <div className="desktop__mid">
          {/* ★ 2026-11-22（汐瑶 · 收壳 P0-a）：**主墙是主区**。
              旧结构把主墙（desktop.widget）挂在 `.windows` 里当壁纸
              （position:absolute + pointer-events:none + 白名单），窗口才是主区 ——
              这就是主人看到的「半改没改」：新墙与旧桌面并置，且新墙还点不到。
              现在：主墙吃满主区并自己滚；窗口降级为绝对定位的细节层（默认零窗口）。 */}
          <main
            className="wall"
            data-testid="desktop-wall"
            aria-label={isHomeWorkspace ? "我的今天" : "共处工作台"}
          >
            {isHomeWorkspace ? (
              <HomeMyToday />
            ) : (
              <SlotHost slot="desktop.widget" className="wall__surface" fallback={<WallEmpty />} />
            )}
          </main>
          {/* 细节层：容器不吃鼠标，窗口自身 auto —— 否则透明覆盖层会挡住主墙点击 */}
          <div className="windows">
            <WindowManager />
          </div>
          {/* 浮层侧栏：默认收起、展开时浮在主墙之上，不再挤占主区宽度 */}
          <DesktopSidebar side="left" />
          <DesktopSidebar side="right" />
        </div>
        {!bottombarCollapsed && <Dock />}
          <ToastHost />

          {searchOpen ? (
            <>
              <div className="search-backdrop" onClick={closeSearch} />
              <div className="search" role="dialog" aria-modal="true" aria-label="全局搜索">
                <input
                  className="search__input"
                  autoFocus
                  placeholder="搜索或输入命令…"
                  value={query}
                  onChange={(e) => {
                    setQuery(e.target.value);
                    setActiveIdx(0);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown") {
                      e.preventDefault();
                      setActiveIdx((i) => Math.min(items.length - 1, i + 1));
                    } else if (e.key === "ArrowUp") {
                      e.preventDefault();
                      setActiveIdx((i) => Math.max(0, i - 1));
                    } else if (e.key === "Enter") {
                      // ★ T17：Enter 执行高亮命令（含模块开窗）。先关面板再跑。
                      runCommand(items[activeIdx]);
                    } else if (e.key === "Escape") {
                      e.stopPropagation(); // ★ BUG-T02-1：Esc 只关搜索层，不许冒泡成"关窗"
                      closeSearch();
                    }
                  }}
                />
                <div className="search__list" role="listbox" aria-label="命令列表">
                  {items.length ? (
                    blocks.map((b) => (
                      <div key={b.group} className="search__group" role="group" aria-label={b.group}>
                        {/* 分组标题不进键盘序列（上下键走的是扁平 items 下标） */}
                        <div className="search__group-title" aria-hidden="true">
                          {b.group}
                        </div>
                        {b.commands.map((c) => {
                          // 用扁平列表里的真实下标，保证「鼠标悬停高亮」与「上下键高亮」是同一个东西
                          const i = items.indexOf(c);
                          return (
                            <div
                              key={c.id}
                              className={`search__item${i === activeIdx ? " search__item--active" : ""}`}
                              role="option"
                              aria-selected={i === activeIdx}
                              data-testid="command-item"
                              onMouseEnter={() => setActiveIdx(i)}
                              onClick={() => runCommand(c)}
                            >
                              {c.title}
                            </div>
                          );
                        })}
                      </div>
                    ))
                  ) : (
                    <div className="search__empty">没有匹配的命令或模块</div>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </div>
      </PluginProvider>
    </AuthGate>
  );
}
