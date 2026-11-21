import type { ModuleReg, WorkspaceState } from "./store";

/**
 * ★ T17：命令面板（v1）—— 纯函数层。
 *
 * 设计约束（来自 01:35 的只读调研，见 `E:/ai work/ningxi/Alongside/日志/2026-11-22-0135-T17调研.md`）：
 *   1. **零新后端接口**：三类来源（模块 / 动作 / 能力）全部取自 store 既有数据。
 *   2. **能力条目只读**：`manifest.provides` 只是能力名（如 `calendar.event.read`），
 *      内核**没有**把它变成可调用端点的映射；v1 绝不"执行"，只跳到提供者模块。
 *      加个假「执行」按钮就是装深度 —— 这个项目从头到尾在防的就是假按钮。
 *   3. **不接 `command.palette` 槽**：`SlotContribution` 只有 `component`，没有
 *      「可执行 + 可检索」形状；要用就得改契约（云昔领地 + 冻结期）→ 留 v2。
 *
 * 这里只放纯函数，UI 在 `Desktop.tsx`。这样命令定义与检索规则可以被单测钉死，
 * 不依赖 React 渲染，也不会因为改样式把逻辑改坏。
 */

export type CommandGroup = "模块" | "动作" | "能力（只读）";

/** 展示顺序：模块（最常用）→ 动作 → 能力（只读，最后）。 */
export const COMMAND_GROUP_ORDER: readonly CommandGroup[] = ["模块", "动作", "能力（只读）"];

export interface Command {
  /** 全局唯一（同 id 在面板里不会出现两次）。 */
  id: string;
  /** 人读到的那一行。要能独立成立，不靠分组标题补全语义。 */
  title: string;
  group: CommandGroup;
  /** 额外检索词（中英/别名/所属模块名），标题本身也会被检索。 */
  keywords: string[];
  /** v1 保留位：置灰且不可执行。当前只有「动作不可用」会用（如无插件管理模块）。 */
  disabled?: boolean;
  run: () => void;
}

export interface CommandDeps {
  modules: Record<string, ModuleReg>;
  workspaces: WorkspaceState[];
  activeWorkspaceId: string;
  topbarCollapsed: boolean;
  dockCollapsed: boolean;
  /** 打开模块（Desktop 传 pluginHost.open）。 */
  openModule: (moduleId: string) => void;
  /** 有 `plugin.manage` 提供者时才有；没有就传 undefined（不渲染该命令）。 */
  openPluginManager?: () => void;
  toggleTopbar: () => void;
  toggleBottombar: () => void;
  tidyDesktop: () => void;
  minimizeAllUnpinned: () => void;
  createWorkspace: () => void;
  switchWorkspace: (workspaceId: string) => void;
  toggleTheme: () => void;
}

/**
 * 从 store 现有数据推导出全部命令。
 * 纯函数：同样的入参永远得到同样的列表（不读 DOM / localStorage / 时间）。
 */
