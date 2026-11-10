import type { ComponentType } from "react";

/**
 * 模块清单类型（对齐后端 `contracts/plugin.schema.json` 的 36 个顶层字段）。
 * 内核只认「一个模块」，绝不出现任何业务词汇。
 *
 * ★ 字段分三层（2026-11-09 补齐 · 此前 manifestToModule 把 36 字段截成 9 个）：
 *   A 必须有（运行时要读）：id/name/version/kind/icon/description/entry/window/
 *     api/slots/tabs/absorbs/provides
 *   B 应保留（强类型，供调试/未来消费）：requires/optionalDependencies/permissions/
 *     kernelApi/minKernel/defines/emits/consumes/lifecycle
 *   C 可透传（不解析，原样保留）：其余协议字段挂在模块顶层，并整体存进 `raw` 快照。
 *
 * 事实源是 schema，不是本文件的便捷子集 —— 后端加字段时这里不该静默丢。
 */

export type ModuleKind = "core" | "builtin" | "third-party" | "container";

export interface ModuleWindowSpec {
  w: number;
  h: number;
  minW?: number;
  minH?: number;
  /** singleton: true 的模块重复点击 = 聚焦，不重复开窗。 */
  singleton?: boolean;
  /** 默认位置（可选，不填则级联排布）。 */
  x?: number;
  y?: number;
}

/**
 * manifest.api —— 后端模块的路由面。
 * WindowFrame 靠 `api.base` 区分「已接入的后端服务」与「清单缺 entry 的真缺失」。
 */
export interface ModuleApiSpec {
  /** 路由前缀，必须等于 /api/v1/<id>。 */
  base: string;
  openapi?: string;
  health?: string;
  /** ISSUE-008 方案 A：resource → 真实路由段 的显式映射。 */
  tools?: Record<string, string>;
}

/** 容器型模块（kind=container）的页签声明（形状与 SystemApp 对接面一致）。 */
export interface ModuleTabSpec {
  /** 页签唯一键（容器内稳定标识）。 */
  key: string;
  /** 页签显示名。 */
  label: string;
  /** 该页关联的模块 id（懒加载对应 App）；省略 = 「仅服务」薄壳页。 */
  entry?: string;
}

/** 权限声明：旧数组格式（向后兼容）或新声明式对象格式。 */
export type PermissionDecl =
  | string[]
  | {
      filesystem?: boolean;
      network?: boolean;
      subprocess?: boolean;
    };

/** 四个生命周期钩子（脚本路径；null = 不挂）。 */
export interface PluginLifecycleHooks {
  onInstall?: string | null;
  onEnable?: string | null;
  onDisable?: string | null;
  onUninstall?: string | null;
}

/** B 组：前端暂不解析、但必须原样保留的协议字段。 */
export interface PluginProtocolFields {
  /** 需要别人提供的能力（<id>.<名词>.<动作>）；未声明的能力调用会被拒。 */
  requires?: string[];
  /** 软依赖：缺了不阻塞启动，只降级。 */
  optionalDependencies?: string[];
  permissions?: PermissionDecl;
  /** 要求的内核接口版本（如 ^1）。 */
  kernelApi?: string;
  /** 要求的最低内核版本（三段式）。 */
  minKernel?: string;
  /** 本插件声明所有权的能力（三角色之一）。 */
  defines?: string[];
  /** 本插件会发出的事件。 */
  emits?: string[];
  /** 本插件会监听的事件。 */
  consumes?: string[];
  lifecycle?: PluginLifecycleHooks;
}

export interface ModuleManifest extends PluginProtocolFields {
  id: string;
  name: string;
  version: string;
  kind: ModuleKind;
  icon?: string;
  description?: string;
  /** 动态 import 说明符，由内核 React.lazy 加载。 */
  entry: string;
  window: ModuleWindowSpec;
  /** 后端 API 路由面（kind=container 可省）。 */
  api?: ModuleApiSpec;
  /** 本插件要挂到的扩展点（只能从已知 15 个里挑）。 */
  slots?: string[];
  /** 容器页签；非容器模块通常无。 */
  tabs?: ModuleTabSpec[];
  /** 被本容器吸收、不再单独出现在底端栏的模块 id。 */
  absorbs?: string[];
  /** 本插件对外提供的能力（<id>.<名词>.<动作>）。 */
  provides?: string[];
  /** 原始 manifest 快照：未被显式解析的协议字段的唯一真相（调试/未来消费）。 */
  raw?: Record<string, unknown>;
  // 允许其余契约字段（provides/requires/slots/emits/permissions…）原样透传
  [key: string]: unknown;
}

/**
 * 模块入口默认导出的形状（与总纲插件协议一致）。
 * 内核用 React.lazy 加载，取其 Component 渲染到窗口内。
 *
 * slots 支持两种形态（E5 起）：
 *   - 简写：`"dashboard.card": DashboardCard`（等价于 { component }）
 *   - 规格：`"window.sidecar": { component: SidecarSummary, attachTo: "calendar" }`
 *     attachTo = 附着目标窗口 moduleId；缺省 = 所有窗口。
 */
export interface SlotContributionSpec {
  component: ComponentType;
  /** E5：sidecar 附着目标窗口 moduleId；缺省 = 所有窗口渲染。 */
  attachTo?: string;
}

export interface PluginModule {
  manifestId: string;
  Component: ComponentType;
  slots?: Record<string, ComponentType | SlotContributionSpec>;
}
