/**
 * 容器页装配器 —— 容器窗口的页签**只从 manifest.tabs 来**（无任何前端补齐表）。
 *
 * 之前每个容器 App 自己手写一份 pages 数组（system 7 页 / knowledge 3 页 / growth 3 页），
 * 与 Dock 的吸收表各写一遍 → 实测分叉（diary 被吸收却没有页签 = 入口消失）。
 * 现在统一走这里：页签顺序 = manifest.tabs 顺序，页内容 = 该 tab.entry 指向模块的 App
 * （内核按目录约定 `src/apps/<dir>/index.tsx` 解析，无需手写映射表）。
 *
 * 语义：
 *   · tab.entry 指向的模块**存在且有 entry 且有 App 文件** → 懒加载其 App
 *     （与 WindowFrame 同一条加载路径）。★ 按 `entry` 加载，不按 `key` ——
 *     页名与模块名不一致本身是协议允许的形态（页签 key 只是页标识）。
 *     ⚠️ **但 `schedule` 的 `{ key: "study", entry: "todo" }` 是待修数据，不是设计**：
 *     真实学业页是 `apps/study/StudyApp.tsx`（不是模块，也没有 `index.tsx`），
 *     按 entry 加载会渲染成第二个待办页。协议目前缺「容器内联页」态，
 *     在该态补上之前 **`schedule` 不得迁到 `useContainerPages`**（仍由 ScheduleApp 手写页）。
 *   · 同理 `pi-agent`：manifest 已用 `absorbs` 声明了 agents/ai-chat 归口，但它
 *     零页签（PiAgentApp 自渲染「对话/编排」两页）—— 迁容器页会落到「未声明页签」
 *     空壳，迁之前必须先补 tabs。
 *   · tab.entry 缺省、或模块无 entry（纯 API）→ 薄壳「已接入（后端服务）」语义页；
 *   · tab.entry 指向的模块**不在清单里** → 薄壳「模块未注册」（清单滞后不白屏）；
 *   · 模块声明了 entry 但 App 文件缺失 → 薄壳「入口缺失」（不炸成「加载失败」）；
 *   · 容器 manifest 尚未进注册表 / tabs 为空 → 空容器提示页（不白屏、不假渲染）。
 */
import { Component, lazy, Suspense, useMemo } from "react";
import type { ComponentType, ErrorInfo, LazyExoticComponent, ReactNode } from "react";

import type { MultitabPage } from "./MultitabFrame";
import { canResolveLoader, resolveLoader } from "./ModuleRegistry";
import { normalizeTabs, resolveTabPage, type DeclaredTab, type TabPageMode } from "./moduleGraph";
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

/** 薄壳语义页：三种「没有可渲染的 App」的原因都说清楚，不留白屏。 */
const SHELL_TEXT: Record<"backend" | "unknown-module" | "missing-app", { title: string; text: (detail?: string) => string }> = {
  backend: {
    title: "已接入（后端服务）",
    text: () => "该模块已接入：提供后端 API 服务，无独立窗口界面。",
  },
  "unknown-module": {
    title: "该页指向的模块未注册",
    text: (detail) =>
      `页签指向模块「${detail ?? "?"}」，但当前注册清单里没有它。` +
      "清单补齐后本页会自动变成真实界面。",
  },
  "missing-app": {
    title: "该页的入口文件缺失",
    text: (detail) =>
      `模块声明的入口「${detail ?? "?"}」在仓库里找不到对应 App 文件` +
      "（约定路径 `src/apps/<目录>/index.tsx`）。",
  },
};

export function BackendOnlyPage({
  label,
  mode = "backend",
  detail,
}: {
  label: string;
  /** 薄壳原因；缺省 = 「后端服务」语义（向后兼容旧调用）。 */
  mode?: "backend" | "unknown-module" | "missing-app";
  /** 补充说明（模块 id / 入口说明符）。 */
  detail?: string;
}) {
  const shell = SHELL_TEXT[mode];
  return (
    <div
      className="win__placeholder"
      role="status"
      data-testid={`system-backend-page-${label}`}
      data-page-reason={mode}
    >
      <div className="win__placeholder-icon" aria-hidden="true">
        ⚙
      </div>
      <div className="win__placeholder-title">{shell.title}</div>
      <div className="win__placeholder-text">{shell.text(detail)}</div>
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
    const tabs = normalizeTabs(declared);
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
      const page = resolveTabPage(tab, modules);

      // ① 可加载：模块存在、有 entry、且 App 文件真的在仓库里
      if (page.mode === "app" && page.appEntry) {
        if (!canResolveLoader(page.appEntry)) {
          // 模块写了 entry 但文件不存在 —— 预检拦下，别让用户看到「加载失败」这种含糊话
          return {
            key: tab.key,
            label: tab.label,
            content: <BackendOnlyPage label={tab.key} mode="missing-app" detail={page.appEntry} />,
          };
        }
        const App = lazyPage(page.moduleId, page.appEntry);
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
      }

      // ② 薄壳：无 entry / 纯 API / 模块未注册 —— 三种原因分别说清楚
      const shellMode: Extract<TabPageMode, "backend" | "unknown-module"> =
        page.mode === "unknown-module" ? "unknown-module" : "backend";
      return {
        key: tab.key,
        label: tab.label,
        content: <BackendOnlyPage label={tab.key} mode={shellMode} detail={page.moduleId} />,
      };
    });
  }, [containerId, declared, modules]);
}