export function buildCommands(d: CommandDeps): Command[] {
  const out: Command[] = [];

  // ── ① 模块：只列**已启用**的 ────────────────────────────────────
  // 停用模块也列出来会让人以为"点了能开"；一点没反应比没有这条更糟。
  // 想启用模块请走插件管理（面板里有那一条）。
  for (const reg of Object.values(d.modules)) {
    if (!reg.enabled) continue;
    const m = reg.manifest;
    out.push({
      id: `mod:${m.id}`,
      title: m.name,
      group: "模块",
      keywords: [m.id, "模块", "module", "打开"],
      run: () => d.openModule(m.id),
    });
  }

  // ── ② 动作：store 既有方法包一层，不发明状态 ────────────────────
  out.push(
    {
      id: "act:toggle-dock",
      // 标题写「Dock」而不是「底栏」：T17 之后罗盘「底栏」与 Dock 是同一个开关，
      // 但面板里要让人一眼看出这动的是那条模块坞（它是 Dock 隐藏后唯一的找回入口）。
      title: d.dockCollapsed ? "显示 Dock（模块坞）" : "隐藏 Dock（模块坞）",
      group: "动作",
      keywords: ["dock", "坞", "底栏", "bottombar", "显示", "隐藏"],
      run: d.toggleBottombar,
    },
    {
      id: "act:toggle-topbar",
      title: d.topbarCollapsed ? "展开顶栏" : "收起顶栏",
      group: "动作",
      keywords: ["topbar", "顶栏", "菜单栏"],
      run: d.toggleTopbar,
    },
    {
      id: "act:tidy",
      title: "整理桌面（窗口归位）",
      group: "动作",
      keywords: ["tidy", "排列", "归位", "整齐"],
      run: d.tidyDesktop,
    },
    {
      id: "act:minimize-unpinned",
      title: "最小化所有未固定窗口",
      group: "动作",
      keywords: ["minimize", "最小化", "收起窗口"],
      run: d.minimizeAllUnpinned,
    },
    {
      id: "act:new-workspace",
      title: "新建工作区",
      group: "动作",
      keywords: ["workspace", "工作区", "新建", "空间"],
      run: d.createWorkspace,
    },
    {
      id: "act:toggle-theme",
      title: "切换明暗主题",
      group: "动作",
      keywords: ["theme", "主题", "明暗", "dark", "light"],
      run: d.toggleTheme,
    },
  );
  // 工作区跳转：当前那个不列（列出来只能是置灰的死条目）。
  for (const w of d.workspaces) {
    if (w.id === d.activeWorkspaceId) continue;
    out.push({
      id: `ws:${w.id}`,
      title: `切到工作区「${w.name}」`,
      group: "动作",
      keywords: ["workspace", "工作区", "切换", w.name],
      run: () => d.switchWorkspace(w.id),
    });
  }
  if (d.openPluginManager) {
    out.push({
      id: "act:plugin-manager",
      title: "打开插件管理",
      group: "动作",
      keywords: ["plugin", "插件", "启用", "停用", "管理"],
      run: d.openPluginManager,
    });
  }
  // 注意：**故意不放「退出登录」**。命令面板是键盘流（Enter 即执行），
  // 把一个会话级不可逆动作塞进容易误触的位置不值得；退路由顶栏菜单保留。

  // ── ③ 能力（只读）：manifest.provides 原样透传，只做跳转 ─────────
  // 同名能力若被多个模块提供，只保留第一个（否则面板里出现两条一模一样的行，
  // 点哪条都一样，人只会怀疑哪个是真的）。
  const seenCaps = new Set<string>();
  for (const reg of Object.values(d.modules)) {
    if (!reg.enabled) continue;
    const caps = reg.manifest.provides ?? [];
    for (const cap of caps) {
      if (seenCaps.has(cap)) continue;
      seenCaps.add(cap);
      const m = reg.manifest;
      out.push({
        id: `cap:${cap}`,
        // 「只读」写进标题本身：分组标题会随滚动离开视野，标题不会。
        title: `能力 · ${cap}`,
        group: "能力（只读）",
        keywords: [cap, m.name, m.id, "能力", "capability"],
        run: () => d.openModule(m.id),
      });
    }
  }

  return out;
}

/** 空查询返回全部；否则对 title / id / keywords / group 做小写包含匹配。 */
export function filterCommands(list: readonly Command[], query: string): Command[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...list];
  return list.filter((c) => {
    if (c.title.toLowerCase().includes(q)) return true;
    if (c.id.toLowerCase().includes(q)) return true;
    if (c.group.toLowerCase().includes(q)) return true;
    return c.keywords.some((k) => k.toLowerCase().includes(q));
  });
}

export interface CommandGroupBlock {
  group: CommandGroup;
  commands: Command[];
}

/**
 * 按固定顺序分组；空组不出现（不要给人看一个只有标题的空分区）。
 * 组内保持入参顺序 —— 上层若想排序，排序应发生在 filter 之前，
 * 这样键盘上下移动与视觉顺序永远一致。
 */
export function groupCommands(list: readonly Command[]): CommandGroupBlock[] {
  const blocks: CommandGroupBlock[] = [];
  for (const group of COMMAND_GROUP_ORDER) {
    const commands = list.filter((c) => c.group === group);
    if (commands.length) blocks.push({ group, commands });
  }
  return blocks;
}

/** 键盘导航用：面板里实际可执行（非 disabled）的下标序列。 */
export function isRunnable(c: Command | undefined): c is Command {
  return Boolean(c) && !c?.disabled;
}
