/**
 * 图标注册表 —— 语义名 → 图形 的唯一解析入口。
 *
 * 解析顺序（每一层都可被插件整体替换）：
 *   1. 已注册的集合（后注册的优先，同名键覆盖 = 整包换图标）
 *   2. 默认 Lucide 集（DEFAULT_ICON_SET）
 *   3. 单字兜底（manifest.icon 里合法存在「课」「记」「程」这种中文单字）
 *   4. 模块名首字（调用方传 fallback 时）
 *
 * ★ 为什么要有 registerIconSet：调研里「换 prefix 换不动整套图标」是硬结论
 *   （16 个真名在四个库命中率 11–13/16，缺的不是同一批）。唯一可行的整包替换形态
 *   是「内核语义名不动，换掉名字到图形的映射表」——这个函数就是那张表的插口。
 */
import type { ReactElement } from "react";

import { DEFAULT_ICON_SET } from "./default-set";
import type { IconSet } from "./types";

/** 已注册集合（栈：后注册的优先；unregister 时弹出） */
const stack: IconSet[] = [];

/** 注册一整套图标。返回注销函数（插件卸载时调用，恢复上一套）。 */
export function registerIconSet(set: IconSet): () => void {
  stack.push(set);
  return () => {
    const i = stack.lastIndexOf(set);
    if (i >= 0) stack.splice(i, 1);
  };
}

/** 当前生效的集合 id 列表（调试/审计用，从底到顶）。 */
export function listIconSets(): string[] {
  return ["lucide", ...stack.map((s) => s.id)];
}

/** 测试用：清空所有第三方集合。 */
export function _resetIconSets(): void {
  stack.length = 0;
}

/** 语义名规范化：把历史/别名写法收敛到内核语义名。 */
const ALIASES: Record<string, string> = {
  // manifest 里用过 globe，语义名统一叫 web（同一个 Lucide globe 图形）
  globe: "web",
};

/** 解析语义名 → 图形；未知名返回 undefined（由调用方决定兜底）。 */
export function resolveIcon(name: string | undefined | null): ReactElement | undefined {
  if (!name) return undefined;
  const key = ALIASES[name] ?? name;
  for (let i = stack.length - 1; i >= 0; i -= 1) {
    const hit = stack[i].icons[key] ?? stack[i].icons[name];
    if (hit) return hit;
  }
  return DEFAULT_ICON_SET[key] ?? DEFAULT_ICON_SET[name];
}

/** 是否是「单字」兜底（中文/日文/韩文单字，或任何单字符）。 */
export function isGlyphFallback(name: string): boolean {
  return Array.from(name).length === 1;
}
