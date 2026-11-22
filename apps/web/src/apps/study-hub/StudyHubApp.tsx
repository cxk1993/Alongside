/**
 * 学习软件 · 聚合入口（2026-11-22）。
 *
 * 为什么要有它：mooc / ucampus / welearn / yuketang / zhidao / sdu-feeds / pi-agent
 * 各自是独立插件，入口散在命令面板里；学生用户想要的是「一个学习软件按钮」。
 * 本页只做三件事：**看见**（启用态/类型）、**打开**（有窗口的）、**同步**（纯 API 的）。
 *
 * 纪律：不聚合数据、不代存账号、不假装可用 —— 插件没启用就显示「启用」，
 * 同步失败就把服务端的错误原样说出来。
 */
import { useCallback, useMemo, useState } from "react";

import { api, ApiError } from "@/shared/api/client";
import { pluginHost } from "@/kernel/ModuleRegistry";
import { usePlugins } from "@/kernel/plugins/PluginContext";

import "./study-hub.css";

type Mode = "window" | "sync";

interface LearningApp {
  id: string;
  label: string;
  mode: Mode;
  /** 一句话说清它是什么 / 需要什么。 */
  hint: string;
  /** sync 类插件：同步按钮的文案。 */
  syncLabel?: string;
  /** 首次同步前要先补齐的内置配置（幂等）：目前只有 sdu-feeds 需要 seed 源。 */
  preSync?: string;
}

const LEARNING_APPS: LearningApp[] = [
  { id: "mooc", label: "中国大学MOOC", mode: "window", hint: "内嵌原站 · 登录状态由浏览器决定" },
  { id: "ucampus", label: "U校园", mode: "window", hint: "内嵌原站 · 登录状态由浏览器决定" },
  { id: "zhidao", label: "知到", mode: "window", hint: "内嵌原站 · 登录状态由浏览器决定" },
  { id: "welearn", label: "WE Learn", mode: "sync", hint: "只读同步 · 需配置 Cookie", syncLabel: "同步进度" },
  { id: "yuketang", label: "雨课堂", mode: "sync", hint: "只读同步 · 需配置 session", syncLabel: "同步公告" },
  {
    id: "sdu-feeds",
    label: "山大通知",
    mode: "sync",
    hint: "公开站点 · 无需登录",
    syncLabel: "抓取通知",
    preSync: "/api/v1/sdu-feeds/sources/seed",
  },
  { id: "pi-agent", label: "Pi 智能体", mode: "window", hint: "需自行安装 pi CLI（外部 agent）" },
];

/** ApiError 的 title 常是 "Error"/"Validation Error"，detail 才是服务端的人话。 */
function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.detail || e.title || `HTTP ${e.status}`;
  return e instanceof Error ? e.message : String(e);
}

interface CardState {
  busy: boolean;
  message: string | null;
  tone: "ok" | "warn";
}

