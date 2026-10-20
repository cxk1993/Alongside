/**
 * 推送插件主界面：浏览器订阅管理 + 手动广播 + 投递流水。
 *
 * 三层职责（与后端 modules/push/router.py 逐条对齐）：
 *   ① 订阅：Notification 权限 → /vapid-public-key → pushManager.subscribe → POST /subscribe
 *   ② 广播：POST /send（验收/排障用，同时验证 SW 的 push 事件链路）
 *   ③ 观测：/health（配置态）+ /subscriptions + /logs
 *
 * ★ 边界处理（不静默失败）：浏览器不支持 Push / 权限被拒 / VAPID 未配置 /
 *   SW 未注册 / 后端 4xx —— 每种都给明确文案与下一步动作。
 */
import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Tabs } from "@/shared/components/Tabs";

import { pushApi, type PushSubscriptionRow } from "./api";

/** VAPID 公钥 base64url → Uint8Array（PushManager 只接受 BufferSource）。 */
export function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const normalized = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(normalized);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** 浏览器是否具备 Web Push 三件套（serviceWorker + PushManager + Notification）。 */
export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** 通知权限态（不支持时返回 "unsupported"）。 */
export function notificationState(): NotificationPermission | "unsupported" {
  if (!pushSupported()) return "unsupported";
  return Notification.permission;
}

function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

