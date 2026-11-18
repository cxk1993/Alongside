import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";

import { api } from "@/shared/api/client";
import { resolveLoader } from "../ModuleRegistry";
import { onPluginListRefresh } from "./pluginListSignal";
import {
  contributionsForSlot,
  registerContributions,
  unregisterContributions,
} from "../slots/contributions";
import { syncPluginsToStore } from "./PluginRegistry";
import { syncPluginSkins } from "@/shared/styles/skin-plugin-store";
import { resetSkin } from "@/shared/styles/skin-runtime";
import type { PluginContextValue, PluginInfo, SlotContribution, SlotName } from "./types";
import type { SlotContributionSpec } from "../types";

/**
 * 动态 import 插件入口：与窗口打开共用 ModuleRegistry.resolveLoader
 * （按 src/apps/<目录>/index.tsx 约定自动发现，不在内核写死插件名）。
 */
async function defaultLoadEntry(entry: string): Promise<{
  default: { slots?: Partial<Record<SlotName, ComponentType | SlotContributionSpec>> };
}> {
  return resolveLoader(entry)() as unknown as Promise<{
    default: { slots?: Partial<Record<SlotName, ComponentType | SlotContributionSpec>> };
  }>;
}

type ApiClient = typeof api;

export interface PluginProviderProps {
  children: ReactNode;
  /** 注入自定义 API 客户端（测试用）。 */
  client?: ApiClient;
  /** 注入自定义入口加载器（测试用）。 */
  loadEntry?: (entry: string) => Promise<{
    default: { slots?: Partial<Record<SlotName, ComponentType | SlotContributionSpec>> };
  }>;
}

/**
 * 插件上下文提供者（T14 · 前端总入口）。
 *
 * 挂载后：拉取 /api/v1/plugins → 同步进桌面 store（Dock 自动出现/置灰）→
 * 动态 import 各启用插件的入口收集插槽贡献。启用/禁用/安装/卸载都走后端 API，
 * 再刷新本地状态，全程不改内核业务代码（"一切皆插件"的落地）。
 *
 * 这是 T02 预留的集成缝：把 <PluginProvider> 包在桌面根组件外即可启用真实插件加载。
 */
export function PluginProvider({
  children,
  client = api,
  loadEntry = defaultLoadEntry,
}: PluginProviderProps) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const data = await client.get<{ plugins: PluginInfo[] }>("/api/v1/plugins");
      const list = data.plugins;
      setPlugins(list);
      syncPluginsToStore(list);
      // ★ 皮肤随插件来、随插件走（皮肤插件 v1）。
      //   传全量让 store 整组重建：卸载/停用插件的皮肤在这里被清掉，
      //   不会留下「来源已不存在」的孤儿条目。
      //   若正在用的正是这份已消失的皮肤，回退默认 —— 来源没了就不该继续生效。
      const skinSync = syncPluginSkins(list);
      if (skinSync.resetApplied) resetSkin();
      for (const p of list) {
        // ISSUE-012 防御：manifest 缺失（后端曾漏字段）→ 该项无入口可加载，跳过不崩。
        if (p.enabled && p.manifest?.entry) {
          try {
            const mod = await loadEntry(p.manifest.entry);
            const slots = mod.default?.slots;
            if (slots) {
              // E5：slots 支持两种形态 —— 简写（ComponentType）与规格（{ component, attachTo }）。
              const items: SlotContribution[] = (Object.keys(slots) as SlotName[]).map((slot) => {
                const spec = slots[slot];
                const isSpec =
                  typeof spec !== "function" && spec !== null && typeof spec === "object";
                return {
                  slot,
                  pluginId: p.id,
                  component: (isSpec ? spec.component : spec) as ComponentType,
                  attachTo: isSpec ? (spec as { attachTo?: string }).attachTo : undefined,
                };
              });
              registerContributions(p.id, items);
            }
          } catch (e) {
            // 入口缺失或加载失败：不影响内核，仅该插件无 UI 贡献。
            // 但**不许静默**——否则"插件界面没出来"会变成查不出的悬案（实测踩过一次）。
            console.error(`[plugins] 插件「${p.id}」的入口加载失败：`, e);
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [client, loadEntry]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ★ r5：外部安装/卸载（CLI、上传端点、另一标签页）也要能**不刷新页面**可见 ——
  //   订阅内核刷新信号（管理面变更后主动 poke；见 pluginListSignal）。
  useEffect(() => onPluginListRefresh(() => void refresh()), [refresh]);

  // ★ r5：回到页面时重取一次（带 30s 节流）——覆盖「在别处装好、切回来要看见」。
  //   廉价通道，绝不轮询：只有 visibilitychange 且距上次刷新 ≥30s 才发一次请求。
  useEffect(() => {
    let last = 0;
    const onVisible = () => {
      if (typeof document === "undefined" || document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - last < 30_000) return;
      last = now;
      void refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refresh]);

  const enable = useCallback(
    async (id: string) => {
      await client.post(`/api/v1/plugins/${id}/enable`);
      await refresh();
    },
    [client, refresh],
  );

  const disable = useCallback(
    async (id: string) => {
      await client.post(`/api/v1/plugins/${id}/disable`);
      await refresh();
    },
    [client, refresh],
  );

  const install = useCallback(
    async (id: string) => {
      await client.post("/api/v1/plugins/install", { id });
      await refresh();
    },
    [client, refresh],
  );

  const uninstall = useCallback(
    async (id: string) => {
      await client.post(`/api/v1/plugins/${id}/uninstall`);
      unregisterContributions(id);
      await refresh();
    },
    [client, refresh],
  );

  const getContributions = useCallback((slot: SlotName) => contributionsForSlot(slot), []);

  const value = useMemo<PluginContextValue>(
    () => ({
      plugins,
      loading,
      error,
      enable,
      disable,
      install,
      uninstall,
      refresh,
      getContributions,
    }),
    [plugins, loading, error, enable, disable, install, uninstall, refresh, getContributions],
  );

  return <PluginContext.Provider value={value}>{children}</PluginContext.Provider>;
}

export const PluginContext = createContext<PluginContextValue | null>(null);

/** 读取插件上下文（必须在 <PluginProvider> 内）。 */
export function usePlugins(): PluginContextValue {
  const ctx = useContext(PluginContext);
  if (!ctx) {
    throw new Error("usePlugins 必须在 <PluginProvider> 内使用");
  }
  return ctx;
}
