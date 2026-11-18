/**
 * 内核默认图标集（Lucide，按语义名裁剪）
 *
 * ★ 为什么是「语义名 → 图形」而不是直接用库内名：
 *   调研（demo重构/开源调研-皮肤与图标-20261118/03-…）实测 16 个真名在四个图标库的命中率
 *   只有 11–13/16，且缺的不是同一批（phosphor 把 gear 叫 settings、search 叫 magnifying-glass）。
 *   所以 manifest.icon 必须保持内核语义名，映射放在这一层 —— 换整套图标时 manifest 零改动。
 *
 * ★ 数据来源：Iconify 的 lucide 集合（https://api.iconify.design/lucide.json），只裁用到语义名，
 *   共 35 个。体积约 3KB（远小于整包 lucide 的 33KB / 字体 3.4MB）。
 *
 * ★ 许可证（必须保留）：
 *   - Lucide 本体：ISC License，Copyright (c) Lucide Contributors
 *   - 其中 134 个图标源自 Feather（https://feathericons.com）：MIT License，
 *     Copyright (c) 2013-2023 Cole Bemis
 *   两者均与本项目 Apache-2.0 兼容，保留本声明即可。
 *
 * ★ 扩展方式：第三方/皮肤插件用 registerIconSet() 注册自己的整套图标，
 *   同名语义键会被覆盖（整包替换），未覆盖的回落到本默认集。
 */
import type { ReactElement } from "react";

export const DEFAULT_ICON_SET: Record<string, ReactElement> = {
  bot: (
    <>
      <path d="M12 8V4H8" />
      <rect width={16} height={12} x={4} y={8} rx={2} />
      <path d="M2 14h2m16 0h2m-7-1v2m-6-2v2" />
    </>
  ),
  palette: (
    <>
      <path d="M12 22a1 1 0 0 1 0-20a10 9 0 0 1 10 9a5 5 0 0 1-5 5h-2.25a1.75 1.75 0 0 0-1.4 2.8l.3.4a1.75 1.75 0 0 1-1.4 2.8z" />
      <circle cx={"13.5"} cy={"6.5"} r={".5"} fill="currentColor" />
      <circle cx={"17.5"} cy={"10.5"} r={".5"} fill="currentColor" />
      <circle cx={"6.5"} cy={"12.5"} r={".5"} fill="currentColor" />
      <circle cx={"8.5"} cy={"7.5"} r={".5"} fill="currentColor" />
    </>
  ),
  shield: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"
      />
    </>
  ),
  box: (
    <>
      <path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="m3.3 7l8.7 5l8.7-5M12 22V12" />
    </>
  ),
  api: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M12 22v-5m3-9V2m2 6a1 1 0 0 1 1 1v4a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1zM9 8V2"
      />
    </>
  ),
  download: (
    <>
      <path d="M12 15V3m9 12v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10l5 5l5-5" />
    </>
  ),
  compass: (
    <>
      <circle cx={12} cy={12} r={10} />
      <path d="m16.24 7.76l-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z" />
    </>
  ),
  "book-open": (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M12 5v16m8.001-2A2 2 0 0 0 22 17V5a2 2 0 0 0-1.999-2L16 3.002A5 5 0 0 0 12 5a5 5 0 0 0-4-2H4a2 2 0 0 0-2 2v12a2 2 0 0 0 1.999 2H8a5 5 0 0 1 4 2a5 5 0 0 1 4-2z"
      />
    </>
  ),
  archive: (
    <>
      <rect width={20} height={5} x={2} y={3} rx={1} />
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8m-10 4h4" />
    </>
  ),
  bell: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M10.268 21a2 2 0 0 0 3.464 0m-10.47-5.674A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"
      />
    </>
  ),
  search: (
    <>
      <path d="m21 21l-4.34-4.34" />
      <circle cx={11} cy={11} r={8} />
    </>
  ),
  dashboard: (
    <>
      <rect width={7} height={9} x={3} y={3} rx={1} />
      <rect width={7} height={5} x={14} y={3} rx={1} />
      <rect width={7} height={9} x={14} y={12} rx={1} />
      <rect width={7} height={5} x={3} y={16} rx={1} />
    </>
  ),
  gear: (
    <>
      <path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0a2.34 2.34 0 0 0 3.319 1.915a2.34 2.34 0 0 1 2.33 4.033a2.34 2.34 0 0 0 0 3.831a2.34 2.34 0 0 1-2.33 4.033a2.34 2.34 0 0 0-3.319 1.915a2.34 2.34 0 0 1-4.659 0a2.34 2.34 0 0 0-3.32-1.915a2.34 2.34 0 0 1-2.33-4.033a2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915" />
      <circle cx={12} cy={12} r={3} />
    </>
  ),
  calendar: (
    <>
      <path d="M8 2v3m8-3v3" />
      <rect width={18} height={18} x={3} y={3} rx={2} />
      <path d="M3 9h18" />
    </>
  ),
  note: (
    <>
      <path d="M13.4 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7.4M2 6h4m-4 4h4m-4 4h4m-4 4h4" />
      <path d="M21.378 5.626a1 1 0 1 0-3.004-3.004l-5.01 5.012a2 2 0 0 0-.506.854l-.837 2.87a.5.5 0 0 0 .62.62l2.87-.837a2 2 0 0 0 .854-.506z" />
    </>
  ),
  course: (
    <>
      <path d="M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0zM22 10v6" />
      <path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5" />
    </>
  ),
  finance: (
    <>
      <path d="M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1" />
      <path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4" />
    </>
  ),
  health: (
    <>
      <path d="M2 9.5a5.5 5.5 0 0 1 9.591-3.676a.56.56 0 0 0 .818 0A5.49 5.49 0 0 1 22 9.5c0 2.29-1.5 4-3 5.5l-5.492 5.313a2 2 0 0 1-3 .019L5 15c-1.5-1.5-3-3.2-3-5.5" />
      <path d="M3.22 13H9.5l.5-1l2 4.5l2-7l1.5 3.5h5.27" />
    </>
  ),
  habit: (
    <>
      <path d="m17 2l4 4l-4 4" />
      <path d="M3 11v-1a4 4 0 0 1 4-4h14M7 22l-4-4l4-4" />
      <path d="M21 13v1a4 4 0 0 1-4 4H3" />
    </>
  ),
  review: (
    <>
      <rect width={8} height={4} x={8} y={2} rx={1} ry={1} />
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <path d="m9 14l2 2l4-4" />
    </>
  ),
  pack: (
    <>
      <path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73zm1 .27V12" />
      <path d="M3.29 7L12 12l8.71-5M7.5 4.27l9 5.15" />
    </>
  ),
  plugin: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M15.39 4.39a1 1 0 0 0 1.68-.474a2.5 2.5 0 1 1 3.014 3.015a1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474a2.5 2.5 0 1 0-3.014 3.015a1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474a2.5 2.5 0 1 1-3.014-3.015a1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474a2.5 2.5 0 1 0 3.014-3.015a1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z"
      />
    </>
  ),
  push: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11zm7.318-19.539l-10.94 10.939"
      />
    </>
  ),
  web: (
    <>
      <circle cx={12} cy={12} r={10} />
      <path d="M12 2a14.5 14.5 0 0 0 0 20a14.5 14.5 0 0 0 0-20M2 12h20" />
    </>
  ),
  ai: (
    <>
      <path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594zM20 2v4m2-2h-4" />
      <circle cx={4} cy={20} r={2} />
    </>
  ),
  chat: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M22 17a2 2 0 0 1-2 2H6.828a2 2 0 0 0-1.414.586l-2.202 2.202A.71.71 0 0 1 2 21.286V5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2z"
      />
    </>
  ),
  system: (
    <>
      <path d="M12 20v2m0-20v2m5 16v2m0-20v2M2 12h2m-2 5h2M2 7h2m16 5h2m-2 5h2M20 7h2M7 20v2M7 2v2" />
      <rect width={16} height={16} x={4} y={4} rx={2} />
      <rect width={8} height={8} x={8} y={8} rx={1} />
    </>
  ),
  docs: (
    <>
      <path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z" />
      <path d="M14 2v5a1 1 0 0 0 1 1h5M10 9H8m8 4H8m8 4H8" />
    </>
  ),
  export: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M12 3v12m5-7l-5-5l-5 5m14 7v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"
      />
    </>
  ),
  knowledge: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="m16 6l4 14M12 6v14M8 8v12M4 4v16"
      />
    </>
  ),
  schedule: (
    <>
      <path d="M16 14v2.2l1.6 1M16 2v3m5 2.338V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h2.338M3 9h5.859M8 2v3" />
      <circle cx={16} cy={16} r={6} />
    </>
  ),
  growth: (
    <>
      <path d="M16 7h6v6" />
      <path d="m22 7l-8.5 8.5l-5-5L2 17" />
    </>
  ),
  persona: (
    <>
      <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
      <circle cx={12} cy={7} r={4} />
    </>
  ),
  sidebar: (
    <>
      <rect width={18} height={18} x={3} y={3} rx={2} />
      <path d="M9 3v18" />
    </>
  ),
  summary: (
    <>
      <rect width={7} height={7} x={3} y={3} rx={1} />
      <rect width={7} height={7} x={14} y={3} rx={1} />
      <rect width={7} height={7} x={14} y={14} rx={1} />
      <rect width={7} height={7} x={3} y={14} rx={1} />
    </>
  ),

  star: (
    <>
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
        d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.12 2.12 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.12 2.12 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.12 2.12 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.12 2.12 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.12 2.12 0 0 0 1.597-1.16z"
      />
    </>
  ),

  rss: (
    <>
      <path d="M4 11a9 9 0 0 1 9 9M4 4a16 16 0 0 1 16 16" />
      <circle cx={5} cy={19} r={1} />
    </>
  ),
};