export default function PushApp() {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [perm, setPerm] = useState<NotificationPermission | "unsupported">(notificationState);
  const [swReady, setSwReady] = useState(false);
  const [browserSub, setBrowserSub] = useState<string | null>(null);

  const health = useQuery({ queryKey: ["push", "health"], queryFn: pushApi.health });
  const subs = useQuery({ queryKey: ["push", "subs"], queryFn: pushApi.subscriptions });
  const logs = useQuery({ queryKey: ["push", "logs"], queryFn: () => pushApi.logs(30) });

  const [title, setTitle] = useState("Alongside 测试推送");
  const [body, setBody] = useState("这是一条来自推送插件的验收广播。");

  /** 读一次浏览器侧真实订阅态（SW ready + pushManager.getSubscription）。 */
  const syncBrowserState = useCallback(async () => {
    if (!pushSupported()) return;
    try {
      const reg = await navigator.serviceWorker.ready;
      setSwReady(true);
      const sub = await reg.pushManager.getSubscription();
      setBrowserSub(sub ? sub.endpoint : null);
    } catch {
      setSwReady(false);
      setBrowserSub(null);
    }
  }, []);

  useEffect(() => {
    void syncBrowserState();
    setPerm(notificationState());
  }, [syncBrowserState]);

  const refreshAll = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["push"] });
    void syncBrowserState();
  }, [qc, syncBrowserState]);

  const subscribeMut = useMutation({
    mutationFn: async () => {
      if (!pushSupported())
        throw new Error("当前浏览器不支持 Web Push（需要 serviceWorker + PushManager）");
      const permission = await Notification.requestPermission();
      setPerm(permission);
      if (permission !== "granted") {
        throw new Error("通知权限未授予——请在浏览器地址栏左侧的站点设置里允许通知，再重试");
      }
      const { public_key: publicKey } = await pushApi.vapidPublicKey();
      const reg = await navigator.serviceWorker.ready;
      const existing = await reg.pushManager.getSubscription();
      const sub =
        existing ??
        (await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(publicKey),
        }));
      const json = sub.toJSON();
      await pushApi.subscribe({
        endpoint: sub.endpoint,
        keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
        user_agent: navigator.userAgent,
      });
      return sub.endpoint;
    },
    onSuccess: (endpoint) => {
      setErr(null);
      setMsg(`✅ 本机已登记订阅：${endpoint.slice(0, 48)}…`);
      refreshAll();
    },
    onError: (e) => {
      setMsg(null);
      setErr(errText(e));
    },
  });

  const unsubscribeMut = useMutation({
    mutationFn: async () => {
      if (!pushSupported()) throw new Error("当前浏览器不支持 Web Push");
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (!sub) return false;
      await pushApi.unsubscribe(sub.endpoint);
      await sub.unsubscribe();
      return true;
    },
    onSuccess: (did) => {
      setErr(null);
      setMsg(did ? "已注销本机订阅（服务端与浏览器双侧解除）" : "本机本来就没有订阅");
      refreshAll();
    },
    onError: (e) => {
      setMsg(null);
      setErr(errText(e));
    },
  });

  const sendMut = useMutation({
    mutationFn: () => pushApi.send({ title, body, url: "/", tag: "alongside-manual" }),
    onSuccess: (r) => {
      setErr(null);
      setMsg(
        `广播结果：成功 ${r.sent} 条${r.pruned ? `，清理失效 ${r.pruned} 条` : ""}${
          r.detail ? `（${r.detail}）` : ""
        }`,
      );
      refreshAll();
    },
    onError: (e) => {
      setMsg(null);
      setErr(errText(e));
    },
  });

  const h = health.data;
  const busy = subscribeMut.isPending || unsubscribeMut.isPending || sendMut.isPending;

  // ★ 窗口内多页签（主人 2026-09-23 建议：太密太挤 → 分页）
  const [tab, setTab] = useState<"status" | "broadcast" | "devices" | "logs">("status");
  const TABS: { id: typeof tab; label: string }[] = [
    { id: "status", label: "状态" },
    { id: "broadcast", label: "广播" },
    { id: "devices", label: "设备" },
    { id: "logs", label: "流水" },
  ];

  return (
    <div className="push-root">
      {msg && <p className="push-ok">{msg}</p>}
      {err && <p className="push-err">✖ {err}</p>}

      <Tabs
        tabs={TABS.map((t) => ({
          id: t.id,
          label:
            t.id === "devices" && subs.data?.length ? (
              <>
                {t.label} <span className="push-tab__count">{subs.data.length}</span>
              </>
            ) : (
              t.label
            ),
        }))}
        active={tab}
        onChange={(id) => setTab(id as typeof tab)}
      />

      {tab === "status" && (
        <>
          {/* ── ① 配置态 ── */}
          <section className="push-card">
            <header className="push-card__head">
              <h3>推送通道</h3>
              <button className="push-btn push-btn--ghost" onClick={refreshAll} disabled={busy}>
                刷新
              </button>
            </header>
            <div className="push-flags">
              <Flag ok={!!h?.vapid_ready} label="VAPID 密钥" hint="服务器 .env 的 PUSH_VAPID_*" />
              <Flag ok={!!h?.pywebpush_installed} label="pywebpush 依赖" hint="发送端库" />
              <Flag ok={swReady} label="Service Worker" hint="sw.js 已接管本页" />
              <Flag
                ok={perm === "granted"}
                label={`通知权限：${perm === "unsupported" ? "不支持" : perm}`}
                hint="浏览器站点设置"
              />
            </div>
            <p className="push-meta">
              服务端活跃订阅 <b>{h?.subscriptions_active ?? "—"}</b> 条 · 本机{" "}
              {browserSub ? "已订阅" : "未订阅"}
            </p>
            {health.isError && <p className="push-warn">健康检查失败：{errText(health.error)}</p>}
          </section>

          {/* ── ② 订阅操作 ── */}
          <section className="push-card">
            <header className="push-card__head">
              <h3>本机订阅</h3>
            </header>
            <div className="push-actions">
              <button
                className="push-btn"
                onClick={() => subscribeMut.mutate()}
                disabled={busy || !pushSupported()}
              >
                开启浏览器推送
              </button>
              <button
                className="push-btn push-btn--ghost"
                onClick={() => unsubscribeMut.mutate()}
                disabled={busy || !pushSupported()}
              >
                关闭并注销
              </button>
            </div>
            {!pushSupported() && (
              <p className="push-warn">
                当前浏览器不支持 Web Push。桌面通知仍可走桥（日历提醒链路），本插件负责浏览器/PWA 通道。
              </p>
            )}
            {h && !h.vapid_ready && (
              <p className="push-warn">
                服务端未配置 VAPID 密钥：订阅会失败。请在部署的 `.env` 里补 `PUSH_VAPID_PUBLIC_KEY` /
                `PUSH_VAPID_PRIVATE_KEY` 后重启后端。
              </p>
            )}
          </section>
        </>
      )}

      {tab === "broadcast" && (
        <>
          {/* ── ③ 手动广播（验收用）── */}
          <section className="push-card">
            <header className="push-card__head">
              <h3>手动广播</h3>
              <span className="push-meta">验收/排障用；也验证 SW 的 push 事件链路</span>
            </header>
            <div className="push-form">
              <input
                className="push-input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="通知标题"
                maxLength={200}
              />
              <input
                className="push-input"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                placeholder="通知正文"
                maxLength={2000}
              />
              <button
                className="push-btn"
                onClick={() => sendMut.mutate()}
                disabled={busy || !title.trim()}
              >
                发送测试推送
              </button>
            </div>
          </section>
        </>
      )}

      {tab === "devices" && (
        <>
          {/* ── ④ 订阅清单 ── */}
          <section className="push-card push-card--grow">
            <header className="push-card__head">
              <h3>订阅设备</h3>
              <span className="push-meta">{subs.data?.length ?? 0} 条</span>
            </header>
            <div className="push-list">
              {(subs.data ?? []).map((s) => (
                <SubRow key={s.id} row={s} />
              ))}
              {subs.isSuccess && (subs.data ?? []).length === 0 && (
                <p className="push-empty">还没有任何订阅——点「状态」页签开启浏览器推送。</p>
              )}
              {subs.isError && <p className="push-warn">{errText(subs.error)}</p>}
            </div>
          </section>
        </>
      )}

      {tab === "logs" && (
        <>
          {/* ── ⑤ 投递流水 ── */}
          <section className="push-card push-card--grow">
            <header className="push-card__head">
              <h3>投递流水</h3>
              <span className="push-meta">最近 30 条</span>
            </header>
            <div className="push-list">
              {(logs.data ?? []).map((l) => (
                <div key={l.id} className="push-logrow">
                  <span className={l.ok ? "push-dot push-dot--ok" : "push-dot push-dot--bad"} />
                  <span className="push-logrow__title">{l.title}</span>
                  <span className="push-meta">
                    {l.topic} · {l.sent_at ? new Date(l.sent_at).toLocaleString() : "—"}
                    {l.detail ? ` · ${l.detail}` : ""}
                  </span>
                </div>
              ))}
              {logs.isSuccess && (logs.data ?? []).length === 0 && (
                <p className="push-empty">暂无投递记录。</p>
              )}
              {logs.isError && <p className="push-warn">{errText(logs.error)}</p>}
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Flag({ ok, label, hint }: { ok: boolean; label: string; hint: string }) {
  return (
    <span className={ok ? "push-flag push-flag--ok" : "push-flag"} title={hint}>
      <span className="push-dot" /> {label}
    </span>
  );
}

function SubRow({ row }: { row: PushSubscriptionRow }) {
  return (
    <div className="push-subrow">
      <span className={row.active ? "push-dot push-dot--ok" : "push-dot push-dot--bad"} />
      <span className="push-subrow__ua">{row.user_agent ?? "未知设备"}</span>
      <span className="push-meta">
        {row.endpoint_prefix}… · 失败 {row.fail_count} 次
        {row.last_sent_at ? ` · 最近 ${new Date(row.last_sent_at).toLocaleString()}` : ""}
      </span>
    </div>
  );
}
