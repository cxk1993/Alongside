/**
 * T30 · ★ 顺修 T02 的回归测试（锁死防回归）。
 *
 * 2026-09-20 口径（Qoder CN 定案）：原「T02 两疑点」经 CDP 可信输入重测**均已撤销**
 * （合成键盘事件假象）。下列两组测试**保留**——它们锁的是两处**防御性增强**，不改变任何已验收行为：
 *
 *   增强① 输入守卫（`shared/hooks/useHotkey.ts`）：裸键 + 焦点在可编辑元素 → 跳过全局快捷键。
 *          ★ 它同时是 BUG-T02-1 的等效修法之一（搜索框里按 Esc 不会冒泡成关窗）。
 *   增强② 新窗初始尺寸钳进视口（`kernel/store.ts` defaultGeo）：视口比 manifest 要求还窄时不把窗开到屏幕外。
 *
 * 另含 ★ BUG-T02-1（P2，Qoder 定案立案）的回归测试：
 *   搜索浮层开着 + 桌面有窗，按一次 Esc → 只关搜索、顶窗不动。
 *   根因：搜索框 Escape 分支 `closeSearch()` 同步重渲染后，window 监听闭包在同一按键上又走 `closeTopmost()`。
 *   修法（双保险）：① 搜索框 onKeyDown Escape 分支 `e.stopPropagation()`（原生冒泡停止 → window 收不到）；
 *                ② useHotkey 输入守卫（焦点在可编辑元素时裸键直接跳过）。证据 L2-32-t02-esc-narrow.png。
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Desktop } from "./Desktop";
import { makeWorkspace, useDesktopStore } from "./store";
import { useHotkey } from "@/shared/hooks/useHotkey";
import { setToken } from "@/shared/api/client";
import type { ModuleManifest } from "./types";

// ───────────────────────── 疑点① ─────────────────────────
function EscProbe({ onEsc }: { onEsc: () => void }): React.JSX.Element {
  useHotkey("escape", onEsc);
  return <input data-testid="inp" defaultValue="" />;
}

describe("★ T02 疑点①：Esc 在输入框里不再关窗", () => {
  it("焦点在输入框里按 Esc → **不**触发全局关窗", () => {
    const onEsc = vi.fn();
    render(<EscProbe onEsc={onEsc} />);
    const inp = screen.getByTestId("inp");
    inp.focus();
    fireEvent.keyDown(inp, { key: "Escape" });
    expect(onEsc).not.toHaveBeenCalled(); // ★ 修复点：以前这里会被调用
  });

  it("桌面空白处按 Esc → 仍然关窗（原语义保留）", () => {
    const onEsc = vi.fn();
    render(<EscProbe onEsc={onEsc} />);
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(onEsc).toHaveBeenCalledTimes(1);
  });

  it("★ 带修饰键的组合不受输入守卫影响（Ctrl+K 仍全局可用）", () => {
    let fired = 0;
    function KProbe(): React.JSX.Element {
      useHotkey("mod+k", () => {
        fired += 1;
      });
      return <input data-testid="inp2" defaultValue="" />;
    }
    render(<KProbe />);
    const inp = screen.getByTestId("inp2");
    inp.focus();
    fireEvent.keyDown(inp, { key: "k", ctrlKey: true });
    expect(fired).toBe(1); // ★ mod 组合在输入框里也生效（搜索入口就是这么用的）
  });
});

// ───────────────────────── 疑点② ─────────────────────────
const BIG: ModuleManifest = {
  id: "big",
  name: "大窗",
  version: "0",
  kind: "builtin",
  entry: "@/apps/__none__",
  window: { w: 5000, h: 3000, minW: 360, minH: 240 },
};

describe("★ T02 疑点②：窄屏上开大窗 → 尺寸被钳进视口", () => {
  it("新窗宽不超过视口宽、高不超过视口可用高", () => {
    useDesktopStore.setState({
      modules: { big: { manifest: BIG, enabled: true } },
      windows: [],
      workspaces: [makeWorkspace(1)],
      activeWorkspaceId: "ws1",
    });
    useDesktopStore.getState().openWindow("big");

    const geo = useDesktopStore.getState().windows[0].geo;
    expect(geo.w).toBeLessThanOrEqual(window.innerWidth); // ★ 以前是 5000 > 视口
    expect(geo.h).toBeLessThanOrEqual(window.innerHeight);
    // ★ 不低于 minW/minH（钳制不能把窗压到不可用）
    expect(geo.w).toBeGreaterThanOrEqual(BIG.window.minW ?? 0);
    expect(geo.h).toBeGreaterThanOrEqual(BIG.window.minH ?? 0);
  });
});

// ───────────────────────── ★ BUG-T02-1（P2，Qoder 定案） ─────────────────────────
const MOD: ModuleManifest = {
  id: "escbug",
  name: "Esc 双杀演示模块",
  version: "0",
  kind: "builtin",
  entry: "@/apps/__none__",
  window: { w: 600, h: 400 },
};

describe("★ BUG-T02-1：搜索浮层开着 + 桌面有窗，一次 Esc 只关搜索、不关窗", () => {
  it("搜索层与顶窗共存时 Esc → 搜索关、窗留；再按 Esc → 窗关", () => {
    localStorage.clear();
    setToken("test-token"); // ★ 桌面壳有鉴权门，先种 token 进桌面
    useDesktopStore.setState({
      modules: { escbug: { manifest: MOD, enabled: true } },
      windows: [],
      workspaces: [makeWorkspace(1)],
      activeWorkspaceId: "ws1",
    });

    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Desktop />
      </QueryClientProvider>,
    );
    useDesktopStore.getState().openWindow("escbug");
    expect(useDesktopStore.getState().windows).toHaveLength(1);

    // Ctrl+K 开搜索（window 级监听，不经过 React 委托）
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = screen.getByRole("dialog", { name: "全局搜索" });
    expect(dialog).toBeTruthy();

    // 焦点在搜索框（autoFocus），按一次 Esc → 只关搜索
    const input = screen.getByPlaceholderText("搜索或输入命令…");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "全局搜索" })).toBeNull();
    // ★ 修复点：以前这里窗也一起被关了
    expect(useDesktopStore.getState().windows).toHaveLength(1);

    // 对照：搜索层已关，非输入焦点再按 Esc → 正常关窗（原语义）
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(useDesktopStore.getState().windows).toHaveLength(0);
  });
});
