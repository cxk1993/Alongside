import { useEffect, useMemo, useRef, useState } from "react";

import { ToastHost } from "@/shared/components/Toast";
import { initTheme } from "@/shared/styles/theme";
import { pluginHost, registerModules } from "./ModuleRegistry";
import { useDesktopStore, normalStack, topmostNormalId, type WindowState } from "./store";
import { installIframeAuthBridge } from "./iframeAuthBridge";
import type { ModuleManifest } from "./types";
import { AuthGate } from "./AuthGate";
import { PluginProvider } from "./plugins/PluginContext";
import { logout } from "@/shared/api/auth";
import { Dock } from "./Dock";
import { TimeWidget } from "./shell/TimeWidget";
import { useShortcuts, type ShortcutHandlers } from "./Shortcuts";
import { TopBar } from "./TopBar";
import { WindowManager } from "./WindowManager";
import { DesktopSidebar } from "./shell/DesktopSidebar";

const MODULES_URL = "/modules.json";

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
 * 启动时拉取 modules.json 注册模块，并 hydrate 已持久化的窗口几何。
 */
/** ★ 罗盘用：读侧栏折叠初值（键与 DesktopSidebar 的 COLLAPSE_KEY 同族）。 */
function readSideCollapsed(side: "left" | "right"): boolean {
  try {
    return localStorage.getItem(`alongside.dock-${side}.collapsed`) === "1";
  } catch {
    return false;
  }
}

export function Desktop() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIdx, setActiveIdx] = useState(0);
  const tidy = useDesktopStore((s) => s.tidyDesktop);
  const modules = useDesktopStore((s) => s.modules);

  useEffect(() => {
    initTheme();
    // ★ B2：跨源自家 iframe 的登录态握手桥（origin 白名单空起步，fail closed）
    const bridge = installIframeAuthBridge();
    let cancelled = false;
    fetch(MODULES_URL)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((list: ModuleManifest[]) => {
        if (cancelled) return;
        registerModules(list);
        useDesktopStore.getState().hydrate();
      })
      .catch(() => {
        /* 没有 modules.json：桌面为空，但不白屏（内核不依赖任何具体模块） */
      });
    return () => {
      cancelled = true;
      bridge.dispose();
    };
  }, []);

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

  const items = useMemo(() => {
    const q = query.trim().toLowerCase();
    return Object.values(modules)
      .filter((r) => r.enabled)
      .map((r) => r.manifest)
      .filter((m) => !q || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q));
  }, [query, modules]);

  // ★ U1（2026-09-24）：顶栏/底栏收放（状态与持久化在 desktop store；判据 U1-1/U1-2）
  const topbarCollapsed = useDesktopStore((s) => s.topbarCollapsed);
  const bottombarCollapsed = useDesktopStore((s) => s.bottombarCollapsed);
  const toggleTopbar = useDesktopStore((s) => s.toggleTopbar);
  const toggleBottombar = useDesktopStore((s) => s.toggleBottombar);
  // ★ 主人④（2026-09-24）：一键最小化所有未固定窗口
  const minimizeAllUnpinned = useDesktopStore((s) => s.minimizeAllUnpinned);

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
            {/* U3：桌面级双侧栏（左=导航/结构，右=摘要/情境；折叠持久化） */}
            <DesktopSidebar side="left" />
            <div className="windows">
              <WindowManager />
            </div>
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
                  placeholder="搜索模块…"
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
                      const m = items[activeIdx];
                      if (m) {
                        closeSearch();
                        pluginHost.open(m.id);
                      }
                    } else if (e.key === "Escape") {
                      e.stopPropagation(); // ★ BUG-T02-1：Esc 只关搜索层，不许冒泡成"关窗"
                      closeSearch();
                    }
                  }}
                />
                <div className="search__list">
                  {items.length ? (
                    items.map((m, i) => (
                      <div
                        key={m.id}
                        className={`search__item${i === activeIdx ? " search__item--active" : ""}`}
                        onMouseEnter={() => setActiveIdx(i)}
                        onClick={() => {
                          closeSearch();
                          pluginHost.open(m.id);
                        }}
                      >
                        {m.name}
                      </div>
                    ))
                  ) : (
                    <div className="search__empty">没有匹配的模块</div>
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
