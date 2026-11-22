/**
 * 应用底座（P2 样板）—— 2026-11-22 · 凝析
 *
 * 为什么要有这一层：31 个 app 各写一套根容器/工具条/错误态，结果是 239 个原生 button、
 * 7 处 confirm/alert、每 app 一套 CSS。这里先立「最小公共语义」，**不改任何业务逻辑**：
 *   - AppShell：一个应用的一屏根容器（纵向 flex、可滚动主体、统一 padding/gap）；
 *   - AppToolbar：工具条（左：视图切换/筛选；右：动作），窄屏自动换行；
 *   - AppErrorState / AppLoading / AppEmpty：三种状态各只有一种说法（失败≠空≠加载）。
 *
 * 样式只吃设计令牌（--sp-* / --radius / --line / --txt…），不写死颜色；
 * 应用自己的皮肤仍由各自 CSS 决定（传 className 即可）。
 */
import type { ReactNode } from "react";

import { Button } from "@/shared/components/Button";

import "./app-shell.css";

interface ShellProps {
  children: ReactNode;
  /** 应用自己的根 class（保留旧皮肤，例如 todo-root）。 */
  className?: string;
  /** 测试/定位用；默认不加 data-testid，避免污染既有断言。 */
  testId?: string;
}

export function AppShell({ children, className = "", testId }: ShellProps) {
  return (
    <div className={`app-shell ${className}`.trim()} data-testid={testId}>
      {children}
    </div>
  );
}

interface ToolbarProps {
  left: ReactNode;
  right?: ReactNode;
  className?: string;
}

export function AppToolbar({ left, right, className = "" }: ToolbarProps) {
  return (
    <div className={`app-toolbar ${className}`.trim()} role="toolbar">
      <div className="app-toolbar__left">{left}</div>
      {right ? <div className="app-toolbar__right">{right}</div> : null}
    </div>
  );
}

interface ErrorProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  /** 旧皮肤 class（例如 todo-error）——不改各 app 现有样式。 */
  className?: string;
}

export function AppErrorState({ error, onRetry, title = "加载失败", className = "" }: ErrorProps) {
  const message = error instanceof Error ? error.message : "请求失败，请稍后重试";
  return (
    <div className={`app-state app-state--error ${className}`.trim()} role="alert">
      <div className="app-state__title">{title}</div>
      <div className="app-state__msg">{message}</div>
      {onRetry ? (
        <Button variant="primary" className="app-state__retry" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  );
}

export function AppLoading({ text = "读取中…" }: { text?: string }) {
  return (
    <div className="app-state app-state--loading" role="status" aria-live="polite">
      <span className="app-state__spinner" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}

export function AppEmpty({ text, hint }: { text: string; hint?: string }) {
  return (
    <div className="app-state app-state--empty">
      <div className="app-state__title">{text}</div>
      {hint ? <div className="app-state__msg">{hint}</div> : null}
    </div>
  );
}