export function StudyHubApp() {
  const { plugins, enable, refresh, loading } = usePlugins();
  const [cards, setCards] = useState<Record<string, CardState>>({});
  const byId = useMemo(() => new Map(plugins.map((p) => [p.id, p])), [plugins]);

  const setCard = useCallback((id: string, patch: Partial<CardState>) => {
    const empty: CardState = { busy: false, message: null, tone: "ok" };
    setCards((prev) => ({ ...prev, [id]: { ...empty, ...prev[id], ...patch } }));
  }, []);

  const openApp = useCallback(
    async (app: LearningApp) => {
      const plugin = byId.get(app.id);
      if (!plugin) {
        setCard(app.id, { message: "未注册：插件清单里没有它", tone: "warn" });
        return;
      }
      setCard(app.id, { busy: true, message: null });
      try {
        if (!plugin.enabled) {
          await enable(app.id);
          await refresh();
          setCard(app.id, { message: "已启用，再点一次打开", tone: "ok" });
          return;
        }
        pluginHost.open(app.id);
        setCard(app.id, { message: null });
      } catch (e) {
        setCard(app.id, { message: errorText(e), tone: "warn" });
      } finally {
        setCard(app.id, { busy: false });
      }
    },
    [byId, enable, refresh, setCard],
  );

  const syncApp = useCallback(
    async (app: LearningApp) => {
      const plugin = byId.get(app.id);
      if (!plugin) {
        setCard(app.id, { message: "未注册：插件清单里没有它", tone: "warn" });
        return;
      }
      setCard(app.id, { busy: true, message: null });
      try {
        if (!plugin.enabled) {
          await enable(app.id);
          await refresh();
        }
        if (app.preSync) await api.post(app.preSync, {});
        const res = await api.post<Record<string, unknown>>(`/api/v1/${app.id}/sync`, {});
        // 两种返回形态都如实汇总：顶层计数（welearn/yuketang）或多源 results（sdu-f feeds）。
        const results = Array.isArray((res as { results?: unknown }).results)
          ? ((res as { results: Array<Record<string, unknown>> }).results)
          : null;
        const summary = results
          ? (() => {
              const sum = (key: string) =>
                results.reduce((n, r) => n + (typeof r[key] === "number" ? (r[key] as number) : 0), 0);
              const failed = results.filter((r) => r.status && r.status !== "ok").length;
              return `源 ${results.length} · 新增 ${sum("new")} · 更新 ${sum("updated")}${failed ? ` · 失败 ${failed}` : ""}`;
            })()
          : ["created", "skipped", "updated", "fetched"]
              .filter((k) => typeof res?.[k] === "number")
              .map((k) => `${k} ${res[k]}`)
              .join(" · ");
        setCard(app.id, { message: summary || "已同步", tone: "ok" });
      } catch (e) {
        setCard(app.id, { message: errorText(e), tone: "warn" });
      } finally {
        setCard(app.id, { busy: false });
      }
    },
    [byId, enable, refresh, setCard],
  );

  return (
    <div className="study-hub">
      <header className="study-hub__head">
        <div>
          <h2 className="study-hub__title">学习软件</h2>
          <p className="study-hub__sub">
            {loading ? "读取插件清单…" : `${LEARNING_APPS.length} 个入口 · 已启用 ${LEARNING_APPS.filter((a) => byId.get(a.id)?.enabled).length} 个`}
          </p>
        </div>
        <button type="button" className="study-hub__manage" onClick={() => pluginHost.open("plugins")}>
          管理插件
        </button>
      </header>

      <div className="study-hub__grid">
        {LEARNING_APPS.map((app) => {
          const plugin = byId.get(app.id);
          const state = cards[app.id];
          const enabled = Boolean(plugin?.enabled);
          return (
            <article key={app.id} className="study-hub__card" data-testid={`study-card-${app.id}`}>
              <div className="study-hub__card-top">
                <span className={`study-hub__dot${enabled ? " is-on" : ""}`} aria-hidden="true" />
                <h3 className="study-hub__name">{plugin?.name ?? app.label}</h3>
                <span className="study-hub__mode">{app.mode === "window" ? "窗口" : "同步"}</span>
              </div>
              <p className="study-hub__hint">{app.hint}</p>
              <div className="study-hub__actions">
                <button
                  type="button"
                  className="study-hub__btn study-hub__btn--primary"
                  disabled={state?.busy || !plugin}
                  onClick={() => (app.mode === "window" ? openApp(app) : syncApp(app))}
                >
                  {state?.busy
                    ? "处理中…"
                    : !plugin
                      ? "未注册"
                      : !enabled
                        ? "启用"
                        : app.mode === "window"
                          ? "打开"
                          : (app.syncLabel ?? "同步")}
                </button>
                <span className={`study-hub__msg${state?.tone === "warn" ? " is-warn" : ""}`} role="status">
                  {state?.message ?? (enabled ? "已启用" : "未启用")}
                </span>
              </div>
            </article>
          );
        })}
      </div>

      <p className="study-hub__note">
        内嵌窗口只做互操作：不抓数据、不注入脚本；站点若拒绝嵌入，用窗口里的「在浏览器打开」。
        纯 API 插件的同步需要先在插件设置里填自己的 Cookie / session。
      </p>
    </div>
  );
}

export default StudyHubApp;