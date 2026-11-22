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
  /** 内嵌站点：默认「新标签打开」（站点常拒绝 iframe），窗口内打开作为次要选项。 */
  externalUrl?: string;
  /** 需要账号会话的插件：配置表单的字段。 */
  session?: { field: "cookie" | "sessionid"; label: string; placeholder: string; defaultBase?: string };
}

const LEARNING_APPS: LearningApp[] = [
  {
    id: "mooc",
    label: "中国大学MOOC",
    mode: "window",
    hint: "官网默认新标签打开（站点拒绝内嵌）",
    externalUrl: "https://www.icourse163.org/",
  },
  {
    id: "ucampus",
    label: "U校园",
    mode: "window",
    hint: "官网默认新标签打开（站点可能拒绝内嵌）",
    externalUrl: "https://u.unipus.cn/",
  },
  {
    id: "zhidao",
    label: "知到",
    mode: "window",
    hint: "官网默认新标签打开（站点可能拒绝内嵌）",
    externalUrl: "https://onlinestuh5.zhihuishu.com/",
  },
  {
    id: "welearn",
    label: "WE Learn",
    mode: "sync",
    hint: "只读同步 · 先配置 Cookie",
    syncLabel: "同步进度",
    session: {
      field: "cookie",
      label: "Cookie（含 .AspNet.Cookies 与 ASP.NET_SessionId）",
      placeholder: "从浏览器 DevTools → Cookies 复制整串",
    },
  },
  {
    id: "yuketang",
    label: "雨课堂",
    mode: "sync",
    hint: "只读同步 · 先配置 sessionid",
    syncLabel: "同步公告",
    session: {
      field: "sessionid",
      label: "sessionid",
      placeholder: "DevTools → Cookies 里的 sessionid",
      defaultBase: "https://changjiang.yuketang.cn",
    },
  },
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
  /** 会话配置表单（凭据只进 input 与 POST body，不写日志、不进卡片消息）。 */
  const [configs, setConfigs] = useState<Record<string, { open: boolean; cred: string; base: string }>>({});
  const byId = useMemo(() => new Map(plugins.map((p) => [p.id, p])), [plugins]);
  const cfgOf = (id: string) => configs[id] ?? { open: false, cred: "", base: "" };

  const setCard = useCallback((id: string, patch: Partial<CardState>) => {
    const empty: CardState = { busy: false, message: null, tone: "ok" };
    setCards((prev) => ({ ...prev, [id]: { ...empty, ...prev[id], ...patch } }));
  }, []);

  const openApp = useCallback(
    async (app: LearningApp, inWindow = false) => {
      const plugin = byId.get(app.id);
      if (!plugin) {
        setCard(app.id, { message: "未注册：插件清单里没有它", tone: "warn" });
        return;
      }
      // 内嵌站点默认新标签打开：icourse163 等会以「非法跨域请求」拒绝 iframe。
      if (app.externalUrl && !inWindow) {
        window.open(app.externalUrl, "_blank", "noopener,noreferrer");
        setCard(app.id, { message: "已在新标签打开", tone: "ok" });
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

  const saveSession = useCallback(
    async (app: LearningApp) => {
      if (!app.session) return;
      const cfg = cfgOf(app.id);
      const value = cfg.cred.trim();
      if (!value) {
        setCard(app.id, { message: "先粘贴凭据再保存", tone: "warn" });
        return;
      }
      setCard(app.id, { busy: true, message: null });
      try {
        const body: Record<string, string> =
          app.session.field === "cookie"
            ? { cookie: value, ...(cfg.base.trim() ? { base_url: cfg.base.trim() } : {}) }
            : { sessionid: value, ...(cfg.base.trim() ? { base_url: cfg.base.trim() } : {}) };
        const res = await api.post<Record<string, unknown>>(`/api/v1/${app.id}/session`, body);
        const name = typeof res?.first_course === "string" ? res.first_course : "";
        setCard(app.id, {
          message: `会话已保存${typeof res?.courses === "number" ? ` · 课程 ${res.courses}` : ""}${name ? ` · ${name}` : ""}`,
          tone: "ok",
        });
        // 保存成功即刻把凭据从内存表单里清掉（只留在服务端库）
        setConfigs((prev) => ({ ...prev, [app.id]: { open: false, cred: "", base: cfg.base } }));
      } catch (e) {
        setCard(app.id, { message: errorText(e), tone: "warn" });
      } finally {
        setCard(app.id, { busy: false });
      }
    },
    [cfgOf, setCard],
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
                          ? app.externalUrl
                            ? "打开官网"
                            : "打开"
                          : (app.syncLabel ?? "同步")}
                </button>
                {app.externalUrl ? (
                  <button
                    type="button"
                    className="study-hub__btn"
                    disabled={state?.busy}
                    onClick={() => openApp(app, true)}
                  >
                    窗口内
                  </button>
                ) : null}
                {app.session ? (
                  <button
                    type="button"
                    className="study-hub__btn"
                    onClick={() =>
                      setConfigs((prev) => ({ ...prev, [app.id]: { ...cfgOf(app.id), open: !cfgOf(app.id).open } }))
                    }
                  >
                    配置
                  </button>
                ) : null}
                <span className={`study-hub__msg${state?.tone === "warn" ? " is-warn" : ""}`} role="status">
                  {state?.message ?? (enabled ? "已启用" : "未启用")}
                </span>
              </div>

              {app.session && cfgOf(app.id).open ? (
                <form
                  className="study-hub__config"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void saveSession(app);
                  }}
                >
                  <label className="study-hub__config-label">
                    {app.session.label}
                    <input
                      type="password"
                      autoComplete="off"
                      className="study-hub__config-input"
                      placeholder={app.session.placeholder}
                      value={cfgOf(app.id).cred}
                      onChange={(e) =>
                        setConfigs((prev) => ({ ...prev, [app.id]: { ...cfgOf(app.id), cred: e.target.value } }))
                      }
                    />
                  </label>
                  <label className="study-hub__config-label">
                    站点（可留空）
                    <input
                      type="text"
                      className="study-hub__config-input"
                      placeholder={app.session.defaultBase ?? "默认官网"}
                      value={cfgOf(app.id).base}
                      onChange={(e) =>
                        setConfigs((prev) => ({ ...prev, [app.id]: { ...cfgOf(app.id), base: e.target.value } }))
                      }
                    />
                  </label>
                  <button type="submit" className="study-hub__btn study-hub__btn--primary" disabled={state?.busy}>
                    保存并校验
                  </button>
                </form>
              ) : null}
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