/** 语义名 → 库内名（供审计与文档用；渲染走 DEFAULT_ICON_SET） */
export const DEFAULT_ICON_LIBRARY_MAP: Record<string, string> = {
  star: "star",
  rss: "rss",
  bot: "bot",
  palette: "palette",
  shield: "shield",
  box: "box",
  api: "plug",
  download: "download",
  compass: "compass",
  "book-open": "book-open",
  archive: "archive",
  bell: "bell",
  search: "search",
  dashboard: "layout-dashboard",
  gear: "settings",
  calendar: "calendar",
  note: "notebook-pen",
  course: "graduation-cap",
  finance: "wallet",
  health: "heart-pulse",
  habit: "repeat",
  review: "clipboard-check",
  pack: "package",
  plugin: "puzzle",
  push: "send",
  web: "globe",
  ai: "sparkles",
  chat: "message-square",
  system: "cpu",
  docs: "file-text",
  export: "upload",
  knowledge: "library",
  schedule: "calendar-clock",
  growth: "trending-up",
  persona: "user",
  sidebar: "panel-left",
  summary: "layout-grid",
};

/** 默认集合（以 IconSet 形态暴露，供 registerIconSet / 审计用） */
export const DEFAULT_ICON_SET_ENTRY = {
  id: "lucide",
  name: "Lucide（内核默认）",
  license: "ISC (Lucide Contributors) + MIT (Feather, Cole Bemis)",
  icons: DEFAULT_ICON_SET,
} as const;
