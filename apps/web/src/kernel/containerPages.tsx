/**
 * 容器页装配器 —— 容器窗口的页签**只从 manifest.tabs 来**（+ moduleGraph 的补页登记）。
 *
 * 之前每个容器 App 自己手写一份 pages 数组（system 7 页 / knowledge 3 页 / growth 3 页），
 * 与 Dock 的吸收表各写一遍 → 实测分叉（diary 被吸收却没有页签 = 入口消失）。
 * 现在统一走这里：页签顺序 = manifest.tabs 顺序，页内容 = 该 tab.entry 指向模块的 App
 * （内核按目录约定 `src/apps/<dir>/index.tsx` 解析，无需手写映射表）。
 *
 * 语义：
 *   · tab.entry 指向的模块**存在且有 entry** → 懒加载其 App（与 WindowFrame 同一条加载路径）；
 *   · tab.entry 缺省、或模块无 entry（纯 API）→ 薄壳「已接入（后端服务）」语义页；
 *   · 容器 manifest 尚未进注册表 / tabs 为空 → 空容器提示页（不白屏、不假渲染）。
 */
import { Component, lazy, Suspense, useMemo } from "react";
import type { ComponentType, ErrorInfo, LazyExoticComponent, ReactNode } from "react";

import type { MultitabPage } from "./MultitabFrame";
import { resolveLoader } from "./ModuleRegistry";
import { effectiveTabsOf, type DeclaredTab } from "./moduleGraph";
import { useDesktopStore } from "./store";

const LAZY_CACHE = new Map<string, LazyExoticComponent<ComponentType>>();

/** 模块 id + 其 manifest.entry → 稳定的 lazy 组件（同键复用，避免每次 render 重挂载）。 */
function lazyPage(moduleId: string, entry: string): LazyExoticComponent<ComponentType> {
  const cacheKey = `${moduleId}\u0000${entry}`;
  const hit = LAZY_CACHE.get(cacheKey);
  if (hit) return hit;
  const loader = resolveLoader(entry);
  const comp = lazy(async () => {
    const mod = await loader();
    const Component_ = (mod.default as { Component?: ComponentType } | undefined)?.Component;
    if (!Component_) throw new Error(`模块「${moduleId}」入口缺少 Component`);
    return { default: Component_ };
  });
  LAZY_CACHE.set(cacheKey, comp);
  return comp;
}

function PageFallback({ testId }: { testId: string }) {
  return (
    <div className="win__placeholder-text" data-testid={testId}>
      加载中…
    </div>
  );
}

/** 页内错误边界：某模块 App 加载失败只坏这一页，容器窗口不白屏。 */
interface BoundaryState {
  failed: boolean;
}
class PageBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[container-pages] 页组件渲染失败", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.failed) {
      return (
        <div className="win__placeholder" role="status">
          <div className="win__placeholder-title">该页加载失败</div>
          <div className="win__placeholder-text">其余页签不受影响，可切换后再试。</div>
        </div>
      );
    }
    return this.props.children;
  }
}

/** 薄壳语义页：与 WindowFrame V5 的 backend 变体同语义（类名同源 win__placeholder）。 */
export function BackendOnlyPage({ label }: { label: string }) {
  return (
    <div className="win__placeholder" role="status" data-testid={`system-backend-page-${label}`}>
      <div className="win__placeholder-icon" aria-hidden="true">
        ⚙
      </div>
      <div className="win__placeholder-title">已接入（后端服务）</div>
      <div className="win__placeholder-text">该模块已接入：提供后端 API 服务，无独立窗口界面。</div>
    </div>
  );
}

function EmptyContainerPage({ containerId }: { containerId: string }) {
  return (
    <div className="win__placeholder" role="status" data-testid={`container-empty-${containerId}`}>
      <div className="win__placeholder-title">该容器未声明页签</div>
      <div className="win__placeholder-text">
        容器型模块应在 manifest.tabs 里声明页签（{`{ key, label, entry? }`}）。
      </div>
    </div>
  );
}

/**
 * 按容器 manifest.tabs 装配页签（React hook：订阅注册表）。
 *
 * @param containerId 容器模块 id（system / knowledge / growth / schedule …）
 */
export function useContainerPages(containerId: string): MultitabPage[] {
  const modules = useDesktopStore((s) => s.modules);
  const manifest = modules[containerId]?.manifest;
  const declared = (manifest as { tabs?: readonly DeclaredTab[] } | undefined)?.tabs;

  return useMemo(() => {
    const tabs = effectiveTabsOf(containerId, declared);
    if (tabs.length === 0) {
      return [
        {
          key: `__empty__${containerId}`,
          label: "页签",
          content: <EmptyContainerPage containerId={containerId} />,
        },
      ];
    }
    return tabs.map((tab): MultitabPage => {
      const target = tab.entry ? modules[tab.entry] : undefined;
      const entrySpec = target?.manifest.entry;
      if (!tab.entry || !entrySpec) {
        // 无 entry / 目标未注册 / 目标无前端入口 → 后端服务薄壳页
        return { key: tab.key, label: tab.label, content: <BackendOnlyPage label={tab.key} /> };
      }
      const App = lazyPage(tab.entry, entrySpec);
      return {
        key: tab.key,
        label: tab.label,
        content: (
          <PageBoundary>
            <Suspense fallback={<PageFallback testId={`container-page-loading-${tab.key}`} />}>
              <App />
            </Suspense>
          </PageBoundary>
        ),
      };
    });
  }, [containerId, declared, modules]);
}
