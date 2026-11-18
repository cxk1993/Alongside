/**
 * <Icon> —— 内核统一图标渲染点（同步、无副作用、无网络）。
 *
 * 用法：
 *   <Icon name={manifest.icon} />              // 语义名
 *   <Icon name={manifest.icon} fallback={id} />// 未知名 → 用 fallback 的首字
 *
 * 为什么不是异步组件：调研结论——Lucide 的 DynamicIcon 走 useState+useEffect，
 * 一屏 10+ 图标会出现「图标后到」，Dock 是首屏元素，必须首帧就位。
 */
import type { CSSProperties, ReactElement } from "react";

import { isGlyphFallback, resolveIcon } from "./registry";

export interface IconProps {
  /** 语义名（manifest.icon 的取值域见 default-set.tsx 的 DEFAULT_ICON_LIBRARY_MAP） */
  name?: string | null;
  /** 解析不到时的兜底文本来源（取首字）；不传则渲染占位点 */
  fallback?: string;
  /** 像素尺寸（默认 18，与 dock__icon 36px 容器、15px 字号的视觉重量对齐） */
  size?: number;
  className?: string;
  style?: CSSProperties;
  /** 装饰性图标默认 aria-hidden；有语义时传 true 让读屏读出 name */
  label?: string;
}

export function Icon({
  name,
  fallback,
  size = 18,
  className,
  style,
  label,
}: IconProps): ReactElement {
  const glyph = name ? resolveIcon(name) : undefined;

  if (glyph) {
    return (
      <svg
        viewBox="0 0 24 24"
        width={size}
        height={size}
        className={className}
        style={{ display: "inline-block", verticalAlign: "middle", flex: "none", ...style }}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        role={label ? "img" : undefined}
        aria-label={label}
        aria-hidden={label ? undefined : true}
        focusable="false"
        data-icon={name}
      >
        {glyph}
      </svg>
    );
  }

  // 兜底 1：单字（「课」「记」「程」这类合法单字，原样显示）
  // 兜底 2：fallback 的首字（模块 id/名字）
  // 兜底 3：· （与旧行为一致）
  const text = name && isGlyphFallback(name) ? name : fallback ? Array.from(fallback)[0] : "·";

  return (
    <span
      className={className}
      style={{ display: "inline-block", lineHeight: 1, ...style }}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      data-icon-fallback={name ?? ""}
    >
      {text}
    </span>
  );
}
